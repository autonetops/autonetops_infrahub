terraform {
  # 1.9+: variable validation that reads another variable (bigip_address).
  required_version = ">= 1.9"

  required_providers {
    bigip = {
      source  = "F5Networks/bigip"
      version = "~> 1.29"
    }
  }
}
