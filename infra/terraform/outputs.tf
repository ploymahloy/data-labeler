output "cluster_name" {
  value = module.eks.cluster_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.backend.repository_url
}

output "api_irsa_role_arn" {
  value = module.api_irsa.iam_role_arn
}

output "images_bucket" {
  value = aws_s3_bucket.images.bucket
}

output "web_bucket" {
  value = aws_s3_bucket.web.bucket
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.main.domain_name
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.main.id
}

output "cloudfront_key_pair_id" {
  description = "Public key id used as CLOUDFRONT_KEY_PAIR_ID."
  value       = aws_cloudfront_public_key.signer.id
}

output "app_secret_name" {
  value = aws_secretsmanager_secret.app.name
}

output "cognito_user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.spa.id
}

output "cognito_hosted_ui_domain" {
  description = "VITE_COGNITO_DOMAIN for the frontend build."
  value       = "${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com"
}

output "web_base_url" {
  value = local.web_base_url
}

output "github_deploy_role_arn" {
  value = try(aws_iam_role.github_deploy[0].arn, null)
}

output "db_endpoint" {
  value = aws_db_instance.main.address
}
