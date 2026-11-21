variable "device" {
  description = "Infrahub DcimLoadBalancer name whose f5-ltm-configuration artifact is applied."
  type        = string
  default     = "LB-DC2"
}

variable "bigip_address" {
  description = "BIG-IP management address (iControl REST)."
  type        = string
  default     = "10.10.10.121"
}

variable "bigip_username" {
  type = string
}

variable "bigip_password" {
  type      = string
  sensitive = true
}

variable "config_file" {
  description = "Path to a rendered f5-ltm-configuration YAML (e.g. from `infrahubctl transform f5_config device=LB-DC2`). When null, the artifact is fetched from Infrahub."
  type        = string
  default     = null
}

variable "infrahub_address" {
  type    = string
  default = "http://localhost:8000"
}

variable "infrahub_branch" {
  type    = string
  default = "main"
}

variable "infrahub_api_key" {
  description = "Infrahub API token; needed only when config_file is null."
  type        = string
  sensitive   = true
  default     = null
}

variable "adopt_existing" {
  description = "Import every object in the config into state instead of creating it. Use once, for objects that already exist on the box; an import of an object the box lacks fails the plan."
  type        = bool
  default     = false
}
