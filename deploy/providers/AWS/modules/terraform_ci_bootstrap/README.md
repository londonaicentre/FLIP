<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->

# terraform_ci_bootstrap

Everything FLIP's Terraform CI needs to exist in an AWS account before it can run: the GitHub OIDC **plan role**
(read-only) and **apply role**, the **permissions boundary** that caps every role an apply creates, and, optionally,
the **Terraform state bucket**. FLIP#962 introduced the roles; FLIP#1199 moved their ownership here.

> [!IMPORTANT]
> **This is a published interface, not a private module of the FLIP root.** It is instantiated by a platform
> repository — `aicentre-lza-iac`, for AI Centre's LZA accounts — pinned to a FLIP commit SHA, and by
> [`../../ci`](../../ci) for anyone bootstrapping their own account. Renaming an input, changing a
> default, or changing a description on the roles or the boundary is a change for those callers. The boundary's
> description in particular must never change: `aws_iam_policy.description` forces replacement, and the policy is in
> use as a boundary on every FLIP role.

## Why it lives outside the FLIP root

The boundary is the ceiling on FLIP's own pipeline. Defined in the repository whose pipeline it caps, and applied by
that pipeline, anyone who can merge to FLIP could raise it. Instantiated by a platform repository, a FLIP merge can
only *propose* a change: nothing reaches an AI Centre account until the platform repository bumps the pinned SHA and
its reviewers approve the resulting plan.

## Using it

Pin a **full commit SHA**, through the GitHub archive URL. FLIP has no tag ruleset, so tags can move; and a SHA-pinned
`git::` source clones the whole repository on every `terraform init`, while the archive is the tree at that commit.

```hcl
module "flip_stag_terraform_ci" {
  source    = "https://github.com/londonaicentre/FLIP/archive/<SHA>.tar.gz//FLIP-<SHA>/deploy/providers/AWS/modules/terraform_ci_bootstrap"
  providers = { aws = aws.flip_stag }

  oidc_provider_arn  = aws_iam_openid_connect_provider.github_flip_stag.arn
  github_org         = "londonaicentre"
  github_repo        = "FLIP"
  environment        = "stag"
  github_environment = "aws-stag"
  apply_branch       = "develop"
  state_bucket_name  = "flip-terraform-state-lza-stag"

  # The LZA deploys its own boundary to every workload account and requires it on
  # every role (londonaicentre/lza#51), so use it rather than declaring FLIP's.
  create_permissions_boundary = false
  permissions_boundary_name   = "AICentre-WorkloadRoleBoundary"

  state_noncurrent_versions_retained = 20
}
```

- **`oidc_provider_arn`** — the account's GitHub OIDC provider. Never created here: an account holds one per issuer
  URL, shared by everything GitHub-driven in it, so it belongs to whatever owns the account's baseline IAM.
- **`github_org` / `github_repo`** have no defaults on purpose. A default of `londonaicentre/FLIP` would make a
  stranger's roles trust AI Centre's workflows.
- **`environment`** — `stag` or `prod`. Only staging's plan role accepts pull-request merge refs.
- **`create_permissions_boundary` / `permissions_boundary_name`** — by default the module declares
  `AICentre-FLIPTerraformBoundary`. Where the platform already deploys a boundary and requires it on every role, as an
  AWS Landing Zone Accelerator estate does, set `create_permissions_boundary = false` and name that policy instead: the
  apply role's grants and Denies then refer to it, and the FLIP root's `iam_permissions_boundary_name` must name it
  too.
- Everything else defaults to what the FLIP root needs; see [`variables.tf`](variables.tf).

## What the roles may do

Least privilege, with each grant explained where it is declared:

- **Plan role** ([`iam_plan.tf`](iam_plan.tf)): `ReadOnlyAccess` for configuration, and explicit Denies taking back
  what a plan never reads — S3 objects outside the state bucket, SSM parameter values outside `ssm_parameter_prefix`
  and AWS's public parameters, log contents, queue messages, table items, console output, RDS log files, the Cognito
  user listing — and every write to the state bucket. Plus a read grant on the one secret a refresh reads.
- **Apply role** ([`iam_apply.tf`](iam_apply.tf)): no managed policy. `<service>:*` for the AWS services the FLIP root
  uses (`apply_service_prefixes`), in the account's region and `us-east-1` only; IAM write only on the FLIP root's
  roles and instance profiles, by name (`managed_role_names`, `managed_instance_profile_names`), under the boundary and
  a three-policy attach allowlist; Denies on the CI roles and the boundary; the state object and its lock.
- **Boundary** ([`boundary.tf`](boundary.tf)): allow everything, deny identity management, Organizations and Account.

`tests/test_terraform_ci_bootstrap.py` holds `apply_service_prefixes` equal to the services the FLIP root's resource
types need, so the list can neither fall behind the root nor keep a service it no longer uses.

## Changing it

1. A FLIP PR changes the module. Merging it changes nothing in AWS.
2. Each platform repository opens a PR moving its pinned SHA (every module block that pins it). Its plan shows the
   IAM diff; that plan is what gets reviewed.
3. **Order matters when FLIP depends on the change.** A new role in the FLIP root must be added to
   `managed_role_names`, and a resource from an AWS service the root does not use yet needs its service in
   `apply_service_prefixes`; both are applied by the platform repository *before* the FLIP change that needs them, or
   the CI apply is denied.

## The state bucket

With `manage_state_bucket = true` (the default) the module declares the bucket and its versioning, public-access
block, encryption (`AES256`, or `aws:kms` with the AWS-managed key), lifecycle (the newest
`state_noncurrent_versions_retained` previous versions, each for `state_noncurrent_version_days`) and a TLS-only
bucket policy. `prevent_destroy` protects it; removing a caller's module block needs
`removed { from = … lifecycle { destroy = false } }`.

`restrict_state_writes` (off by default) adds a Deny on state-object writes for every principal except the apply role
and `state_writer_principal_arns`. Staging typically lists the engineers' SSO role there so laptop applies used to test
a branch keep working; production lists a break-glass role only. SSO patterns need their path:
`arn:aws:iam::*:role/aws-reserved/sso.amazonaws.com/*/AWSReservedSSO_<PermissionSet>_*`.

## Taking over an existing bootstrap

The resource addresses are the ones [`../../ci`](../../ci) used (the boundary now at `aws_iam_policy.apply_boundary[0]`),
so existing roles, policies and buckets import with plain `import` blocks. Three differences from a bootstrap made by an
older `ci/` are expected: the apply role's `PowerUserAccess` attachment is removed in favour of the `apply_services`
allowlist, `flip-terraform-apply-iam` scopes its role-writing verbs to the named roles, and the plan role gains
`deny-data-reads`. Beyond those, a correct import plan shows **no change** to any
role, inline policy, attachment or the boundary — only bucket-side additions (tags, lifecycle, the bucket policy) and
whatever `default_tags` the caller's provider stamps. Anything else touching a trust policy, a permission policy or a
description means the module and the live objects disagree: stop and find out why.
