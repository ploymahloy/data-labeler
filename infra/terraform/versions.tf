terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Configure remote state before first real apply, e.g.:
  # backend "s3" {
  #   bucket         = "my-tf-state"
  #   key            = "data-labeler/prod.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "my-tf-locks"
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront public keys and ACM certs for CloudFront must live in us-east-1.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = var.name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
