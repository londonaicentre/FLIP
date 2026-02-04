############################
# S3
############################

resource "aws_s3_bucket" "flip_bucket" {
  bucket = var.FLIP_BUCKET_NAME
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_cors_configuration" "flip_bucket_cors" {
  bucket = aws_s3_bucket.flip_bucket.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = ["*"]
    expose_headers  = []
  }
}

# Model files uploaded by researchers will be combined with the base app here
resource "aws_s3_object" "app_destination_bucket" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "app_destination_bucket/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# Model files uploaded by researchers will be stored here
resource "aws_s3_object" "model_files" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "model_files/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

# FL results will be stored here
resource "aws_s3_object" "uploaded_federated_data" {
  bucket  = aws_s3_bucket.flip_bucket.id
  key     = "uploaded_federated_data/"
  content = ""
  lifecycle {
    prevent_destroy = true
  }
}

############################
# Cognito
############################

resource "aws_cognito_user_pool" "flip_user_pool" {
  name                     = var.flip_user_pool_name
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  schema {
    name                = "email"
    attribute_data_type = "String"
    mutable             = false
    required            = true
  }
  deletion_protection = "ACTIVE"
  lifecycle {
    prevent_destroy = true
  }
}

resource "random_string" "cognito_domain" {
  length  = 8
  special = false
  upper   = false
}

resource "aws_cognito_user_pool_domain" "main" {
  domain                = random_string.cognito_domain.result
  user_pool_id          = aws_cognito_user_pool.flip_user_pool.id
  managed_login_version = 2

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_cognito_user_pool_client" "client" {
  name         = var.flip_cognito_client
  user_pool_id = aws_cognito_user_pool.flip_user_pool.id

  # app client information
  refresh_token_validity = 5
  access_token_validity  = 60
  id_token_validity      = 60
  token_validity_units {
    refresh_token = "days"
    access_token  = "minutes"
    id_token      = "minutes"
  }
  explicit_auth_flows = [
    "ALLOW_USER_AUTH",
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_USER_SRP_AUTH",
    "ALLOW_ADMIN_USER_PASSWORD_AUTH",
    "ALLOW_CUSTOM_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH"
  ]
  prevent_user_existence_errors = "ENABLED"

  # login page
  allowed_oauth_flows          = ["code", "implicit"]
  allowed_oauth_scopes         = ["email", "openid", "profile"]
  callback_urls                = ["https://localhost:44357"]
  logout_urls                  = ["https://localhost:44357"]
  supported_identity_providers = ["COGNITO"]

  lifecycle {
    prevent_destroy = true
  }
}

############################
# Cognito Users
############################

resource "aws_cognito_user" "admin_user" {
  user_pool_id = aws_cognito_user_pool.flip_user_pool.id
  username     = var.flip_cognito_admin_email
  attributes = {
    "email"          = var.flip_cognito_admin_email
    "email_verified" = true
  }
  password = var.ADMIN_USER_PASSWORD

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      enabled
    ]
  }
}

resource "aws_cognito_user" "researcher_user" {
  user_pool_id = aws_cognito_user_pool.flip_user_pool.id
  username     = var.flip_cognito_researcher_email
  attributes = {
    "email"          = var.flip_cognito_researcher_email
    "email_verified" = true
  }
  password = var.ADMIN_USER_PASSWORD

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      enabled
    ]
  }
}
