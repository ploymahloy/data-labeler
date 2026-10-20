locals {
  web_base_url  = var.web_domain != "" ? "https://${var.web_domain}" : "https://${aws_cloudfront_distribution.main.domain_name}"
  callback_urls = concat(["${local.web_base_url}/auth/callback"], var.extra_callback_urls)
  logout_urls   = concat([local.web_base_url], [for u in var.extra_callback_urls : regex("^https?://[^/]+", u)])
}

resource "aws_cognito_user_pool" "main" {
  name = local.prefix

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = false
    temporary_password_validity_days = 7
  }

  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  # Team accounts are provisioned by admins, not self-service.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 3
      max_length = 256
    }
  }

  deletion_protection = "ACTIVE"
}

resource "aws_cognito_user_group" "roles" {
  for_each = toset(["admin", "labeler", "reviewer"])

  name         = each.key
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "${each.key} role"
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_pool_client" "spa" {
  name         = "${local.prefix}-spa"
  user_pool_id = aws_cognito_user_pool.main.id

  # Public client: the SPA uses the authorization-code flow with PKCE (no secret).
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = local.callback_urls
  logout_urls                          = local.logout_urls

  prevent_user_existence_errors = "ENABLED"
  enable_token_revocation       = true

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }
}
