# Terraform CI bootstrap (for your own AWS account)

Everything FLIP's Terraform CI needs in an AWS account before its first run, for anyone deploying FLIP into an
account they own. This root is a thin wrapper around
[`../modules/terraform_ci_bootstrap`](../modules/terraform_ci_bootstrap): it adds a provider with an account guard,
looks up the account's GitHub OIDC provider, and keeps its own state.

> [!NOTE]
> **AI Centre's accounts are not bootstrapped from here.** The platform repositories — `aicentre-iac` for the
> self-contained accounts and `aicentre-lza-iac` for the LZA ones — instantiate the same module, pinned to a FLIP
> commit, through their own reviewed pipelines. Do not run this root in those accounts: `make plan` refuses when it
> finds the roles already there.

## What it creates

| Object | Purpose |
| --- | --- |
| `AICentre-FLIPTerraformPlanRole` | Assumed by PR plans (staging only) and the nightly drift run. `ReadOnlyAccess`, plus an explicit Deny on every write to the state bucket, plus a read grant on the one secret a plan refreshes |
| `AICentre-FLIPTerraformApplyRole` | Assumed only by `terraform_apply.yml` pushed to the environment's branch. `PowerUserAccess`, plus IAM write bounded by the boundary below, a managed-policy allowlist, `PassRole` scoped to the FLIP root's own roles, and Denies on the CI roles and the boundary itself |
| `AICentre-FLIPTerraformBoundary` | The permissions boundary every role the FLIP root creates carries (`iam_permissions_boundary_name` in `../variables.tf`) |
| The state bucket | Versioned, public access blocked, SSE-S3 by default (`state_bucket_sse_algorithm = "aws:kms"` for the AWS-managed key), a TLS-only bucket policy, bounded version history, `prevent_destroy` |

The names are inputs; the defaults are the ones the FLIP workflows and scripts expect. The module
[README](../modules/terraform_ci_bootstrap/README.md) and its `iam_*.tf` files explain each grant and Deny.

## Before you start

1. **A GitHub OIDC identity provider in the account**, declared by your own baseline IaC — this root looks it up and
   never creates it, because it is shared by everything GitHub-driven in the account. `make plan` checks for it first
   and prints the resource to declare if it is missing.
2. **A fork or copy of FLIP** whose workflows will assume the roles. `github_org` / `github_repo` have no defaults: a
   default of `londonaicentre/FLIP` would make your roles trust AI Centre's workflows.
3. Credentials for the account (`AWS_PROFILE`, SSO) able to create IAM roles and policies and an S3 bucket.

## Bootstrapping an account

One tfvars file per account. `TFVARS` selects it (default `terraform.tfvars`).

```bash
cp terraform.tfvars.example terraform.tfvars   # fill in: region, account ID, org/repo, environment, bucket name
make init                                       # local state — the bucket does not exist yet
make plan                                       # runs check-oidc-provider and check-not-managed-elsewhere first
make apply
make migrate-state                              # move the state into the bucket the apply just created
```

`make migrate-state` copies `backend.s3.tf.example` to `backend.tf` (gitignored), re-initialises with
`-migrate-state` against the new bucket (key `flip/ci/terraform.tfstate`, separate from the FLIP root's
`flip/terraform.tfstate`), and lists the migrated resources. Check the list, then delete the local
`terraform.tfstate` and `terraform.tfstate.backup`. From then on `make init` reattaches the remote state.

Use one copy of this directory per account: the local state, and then `backend.tf`, belong to the account they were
created for.

`allowed_account_ids` is the provider's account guard: with the wrong profile, Terraform stops before planning.

## Wiring it into GitHub

`make output` prints the role ARNs and the OIDC claims they expect. On the matching GitHub environment
(`aws-stag` / `aws-prod`) set `TF_PLAN_ROLE_ARN` and `TF_APPLY_ROLE_ARN` to the two ARNs, and point the FLIP root's
`FLIP_TFSTATE_BUCKET_NAME` at the state bucket. `../scripts/setup-github-environments.sh` seeds the environments and
verifies the roles' trust policies against the mode before it writes anything.

## Changing it

The roles are not managed by the pipeline they authorise, so a change reaches AWS only when you apply it: after
pulling a FLIP change to the module, `make plan && make apply`. **Order matters when FLIP adds an IAM role**: add it to
`managed_role_names` and apply here *before* the FLIP change that creates it, or the CI apply cannot pass or re-trust
it.

## Roles that already exist

`check-not-managed-elsewhere` refuses to plan when this root's state is empty but the apply role already exists. If the
roles genuinely belong to you:

- **Created by an earlier version of this root**, which declared the resources directly: point `make init` at that
  state (the same bucket and key). The `moved` blocks in `main.tf` re-address the nine IAM objects into the module
  with no change. That earlier version did not manage the bucket, so either import it
  (`import { to = module.terraform_ci.aws_s3_bucket.state[0] … }`, and likewise for its versioning, public-access
  block and encryption) or set `manage_state_bucket = false`.
- **Created some other way**: import them with `import` blocks at the module's addresses —
  `module.terraform_ci.aws_iam_role.terraform_plan` and so on (see the `moved` blocks for the full list). A correct
  import plan changes no trust policy, permission policy or description; anything else means the module and the live
  objects disagree.

## The OIDC claims, and the two easy mistakes

**`sub` carries the environment, not the ref.** When a job declares
`environment: aws-prod`, GitHub mints `sub` as
`repo:<org>/<repo>:environment:aws-prod`. It does **not** contain the
branch. A trust policy conditioned on `repo:…:ref:refs/heads/main` therefore
matches nothing and every apply is denied with no useful detail; relaxing that to
`repo:…:*` to make it work trades a broken policy for an open one. Every trust
policy here is written against the environment form.

Every job must declare an environment anyway — it is the only way a workflow can
read environment secrets, and the plan needs real values to produce a truthful
diff.

**`job_workflow_ref` is what actually pins the branch.** It names the workflow
file *and* the ref it was loaded from, and a pull request cannot forge it: a
PR-triggered run reports `@refs/pull/<n>/merge`, never `@refs/heads/<branch>`.
So:

- the apply role requires **exactly** `…/terraform_apply.yml@refs/heads/main`
  (prod) or `@refs/heads/develop` (stag) — a PR editing the apply workflow is
  denied, which is what makes automatic apply on merge safe to switch on;
- the plan role accepts exactly two shapes, and only the ones a run can actually
  present:
  - `terraform_plan.yml@refs/pull/*/merge` — **staging only**. `terraform_plan.yml`
    declares `environment: aws-stag` unconditionally, so no pull request can reach
    the production plan role; listing the merge-ref pattern there would be dead
    weight that reads like a permission.
  - `terraform_drift.yml@refs/heads/${drift_branch}` — the nightly run.

`terraform_plan.yml@refs/heads/<branch>` is deliberately absent: that workflow has
no push trigger, so no run can present it.

**`drift_branch` is not `apply_branch`, and conflating them breaks prod drift.**
`job_workflow_ref` names the branch the workflow *file was loaded from*. GitHub
only fires a `schedule` from the repository default branch, so a scheduled run
always presents `@refs/heads/develop` whatever environment it targets. Deriving
the drift entry from `apply_branch` would leave a production drift job presenting
`@refs/heads/develop` against a policy trusting `@refs/heads/main`, able to assume
nothing at all. Production sets `drift_branch = main` and the
workflow earns it: the develop-scheduled run dispatches
`terraform_drift.yml --ref main`, and the production leg runs from there.

The environment's own deployment branch policy is a second, independent control
on the same thing, and it is the one that gates the *secrets* rather than AWS.
Keep `aws-prod` restricted to **`main` alone** — a GitHub environment's secrets
are readable by any workflow that names the environment and runs on an admitted
branch, before it makes any AWS call, so admitting the default branch would hand
the production secrets to every workflow merged to develop. `aws-stag` must stay
open so PRs can plan; that exposure is recorded in `../README.md`.

## The plan role reads one secret, on purpose

`ReadOnlyAccess` withholds `secretsmanager:GetSecretValue` — AWS excludes it
because it returns secret material. `terraform plan` nonetheless refreshes
`module.flip_api_secret`'s `aws_secretsmanager_secret_version`, so without an
explicit grant every plan fails with `AccessDeniedException` before emitting any
diff. `plan_read_flip_api_secret` grants it, scoped to that one secret.

The secret is encrypted with the FLIP application CMK, so the read also needs
`kms:Decrypt` — without it `GetSecretValue` still fails, with the much less
obvious `Access to KMS is not allowed`. The key is scoped by a `kms:ViaService`
condition rather than by ARN: resolving `alias/flip-app-key` here would make this
bootstrap fail to apply until the FLIP root exists, and the ordering runs the other
way — in a new account these roles must exist before CI can apply anything. The
effective boundary is the intersection of the two statements: decrypt only
through Secrets Manager in this region, and only `FLIP_API` is readable.

It does not widen what the role can see: planning requires reading the state
object, and state already stores the same `AES_KEY_BASE64` and internal service
key in clear. The role still cannot write anything.

## What bounds the apply role

`PowerUserAccess` is everything except IAM, and the FLIP root owns IAM roles
(`iam_ecs.tf`, `rds_proxy.tf`, `security.tf`, and the two EC2 roles in `main.tf`),
so the apply role needs IAM write. Four separate limits keep that from being
`AdministratorAccess` under another name:

1. **A permissions boundary.** `iam:CreateRole` and `iam:PutRolePolicy` are
   granted only under an `iam:PermissionsBoundary` condition naming
   `AICentre-FLIPTerraformBoundary`, so a role the pipeline mints is capped at
   what the pipeline itself holds and can never be given IAM write.
2. **A managed-policy allowlist.** `iam:AttachRolePolicy` additionally carries an
   `iam:PolicyARN` condition naming the three AWS-managed policies the FLIP root
   actually attaches, so `AttachRolePolicy AdministratorAccess` is denied.
3. **Scoped escalation primitives.** `iam:PassRole` and
   `iam:UpdateAssumeRolePolicy` — the two verbs that make a role usable by
   something else — are restricted to the eight roles the FLIP root owns, all of
   which have literal names (`var.managed_role_names`).
4. **Two Denies.** One on both CI roles, so an apply cannot re-trust or re-permit
   itself; one on the boundary policy, so it cannot raise its own ceiling.

**What this still does not prevent, stated plainly rather than claimed away:** an
apply can create a role that trusts an external principal and give it everything
under the boundary — roughly PowerUser. It cannot exceed itself, but it can lend
itself out. The control for that is the same one that authorises the apply at
all: review on the environment's branch, plus the trust policy pinning
`job_workflow_ref` to `terraform_apply.yml` at that branch. Adding a role to the
FLIP root means adding its name to `managed_role_names` and applying the bootstrap
*first* — deliberate coupling, so a human is in the loop on every new principal
the pipeline can hand to a service.

## Debugging an AssumeRole denial

`Not authorized to perform sts:AssumeRoleWithWebIdentity` carries no detail about which condition failed. Compare
`make output` (`expected_oidc_sub`, `expected_apply_job_workflow_ref`, `plan_job_workflow_refs`) against the job: it
must declare the GitHub environment (the `sub` claim carries the environment, not the branch), and the workflow file
and branch must match `job_workflow_ref` exactly.
