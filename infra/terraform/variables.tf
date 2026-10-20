variable "region" {
  description = "Primary AWS region (API, database, images bucket)."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Name prefix for all resources."
  type        = string
  default     = "data-labeler"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "single_nat_gateway" {
  description = "Use one NAT gateway (cheaper, less resilient). Set false in production."
  type        = bool
  default     = false
}

variable "eks_version" {
  type    = string
  default = "1.30"
}

variable "node_instance_types" {
  type    = list(string)
  default = ["t3.large"]
}

variable "node_min_size" {
  type    = number
  default = 3
}

variable "node_max_size" {
  type    = number
  default = 8
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_allocated_storage" {
  type    = number
  default = 50
}

variable "db_multi_az" {
  type    = bool
  default = true
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "web_domain" {
  description = "Public hostname of the SPA (e.g. labeler.example.com). Empty = use the default *.cloudfront.net name."
  type        = string
  default     = ""
}

variable "web_certificate_arn" {
  description = "ACM certificate in us-east-1 covering web_domain. Required when web_domain is set."
  type        = string
  default     = ""
}

variable "cognito_domain_prefix" {
  description = "Prefix for the Cognito Hosted UI domain (must be globally unique)."
  type        = string
}

variable "extra_callback_urls" {
  description = "Extra OAuth callback URLs (e.g. http://localhost:5173/auth/callback for development)."
  type        = list(string)
  default     = []
}

variable "github_repository" {
  description = "GitHub repo (owner/name) allowed to assume the CI/CD role via OIDC. Empty disables the role."
  type        = string
  default     = ""
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if it already exists in the account."
  type        = bool
  default     = true
}

variable "api_domain" {
  description = "Public hostname of the API (e.g. api.labeler.example.com). Added to the SPA's CSP connect-src."
  type        = string
  default     = ""
}

variable "waf_enabled" {
  description = "Create WAF web ACLs for CloudFront and the API load balancer."
  type        = bool
  default     = true
}

variable "waf_block_mode" {
  description = "false = WAF rules only count (tune against real traffic, then set true)."
  type        = bool
  default     = true
}

variable "waf_rate_limit_api" {
  description = "Per-IP requests per 5 minutes on the API load balancer."
  type        = number
  default     = 2000
}

variable "waf_rate_limit_cdn" {
  description = "Per-IP requests per 5 minutes on CloudFront (image grids load many files)."
  type        = number
  default     = 10000
}

variable "alarm_email" {
  description = "Email subscribed to the alarm SNS topic (confirm the subscription email). Empty = topic only (wire PagerDuty/Slack yourself)."
  type        = string
  default     = ""
}

variable "api_alb_name" {
  description = "Name of the API's ALB (the Ingress pins it with alb.ingress.kubernetes.io/load-balancer-name). Set after the first deploy to enable ALB alarms; empty skips them because the ALB is created by the cluster, not Terraform."
  type        = string
  default     = ""
}

variable "submitted_age_alarm_seconds" {
  description = "Alarm when the oldest image awaiting review is older than this."
  type        = number
  default     = 86400
}

variable "rds_connections_alarm" {
  description = "DatabaseConnections alarm threshold. db.t4g.medium allows ~450; the default is 80% of that."
  type        = number
  default     = 360
}
