resource "random_password" "db" {
  length  = 32
  special = false # keeps the password URL-safe inside DATABASE_URL
}

resource "aws_security_group" "db" {
  name_prefix = "${local.prefix}-db-"
  description = "Postgres access from EKS nodes/pods"
  vpc_id      = module.vpc.vpc_id

  ingress {
    description     = "Postgres from EKS"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [module.eks.node_security_group_id, module.eks.cluster_primary_security_group_id]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_parameter_group" "postgres" {
  name_prefix = "${local.prefix}-pg16-"
  family      = "postgres16"

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_db_instance" "main" {
  identifier = local.prefix

  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage
  max_allocated_storage = var.db_allocated_storage * 4
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "labeler"
  username = "labeler"
  password = random_password.db.result
  port     = 5432

  multi_az               = var.db_multi_az
  db_subnet_group_name   = module.vpc.database_subnet_group_name
  vpc_security_group_ids = [aws_security_group.db.id]
  parameter_group_name   = aws_db_parameter_group.postgres.name
  publicly_accessible    = false

  backup_retention_period      = 14
  copy_tags_to_snapshot        = true
  deletion_protection          = var.db_deletion_protection
  skip_final_snapshot          = !var.db_deletion_protection
  final_snapshot_identifier    = var.db_deletion_protection ? "${local.prefix}-final" : null
  auto_minor_version_upgrade   = true
  performance_insights_enabled = true
  apply_immediately            = false
}
