locals {
  prefix       = "${var.name}-${var.environment}"
  cluster_name = local.prefix
}

# IAM roles created by the core stack (IRSA).
data "aws_iam_role" "lb_controller" {
  name = "${local.prefix}-lb-controller"
}

data "aws_iam_role" "external_secrets" {
  name = "${local.prefix}-external-secrets"
}

resource "helm_release" "aws_load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.lb_controller_chart_version

  set {
    name  = "clusterName"
    value = local.cluster_name
  }
  set {
    name  = "region"
    value = var.region
  }
  set {
    name  = "vpcId"
    value = data.aws_eks_cluster.this.vpc_config[0].vpc_id
  }
  set {
    name  = "serviceAccount.name"
    value = "aws-load-balancer-controller"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = data.aws_iam_role.lb_controller.arn
  }
}

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  namespace        = "external-secrets"
  create_namespace = true
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = var.external_secrets_chart_version

  set {
    name  = "installCRDs"
    value = "true"
  }
  set {
    name  = "serviceAccount.name"
    value = "external-secrets"
  }
  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = data.aws_iam_role.external_secrets.arn
  }
}

# The ClusterSecretStore is an instance of a CRD that only exists once the operator above is
# installed, so it ships as a tiny local chart (Helm does not need the CRD at plan time).
resource "helm_release" "secret_store" {
  name      = "aws-secrets-manager-store"
  namespace = "external-secrets"
  chart     = "${path.module}/charts/secret-store"

  set {
    name  = "region"
    value = var.region
  }

  depends_on = [helm_release.external_secrets]
}
