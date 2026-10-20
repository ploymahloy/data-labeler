# One distribution, two origins:
#   /            -> web bucket (SPA, public)
#   /batches/*   -> images bucket (signed URLs only: trusted key group)

resource "aws_cloudfront_origin_access_control" "s3" {
  name                              = "${local.prefix}-s3"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Key pair for signed image URLs. The private key is stored in Secrets Manager (secrets.tf).
resource "tls_private_key" "cloudfront_signer" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "aws_cloudfront_public_key" "signer" {
  provider    = aws.us_east_1
  name_prefix = "${local.prefix}-"
  encoded_key = tls_private_key.cloudfront_signer.public_key_pem
}

resource "aws_cloudfront_key_group" "signer" {
  provider = aws.us_east_1
  name     = "${local.prefix}-signers"
  items    = [aws_cloudfront_public_key.signer.id]
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# Security headers. The SPA is a static bundle (no inline scripts), so the CSP can be strict:
# scripts and styles only from this origin, connections only to the API, Cognito and S3 uploads.
locals {
  csp_connect = compact([
    "'self'",
    var.api_domain != "" ? "https://${var.api_domain}" : "",
    "https://${var.cognito_domain_prefix}.auth.${var.region}.amazoncognito.com",
    "https://*.s3.amazonaws.com",
    "https://*.s3.${var.region}.amazonaws.com",
    "https://*.s3-accelerate.amazonaws.com",
  ])
  csp_img = compact([
    "'self'",
    "blob:",
    "data:",
    "https://*.cloudfront.net",
    var.web_domain != "" ? "https://${var.web_domain}" : "",
  ])
  csp = join("; ", [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src ${join(" ", local.csp_img)}",
    "connect-src ${join(" ", local.csp_connect)}",
    "font-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "upgrade-insecure-requests",
  ])
}

resource "aws_cloudfront_response_headers_policy" "security" {
  provider = aws.us_east_1
  name     = "${local.prefix}-security"

  security_headers_config {
    content_security_policy {
      content_security_policy = local.csp
      override                = true
    }
    strict_transport_security {
      access_control_max_age_sec = 63072000
      include_subdomains         = true
      preload                    = true
      override                   = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
  }

  custom_headers_config {
    items {
      header   = "Permissions-Policy"
      value    = "camera=(), microphone=(), geolocation=(), payment=()"
      override = true
    }
  }
}

# WAF in front of CloudFront (SPA + signed image URLs). CLOUDFRONT-scope ACLs live in us-east-1.
module "waf_cdn" {
  count     = var.waf_enabled ? 1 : 0
  source    = "./modules/waf"
  providers = { aws = aws.us_east_1 }

  name       = "${local.prefix}-cdn"
  scope      = "CLOUDFRONT"
  rate_limit = var.waf_rate_limit_cdn
  block_mode = var.waf_block_mode
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.prefix} SPA and image delivery"
  default_root_object = "index.html"
  price_class         = "PriceClass_All" # global teams: use every edge location
  http_version        = "http2and3"
  aliases             = var.web_domain != "" ? [var.web_domain] : []
  web_acl_id          = var.waf_enabled ? module.waf_cdn[0].arn : null

  origin {
    origin_id                = "web"
    domain_name              = aws_s3_bucket.web.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  origin {
    origin_id                = "images"
    domain_name              = aws_s3_bucket.images.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  default_cache_behavior {
    target_origin_id           = "web"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  ordered_cache_behavior {
    path_pattern           = "batches/*"
    target_origin_id       = "images"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id        = data.aws_cloudfront_cache_policy.optimized.id
    trusted_key_groups     = [aws_cloudfront_key_group.signer.id]
  }

  # SPA client-side routing: unknown paths return index.html.
  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.web_certificate_arn == ""
    acm_certificate_arn            = var.web_certificate_arn != "" ? var.web_certificate_arn : null
    ssl_support_method             = var.web_certificate_arn != "" ? "sni-only" : null
    minimum_protocol_version       = var.web_certificate_arn != "" ? "TLSv1.2_2021" : null
  }
}
