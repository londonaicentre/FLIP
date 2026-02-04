variable "name" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "description" {
  type = string
}

variable "ingress_rules" {
  type = list(object({
    port                     = number
    description              = string
    source_security_group_id = optional(string)
  }))
}

variable "block_all_outbound" {
  type    = bool
  default = false
}
