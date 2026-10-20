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

data "aws_cloudfront_response_headers_policy" "security" {
  name = "Managed-SecurityHeadersPolicy"
}

resource "aws_cloudfront_distribution" "main" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "${local.prefix} SPA and image delivery"
  default_root_object = "index.html"
  price_class         = "PriceClass_All" # global teams: use every edge location
  http_version        = "http2and3"
  aliases             = var.web_domain != "" ? [var.web_domain] : []

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
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security.id
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
