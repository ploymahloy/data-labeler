terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.14"
    }
  }

  # Use a different state key from the core stack, e.g.:
  # backend "s3" {
  #   bucket         = "my-tf-state"
  #   key            = "data-labeler/prod-addons.tfstate"
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

# The cluster is read from AWS (it must already exist: apply the core stack first), so the
# Helm provider never depends on resources created in the same run.
data "aws_eks_cluster" "this" {
  name = local.cluster_name
}

provider "helm" {
  kubernetes {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    exec {
      api_version = "client.authentication.k8s.io/v1beta1"
      command     = "aws"
      args        = ["eks", "get-token", "--cluster-name", local.cluster_name, "--region", var.region]
    }
  }
}
