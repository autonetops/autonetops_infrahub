# F5 BIG-IP LTM objects from the Infrahub f5-ltm-configuration artifact.
#
# The YAML already speaks the bigip provider's language (transforms/f5_config.py
# makes every vendor decision), so this module only maps fields. Each map key
# is the object's full path and is used as both the for_each key and the F5
# name: renaming an object in Infrahub replaces it on the box.
#
# Terraform owns exactly what the document lists. An object removed from
# Infrahub is deleted on the next apply; objects never listed are left alone.

locals {
  cfg = yamldecode(var.config)

  monitors        = { for k, v in local.cfg.monitors : k => v }
  nodes           = { for k, v in local.cfg.nodes : k => v }
  pools           = { for k, v in local.cfg.pools : k => v }
  snatpools       = { for k, v in local.cfg.snatpools : k => v }
  virtual_servers = { for k, v in local.cfg.virtual_servers : k => v }

  # Pool members are nested under their pool in the document and are separate
  # bigip_ltm_pool_attachment resources here, keyed "<pool>|<member>".
  pool_members = merge(concat([{}], [
    for pool, p in local.pools : {
      for member, m in p.members : "${pool}|${member}" => merge(m, { pool = pool, node = member })
    }
  ])...)
}

resource "bigip_ltm_monitor" "this" {
  for_each = local.monitors

  name     = each.key
  parent   = each.value.parent
  interval = try(each.value.interval, null)
  timeout  = try(each.value.timeout, null)
  send     = try(each.value.send, null)
  receive  = try(each.value.receive, null)
}

resource "bigip_ltm_node" "this" {
  for_each = local.nodes

  name    = each.key
  address = each.value.address
  monitor = each.value.monitor

  lifecycle {
    # Infrahub does not model node descriptions; leave whatever the box has.
    ignore_changes = [description]
  }
}

resource "bigip_ltm_pool" "this" {
  for_each = local.pools

  name                   = each.key
  description            = each.value.description
  load_balancing_mode    = each.value.load_balancing_mode
  minimum_active_members = each.value.minimum_active_members
  slow_ramp_time         = each.value.slow_ramp_time
  service_down_action    = each.value.service_down_action
  reselect_tries         = each.value.reselect_tries
  allow_snat             = each.value.allow_snat
  allow_nat              = each.value.allow_nat
  # The provider reads "no monitor" back as [""]; an empty list would plan a
  # change forever. [""] also clears a monitor the box has (verified live).
  monitors = length(each.value.monitors) > 0 ? each.value.monitors : [""]

  depends_on = [bigip_ltm_monitor.this]
}

resource "bigip_ltm_pool_attachment" "this" {
  for_each = local.pool_members

  pool             = bigip_ltm_pool.this[each.value.pool].name
  node             = each.value.node
  ratio            = each.value.ratio
  priority_group   = each.value.priority_group
  connection_limit = each.value.connection_limit
  state            = each.value.state

  # The member's node must exist first, or the box auto-creates one that
  # Terraform does not own.
  depends_on = [bigip_ltm_node.this]
}

resource "bigip_ltm_snatpool" "this" {
  for_each = local.snatpools

  name    = each.key
  members = each.value.members
}

resource "bigip_ltm_virtual_server" "this" {
  for_each = local.virtual_servers

  name                       = each.key
  description                = each.value.description
  destination                = each.value.destination
  port                       = each.value.port
  mask                       = each.value.mask
  ip_protocol                = each.value.ip_protocol
  source                     = each.value.source
  pool                       = each.value.pool
  profiles                   = each.value.profiles
  client_profiles            = each.value.client_profiles
  server_profiles            = each.value.server_profiles
  persistence_profiles       = each.value.persistence_profiles
  irules                     = each.value.irules
  source_address_translation = each.value.source_address_translation
  snatpool                   = each.value.snatpool
  translate_address          = each.value.translate_address
  translate_port             = each.value.translate_port
  vlans                      = each.value.vlans
  vlans_enabled              = each.value.vlans_enabled
  connection_limit           = each.value.connection_limit
  source_port                = each.value.source_port
  state                      = each.value.state

  depends_on = [bigip_ltm_pool.this, bigip_ltm_pool_attachment.this, bigip_ltm_snatpool.this]
}

# --- zero-value workaround ----------------------------------------------------
# bigip provider 1.29 drops zero/empty values from its requests, so a field
# that goes TO 0 / [] / null in Infrahub is silently left at its old value on
# the box (found by tests/live_test.sh):
#   virtual: connection_limit -> 0, vlans -> [], description -> null
#   pool member: priority_group -> 0, connection_limit -> 0
# Pool fields and lists like irules/persistence are not affected.
# For those fields, each object whose value is zero gets a bigip_command that
# writes it explicitly in tmsh. The for_each key carries a hash of the
# commands, so a change in which fields are zero creates a fresh resource and
# the commands run once more; unchanged objects do nothing on later applies.

locals {
  vs_zero_commands = {
    for k, v in local.virtual_servers : k => compact([
      v.connection_limit == 0 ? "modify ltm virtual ${k} connection-limit 0" : "",
      length(v.vlans) == 0 ? "modify ltm virtual ${k} vlans none vlans-disabled" : "",
      v.description == null ? "modify ltm virtual ${k} description none" : "",
    ])
  }
  member_zero_commands = {
    for k, m in local.pool_members : k => compact([
      m.priority_group == 0 ? "modify ltm pool ${m.pool} members modify { ${m.node} { priority-group 0 } }" : "",
      m.connection_limit == 0 ? "modify ltm pool ${m.pool} members modify { ${m.node} { connection-limit 0 } }" : "",
    ])
  }
  zero_commands = merge(
    { for k, c in local.vs_zero_commands : "virtual ${k} #${substr(sha1(join("\n", c)), 0, 8)}" => c if length(c) > 0 },
    { for k, c in local.member_zero_commands : "member ${k} #${substr(sha1(join("\n", c)), 0, 8)}" => c if length(c) > 0 },
  )
}

resource "bigip_command" "zero_values" {
  for_each = local.zero_commands

  commands = each.value
  when     = "apply"

  depends_on = [bigip_ltm_virtual_server.this, bigip_ltm_pool_attachment.this]
}
