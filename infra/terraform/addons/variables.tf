# Keep these identical to the core stack (they derive the names of the resources to look up).

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "name" {
  type    = string
  default = "data-labeler"
}

variable "environment" {
  type    = string
  default = "prod"
}

variable "lb_controller_chart_version" {
  type    = string
  default = "1.8.1"
}

variable "external_secrets_chart_version" {
  type    = string
  default = "0.9.20"
}
