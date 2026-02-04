variable "name_prefix" {
  type    = string
  default = "trust"
}

variable "AWS_REGION" {
  type    = string
  default = "eu-west-2"
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "key_name" {
  type    = string
  default = "~/.ssh/id_rsa"
}

variable "subnet_id" {
  type = string
}

variable "security_group_ids" {
  type    = list(string)
  default = []
}

variable "create_elastic_ip" {
  type    = bool
  default = false
}

variable "TRUST_API_PORT" {
  type    = number
}

variable "XNAT_PORT" {
  type    = number
}

variable "PACS_UI_PORT" {
  type    = number
}

variable "FL_API_PORT" {
  type    = number
}

variable "iam_instance_profile_name" {
  description = "Name of an existing IAM instance profile to attach to the Trust EC2 instance"
  type        = string
}
