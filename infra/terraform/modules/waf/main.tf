# Web ACL with AWS managed rule groups plus a per-IP rate limit.
# One module, instantiated twice: scope = CLOUDFRONT (us-east-1 provider) and REGIONAL (ALB).

terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
    }
  }
}

variable "name" {
  type = string
}

variable "scope" {
  type = string

  validation {
    condition     = contains(["CLOUDFRONT", "REGIONAL"], var.scope)
    error_message = "scope must be CLOUDFRONT or REGIONAL."
  }
}

variable "rate_limit" {
  description = "Max requests per 5 minutes from one IP before it is blocked."
  type        = number
}

variable "block_mode" {
  description = "false = count only (use to tune before enforcing)."
  type        = bool
  default     = true
}

locals {
  managed_groups = [
    { name = "AWSManagedRulesCommonRuleSet", priority = 10 },
    { name = "AWSManagedRulesKnownBadInputsRuleSet", priority = 20 },
    { name = "AWSManagedRulesAmazonIpReputationList", priority = 30 },
  ]
}

resource "aws_wafv2_web_acl" "this" {
  name  = var.name
  scope = var.scope

  default_action {
    allow {}
  }

  dynamic "rule" {
    for_each = local.managed_groups
    content {
      name     = rule.value.name
      priority = rule.value.priority

      # Managed groups carry their own actions; override_action count = observe only.
      override_action {
        dynamic "none" {
          for_each = var.block_mode ? [1] : []
          content {}
        }
        dynamic "count" {
          for_each = var.block_mode ? [] : [1]
          content {}
        }
      }

      statement {
        managed_rule_group_statement {
          name        = rule.value.name
          vendor_name = "AWS"

          # Annotation saves post JSON bodies larger than this rule's 8 KB limit.
          dynamic "rule_action_override" {
            for_each = rule.value.name == "AWSManagedRulesCommonRuleSet" ? ["SizeRestrictions_BODY"] : []
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name}-${rule.value.name}"
        sampled_requests_enabled   = true
      }
    }
  }

  rule {
    name     = "rate-limit-per-ip"
    priority = 1

    action {
      dynamic "block" {
        for_each = var.block_mode ? [1] : []
        content {}
      }
      dynamic "count" {
        for_each = var.block_mode ? [] : [1]
        content {}
      }
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name}-rate-limit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }
}

output "arn" {
  value = aws_wafv2_web_acl.this.arn
}
