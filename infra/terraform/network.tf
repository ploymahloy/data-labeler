data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  prefix = "${var.name}-${var.environment}"
  azs    = slice(data.aws_availability_zones.available.names, 0, 3)
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = local.prefix
  cidr = var.vpc_cidr

  azs              = local.azs
  private_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets   = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 192 + i)]
  database_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 200 + i)]

  create_database_subnet_group = true

  enable_nat_gateway     = true
  single_nat_gateway     = var.single_nat_gateway
  one_nat_gateway_per_az = !var.single_nat_gateway

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Tags the AWS Load Balancer Controller uses for subnet discovery.
  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = "1"
  }
}

# WAF for the API's ALB (the ALB itself is created by the Load Balancer Controller from the Ingress;
# the Ingress references this ACL by ARN via alb.ingress.kubernetes.io/wafv2-acl-arn).
module "waf_api" {
  count  = var.waf_enabled ? 1 : 0
  source = "./modules/waf"

  name       = "${local.prefix}-api"
  scope      = "REGIONAL"
  rate_limit = var.waf_rate_limit_api
  block_mode = var.waf_block_mode
}
