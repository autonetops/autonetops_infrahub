variable "config" {
  description = "F5 LTM desired state: the YAML of the Infrahub artifact f5-ltm-configuration (transforms/f5_config.py)."
  type        = string

  validation {
    condition     = try(yamldecode(var.config).format_version, null) == 1
    error_message = "config is not an f5-ltm-configuration document with format_version 1 (transforms/f5_config.py FORMAT_VERSION)."
  }
}

variable "bigip_address" {
  description = "Management address the provider is connected to. When set, it must equal device.management_address in config, so one box's config is never pushed to another."
  type        = string
  default     = null

  validation {
    condition     = var.bigip_address == null || try(yamldecode(var.config).device.management_address, null) == var.bigip_address
    error_message = "config was rendered for a load balancer with a different management address than bigip_address."
  }
}
