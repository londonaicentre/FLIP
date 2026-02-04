terraform {
  backend "s3" {
    key     = "flip/terraform.tfstate"
    region  = "eu-west-2"
    encrypt = true
    use_lockfile = true
  }
}
