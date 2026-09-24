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
> **This is a published interface, not a private module of the FLIP root.** It is instantiated by the platform
> repositories — `aicentre-iac` (self-contained accounts) and `aicentre-lza-iac` (LZA accounts) — pinned to a FLIP
> commit SHA, and by [`../../ci`](../../ci) for anyone bootstrapping their own account. Renaming an input, changing a
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

  state_noncurrent_versions_retained = 20
}
```

- **`oidc_provider_arn`** — the account's GitHub OIDC provider. Never created here: an account holds one per issuer
  URL, shared by everything GitHub-driven in it, so it belongs to whatever owns the account's baseline IAM.
- **`github_org` / `github_repo`** have no defaults on purpose. A default of `londonaicentre/FLIP` would make a
  stranger's roles trust AI Centre's workflows.
- **`environment`** — `stag` or `prod`. Only staging's plan role accepts pull-request merge refs.
- Everything else defaults to the values in use in every AI Centre account; see [`variables.tf`](variables.tf).

## Changing it

1. A FLIP PR changes the module. Merging it changes nothing in AWS.
2. Each platform repository opens a PR moving its pinned SHA (every module block that pins it). Its plan shows the
   IAM diff; that plan is what gets reviewed.
3. **Order matters when FLIP depends on the change.** A new role in the FLIP root must be added to
   `managed_role_names` here and applied by the platform repositories *before* the FLIP change that creates the role,
   or the CI apply cannot pass or re-trust it.

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

The resource addresses are the ones [`../../ci`](../../ci) used, so existing roles, policies and buckets import with
plain `import` blocks. A correct import plan shows **no change** to any role, inline policy, attachment or the
boundary — only bucket-side additions (tags, lifecycle, the bucket policy) and whatever `default_tags` the caller's
provider stamps. Anything touching a trust policy, a permission policy or a description means the module and the live
objects disagree: stop and find out why.
