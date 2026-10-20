# Alarms. Everything notifies one SNS topic. Runbooks: docs/runbooks/alarms.md.
#
# Alarm names are stable and prefixed with the environment; the runbook is organised by them.

locals {
  # The app tags metrics with its ENVIRONMENT setting ("production"), Terraform's name is "prod".
  app_environment = var.environment == "prod" ? "production" : var.environment
  alarm_actions   = [aws_sns_topic.alarms.arn]
}

resource "aws_sns_topic" "alarms" {
  name = "${local.prefix}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alarm_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# ---------------------------------------------------------------------------
# Reaper: it is both the lease janitor and the system heartbeat.
# ---------------------------------------------------------------------------

# No run in ten minutes (CronJob not scheduling, image pull failing, DB unreachable before the
# metric is written...). Missing data counts as breaching, which is the point.
resource "aws_cloudwatch_metric_alarm" "reaper_stalled" {
  alarm_name          = "${local.prefix}-reaper-stalled"
  alarm_description   = "No reaper heartbeat for 10 minutes. Expired leases are not being returned to the queue."
  namespace           = "DataLabeler"
  metric_name         = "ReaperRuns"
  dimensions          = { Environment = local.app_environment }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "reaper_failures" {
  alarm_name          = "${local.prefix}-reaper-failures"
  alarm_description   = "The reaper job is failing."
  namespace           = "DataLabeler"
  metric_name         = "ReaperFailures"
  dimensions          = { Environment = local.app_environment }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# Work waiting for a reviewer for too long: not enough reviewers, or reviewers are stuck.
resource "aws_cloudwatch_metric_alarm" "review_backlog_age" {
  alarm_name          = "${local.prefix}-review-backlog-age"
  alarm_description   = "The oldest submitted image has waited longer than the threshold for review."
  namespace           = "DataLabeler"
  metric_name         = "OldestSubmittedAgeSeconds"
  dimensions          = { Environment = local.app_environment }
  statistic           = "Maximum"
  period              = 900
  evaluation_periods  = 2
  threshold           = var.submitted_age_alarm_seconds
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching" # reaper_stalled covers a missing heartbeat
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ---------------------------------------------------------------------------
# API load balancer (created by the Load Balancer Controller, so opt-in by name)
# ---------------------------------------------------------------------------

data "aws_lb" "api" {
  count = var.api_alb_name != "" ? 1 : 0
  name  = var.api_alb_name
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  count               = var.api_alb_name != "" ? 1 : 0
  alarm_name          = "${local.prefix}-api-5xx-rate"
  alarm_description   = "More than 5% of API requests are failing with 5xx (503 db_busy included)."
  comparison_operator = "GreaterThanThreshold"
  threshold           = 5
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions

  metric_query {
    id          = "rate"
    expression  = "IF(requests < 50, 0, 100 * FILL(errors, 0) / requests)"
    label       = "5xx percent"
    return_data = true
  }
  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_Target_5XX_Count"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = data.aws_lb.api[0].arn_suffix }
    }
  }
  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 60
      stat        = "Sum"
      dimensions  = { LoadBalancer = data.aws_lb.api[0].arn_suffix }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "api_unhealthy_targets" {
  count               = var.api_alb_name != "" ? 1 : 0
  alarm_name          = "${local.prefix}-api-unhealthy-targets"
  alarm_description   = "ALB has unhealthy API targets (failing /readyz)."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  dimensions          = { LoadBalancer = data.aws_lb.api[0].arn_suffix }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 5
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

# ---------------------------------------------------------------------------
# RDS
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.prefix}-rds-cpu"
  alarm_description   = "Database CPU above 80% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name          = "${local.prefix}-rds-connections"
  alarm_description   = "Database connections near max_connections. Pods are holding more than the pool budget."
  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  datapoints_to_alarm = 3
  threshold           = var.rds_connections_alarm
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.prefix}-rds-free-storage"
  alarm_description   = "Less than 20% of the allocated database storage is free."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.db_allocated_storage * 0.2 * 1024 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "rds_freeable_memory" {
  alarm_name          = "${local.prefix}-rds-freeable-memory"
  alarm_description   = "Database freeable memory below 256 MiB."
  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 3
  threshold           = 256 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "missing"
  alarm_actions       = local.alarm_actions
  ok_actions          = local.alarm_actions
}
