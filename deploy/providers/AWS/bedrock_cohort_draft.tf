# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

############################
# Cohort SQL drafting (Beta, FLIP#1556)
############################
#
# Opt-in: with var.cohort_draft_enabled false (the default) every block here has
# count 0 and the flip-api task env gains nothing, so the plan is unchanged.
#
# Enabled, it adds a Bedrock Guardrail (+ a published version) that blocks
# patient identifiers in the researcher's description, an inline policy on the
# flip-api task role to call the chosen model and apply that guardrail, and the
# COHORT_DRAFT_* settings in the flip-api task env.
#
# Not created here: the private route to Bedrock. On the LZA estates the hub's
# app subnets have no internet egress, so the networking account must provide
# an interface VPC endpoint for bedrock-runtime, which flip-api's client calls
# (InvokeModel), reachable from those subnets, before the feature is switched on
# (see the CohortDraftNetworkPrerequisite output).

locals {
  # A cross-Region inference profile ID is the foundation model ID behind a
  # geography prefix (`eu.anthropic.…`, `global.anthropic.…`).
  cohort_draft_model_is_profile = can(regex("^(global|eu|us|us-gov|apac|jp|au|ca)\\.", var.cohort_draft_bedrock_model_id))
  cohort_draft_foundation_model = local.cohort_draft_model_is_profile ? replace(var.cohort_draft_bedrock_model_id, "/^[a-z-]+\\./", "") : var.cohort_draft_bedrock_model_id
  cohort_draft_profile_arn      = "arn:aws:bedrock:${var.AWS_REGION}:${data.aws_caller_identity.current.account_id}:inference-profile/${var.cohort_draft_bedrock_model_id}"

  cohort_draft_env = var.cohort_draft_enabled ? {
    COHORT_DRAFT_ENABLED           = "true"
    COHORT_DRAFT_PROVIDER          = "bedrock"
    COHORT_DRAFT_BEDROCK_MODEL_ID  = var.cohort_draft_bedrock_model_id
    COHORT_DRAFT_BEDROCK_REGION    = var.AWS_REGION
    COHORT_DRAFT_GUARDRAIL_ID      = aws_bedrock_guardrail.cohort_draft[0].guardrail_id
    COHORT_DRAFT_GUARDRAIL_VERSION = aws_bedrock_guardrail_version.cohort_draft[0].version
  } : {}

  # Blocked outright in the description. There is no date-of-birth entity; AGE is
  # left out because "patients over 65" is an ordinary cohort description.
  cohort_draft_blocked_pii = ["ADDRESS", "EMAIL", "NAME", "PHONE", "UK_NATIONAL_HEALTH_SERVICE_NUMBER"]
}

resource "aws_bedrock_guardrail" "cohort_draft" {
  count = var.cohort_draft_enabled ? 1 : 0

  name                      = "flip-cohort-draft"
  description               = "Blocks patient identifiers in cohort SQL drafting descriptions (FLIP#1556)."
  blocked_input_messaging   = "Your description looks like it contains patient details (a name, NHS number, address, email or phone number). Describe the cohort without identifying anyone, then try again."
  blocked_outputs_messaging = "The draft could not be returned. Try rephrasing your description."

  sensitive_information_policy_config {
    dynamic "pii_entities_config" {
      for_each = toset(local.cohort_draft_blocked_pii)
      content {
        type           = pii_entities_config.value
        action         = "BLOCK"
        input_action   = "BLOCK"
        input_enabled  = true
        output_action  = "NONE"
        output_enabled = false
      }
    }

    # The NHS number's shape, for one written without the context the entity
    # detector needs. Ten digits, optionally grouped 3-3-4.
    regexes_config {
      name           = "nhs-number-shape"
      description    = "Ten digits, optionally grouped 3-3-4 (NHS number)"
      pattern        = "\\b\\d{3}\\s?\\d{3}\\s?\\d{4}\\b"
      action         = "BLOCK"
      input_action   = "BLOCK"
      input_enabled  = true
      output_action  = "NONE"
      output_enabled = false
    }
  }
}

resource "aws_bedrock_guardrail_version" "cohort_draft" {
  count = var.cohort_draft_enabled ? 1 : 0

  guardrail_arn = aws_bedrock_guardrail.cohort_draft[0].guardrail_arn
  description   = "Published for flip-api (FLIP#1556)"
}

# Scoped to the configured model only. A foundation model ID gets its in-Region
# ARN. An inference profile needs the profile ARN plus the foundation model in
# every Region the profile can route to: the `*` Region covers each geography's
# destinations, the empty Region a global profile's, and the condition limits
# both to calls made through this one profile.
data "aws_iam_policy_document" "cohort_draft" {
  count = var.cohort_draft_enabled ? 1 : 0

  statement {
    sid     = "BedrockInvokeCohortDraftModel"
    actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = [
      local.cohort_draft_model_is_profile
      ? local.cohort_draft_profile_arn
      : "arn:aws:bedrock:${var.AWS_REGION}::foundation-model/${local.cohort_draft_foundation_model}",
    ]
  }

  dynamic "statement" {
    for_each = local.cohort_draft_model_is_profile ? [1] : []
    content {
      sid     = "BedrockInvokeCohortDraftModelViaProfile"
      actions = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      resources = [
        "arn:aws:bedrock:*::foundation-model/${local.cohort_draft_foundation_model}",
        "arn:aws:bedrock:::foundation-model/${local.cohort_draft_foundation_model}",
      ]
      condition {
        test     = "StringEquals"
        variable = "bedrock:InferenceProfileArn"
        values   = [local.cohort_draft_profile_arn]
      }
    }
  }

  # InvokeModel with the guardrail headers also needs ApplyGuardrail on it.
  statement {
    sid       = "BedrockApplyCohortDraftGuardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.cohort_draft[0].guardrail_arn]
  }
}

resource "aws_iam_role_policy" "ecs_flip_api_task_cohort_draft" {
  count = var.cohort_draft_enabled ? 1 : 0

  name   = "flip-api-cohort-draft"
  role   = aws_iam_role.ecs_flip_api_task.id
  policy = data.aws_iam_policy_document.cohort_draft[0].json
}

output "CohortDraftGuardrailId" {
  description = "Bedrock Guardrail ID for cohort SQL drafting (null while cohort_draft_enabled is false)."
  value       = one(aws_bedrock_guardrail.cohort_draft[*].guardrail_id)
}

output "CohortDraftGuardrailVersion" {
  description = "Published Bedrock Guardrail version flip-api uses (null while cohort_draft_enabled is false)."
  value       = one(aws_bedrock_guardrail_version.cohort_draft[*].version)
}

output "CohortDraftNetworkPrerequisite" {
  description = "What the networking account must provide before cohort drafting can reach Bedrock (null while disabled)."
  value       = var.cohort_draft_enabled ? "An interface VPC endpoint for bedrock-runtime (com.amazonaws.${var.AWS_REGION}.bedrock-runtime), which flip-api calls with InvokeModel, with private DNS, reachable from the hub's app subnets. Not created by this root." : null
}
