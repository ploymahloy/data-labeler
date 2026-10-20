module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.24"

  cluster_name    = local.prefix
  cluster_version = var.eks_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access           = true
  enable_cluster_creator_admin_permissions = true
  enable_irsa                              = true

  cluster_addons = {
    coredns    = {}
    kube-proxy = {}
    vpc-cni = {
      # Enforce Kubernetes NetworkPolicy (deploy/k8s/base/networkpolicies.yaml).
      configuration_values = jsonencode({ enableNetworkPolicy = "true" })
    }
  }

  eks_managed_node_groups = {
    default = {
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_min_size
      subnet_ids     = module.vpc.private_subnets
    }
  }
}

# ---------------------------------------------------------------------------
# IRSA roles
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

# The API pod: presign uploads, read/verify/delete image objects, read the app secret (via ESO is separate).
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "ImageObjects"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.images.arn}/*"]
  }
  statement {
    sid       = "ImageBucketList"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.images.arn]
  }
}

resource "aws_iam_policy" "api" {
  name   = "${local.prefix}-api"
  policy = data.aws_iam_policy_document.api.json
}

module "api_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name = "${local.prefix}-api"

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["labeler:labeler-api"]
    }
  }

  role_policy_arns = {
    api = aws_iam_policy.api.arn
  }
}

module "lb_controller_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name                              = "${local.prefix}-lb-controller"
  attach_load_balancer_controller_policy = true

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:aws-load-balancer-controller"]
    }
  }
}

module "external_secrets_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.44"

  role_name                             = "${local.prefix}-external-secrets"
  attach_external_secrets_policy        = true
  external_secrets_secrets_manager_arns = [aws_secretsmanager_secret.app.arn]
  external_secrets_ssm_parameter_arns   = []
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["external-secrets:external-secrets"]
    }
  }
}

# Cluster add-ons (AWS Load Balancer Controller, External Secrets Operator) live in the
# separate `addons/` stack: Helm needs a running cluster, so they are applied after this one.
