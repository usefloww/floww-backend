terraform {
  required_providers {
    stripe = {
      source  = "lukasaron/stripe"
      version = "1.7.0"
    }
  }
}

provider "stripe" {
  
}