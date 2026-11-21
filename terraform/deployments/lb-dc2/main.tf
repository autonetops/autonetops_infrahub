# Applies the Infrahub f5-ltm-configuration artifact of one load balancer to
# that box, through the f5_ltm module.
#
#   terraform plan  -var-file=secret.tfvars                       # artifact from Infrahub
#   terraform plan  -var-file=secret.tfvars -var config_file=x.yml # a local render
#   terraform apply -var-file=secret.tfvars -var adopt_existing=true   # first run on a box
#                                                                       # that already has the objects

provider "bigip" {
  address                = var.bigip_address
  username               = var.bigip_username
  password               = var.bigip_password
  validate_certs_disable = true # lab box, self-signed management certificate
  teem_disable           = true # no usage telemetry to F5
}

# --- config source: a local file, or the artifact stored in Infrahub --------

data "http" "artifact_index" {
  count = var.config_file == null ? 1 : 0

  url    = "${var.infrahub_address}/graphql/${var.infrahub_branch}"
  method = "POST"
  request_headers = {
    Content-Type   = "application/json"
    X-INFRAHUB-KEY = var.infrahub_api_key
  }
  request_body = jsonencode({
    query = <<-GQL
      query { CoreArtifact(name__value: "f5-ltm-configuration") { edges { node {
        status { value } storage_id { value } checksum { value }
        object { node { display_label } }
      } } } }
    GQL
  })

  lifecycle {
    postcondition {
      condition     = self.status_code == 200 && try(jsondecode(self.response_body).errors, null) == null
      error_message = "Infrahub GraphQL lookup of the f5-ltm-configuration artifacts failed."
    }
  }
}

locals {
  artifact = var.config_file != null ? null : one([
    for e in jsondecode(data.http.artifact_index[0].response_body).data.CoreArtifact.edges : e.node
    if e.node.object.node.display_label == var.device
  ])
}

data "http" "artifact" {
  count = var.config_file == null ? 1 : 0

  url             = "${var.infrahub_address}/api/storage/object/${local.artifact.storage_id.value}"
  request_headers = { X-INFRAHUB-KEY = var.infrahub_api_key }

  lifecycle {
    precondition {
      condition     = local.artifact != null && try(local.artifact.status.value, "") == "Ready"
      error_message = "No Ready f5-ltm-configuration artifact for ${var.device} on Infrahub branch ${var.infrahub_branch}."
    }
    postcondition {
      condition     = self.status_code == 200
      error_message = "Could not download the f5-ltm-configuration artifact from Infrahub storage."
    }
  }
}

locals {
  config = var.config_file != null ? file(var.config_file) : data.http.artifact[0].response_body
}

module "ltm" {
  source = "../../modules/f5_ltm"

  config        = local.config
  bigip_address = var.bigip_address
}

# --- adoption of objects that already exist on the box -----------------------
# Terraform cannot create an LTM object that exists, so the first run on a
# configured box imports instead. Imports are no-ops once the object is in
# state, but they fail for objects the box lacks: keep adopt_existing off
# after the first apply.

locals {
  adopt = var.adopt_existing ? module.ltm.import_ids : {
    monitors = {}, nodes = {}, pools = {}, pool_members = {}, snatpools = {}, virtual_servers = {}
  }
}

import {
  for_each = local.adopt.monitors
  to       = module.ltm.bigip_ltm_monitor.this[each.key]
  id       = each.value
}

import {
  for_each = local.adopt.nodes
  to       = module.ltm.bigip_ltm_node.this[each.key]
  id       = each.value
}

import {
  for_each = local.adopt.pools
  to       = module.ltm.bigip_ltm_pool.this[each.key]
  id       = each.value
}

import {
  for_each = local.adopt.pool_members
  to       = module.ltm.bigip_ltm_pool_attachment.this[each.key]
  id       = each.value
}

import {
  for_each = local.adopt.snatpools
  to       = module.ltm.bigip_ltm_snatpool.this[each.key]
  id       = each.value
}

import {
  for_each = local.adopt.virtual_servers
  to       = module.ltm.bigip_ltm_virtual_server.this[each.key]
  id       = each.value
}

output "device" {
  value = module.ltm.device
}

output "managed_objects" {
  value = module.ltm.managed_objects
}

output "artifact_checksum" {
  description = "Checksum of the Infrahub artifact applied (null for a local config_file)."
  value       = try(local.artifact.checksum.value, null)
}
