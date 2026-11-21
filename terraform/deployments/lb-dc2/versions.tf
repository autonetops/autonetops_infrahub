terraform {
  required_version = ">= 1.9"

  required_providers {
    bigip = {
      source  = "F5Networks/bigip"
      version = "~> 1.29"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.4"
    }
  }

  # Local state on the operator's machine. Move to a shared backend before
  # more than one person (or a pipeline) applies.
  backend "local" {}
}
