# Live test root for the f5_ltm module (driven by ../live_test.sh).
terraform {
  required_providers {
    bigip = {
      source  = "F5Networks/bigip"
      version = "~> 1.29"
    }
  }
}

variable "config_file" { type = string }
variable "bigip_address" { type = string }
variable "bigip_username" { type = string }
variable "bigip_password" {
  type      = string
  sensitive = true
}

provider "bigip" {
  address                = var.bigip_address
  username               = var.bigip_username
  password               = var.bigip_password
  validate_certs_disable = true
  teem_disable           = true
}

module "ltm" {
  source = "../.."
  config = file(var.config_file)
}
