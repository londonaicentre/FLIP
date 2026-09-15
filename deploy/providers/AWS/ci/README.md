# Terraform CI roles

GitHub Actions OIDC roles for the FLIP Terraform pipeline (FLIP#962). Two roles
per AWS account:

| Role | Assumed by | Permissions |
| --- | --- | --- |
| `AICentre-FLIPTerraformPlanRole` | PR plans (stag only), nightly drift | `ReadOnlyAccess`, plus an explicit `Deny` on every write to the state bucket |
| `AICentre-FLIPTerraformApplyRole` | Applies on the environment's branch | `PowerUserAccess`, plus IAM write bounded four ways (below) |

There is also a third object: `AICentre-FLIPTerraformBoundary`, the permissions
boundary. It is declared here and *carried* by every IAM role the FLIP root owns
(`iam_permissions_boundary_name` in `../variables.tf`).

## Applying

From a laptop, per account. **Never from CI** — that is the point of the separate
state (`flip/ci/terraform.tfstate`): the role that runs Terraform must not be the
only manager of its own permissions, or a bad apply locks the pipeline out of the
apply that would fix it.

```bash
make init && make plan && make apply                          # stag
make init PROD=true && make plan PROD=true && make apply PROD=true   # prod
```

**Order matters when the boundary is introduced or renamed.** `iam:PutRolePolicy`
and `iam:AttachRolePolicy` are conditioned on the *target role's current*
boundary, so a role that does not yet carry one cannot be given an inline policy
by the pipeline. Terraform gets this right on its own — `aws_iam_role_policy`
depends on the role, so `PutRolePermissionsBoundary` runs first — but it means the
first apply after this change must be run somewhere it can be watched, and that
`make -C ci apply` has to land in each account **before** an apply that creates a
new role there.

`make output` prints the two ARNs and the OIDC claims they expect. Put the ARNs
on the matching GitHub environment (`aws-stag` / `aws-prod`) as the variables
`TF_PLAN_ROLE_ARN` and `TF_APPLY_ROLE_ARN`. `account_id` is also an output —
check it against the intended account before wiring anything up.

## The OIDC claims, and the two easy mistakes

**`sub` carries the environment, not the ref.** When a job declares
`environment: aws-prod`, GitHub mints `sub` as
`repo:londonaicentre/FLIP:environment:aws-prod`. It does **not** contain the
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
the drift entry from `apply_branch` therefore left the production drift job
presenting `@refs/heads/develop` against a policy trusting `@refs/heads/main`, and
it could assume nothing at all. Production sets `drift_branch = main` and the
workflow earns it: the develop-scheduled run dispatches
`terraform_drift.yml --ref main`, and the production leg runs from there.

The environment's own deployment branch policy is a second, independent control
on the same thing, and it is the one that gates the *secrets* rather than AWS.
Keep `aws-prod` restricted to **`main` alone** — a GitHub environment's secrets
are readable by any workflow that names the environment and runs on an admitted
branch, before it makes any AWS call, so admitting the default branch would hand
the production secrets to every workflow merged to develop. `aws-stag` must stay
open so PRs can plan; that exposure is recorded in `../README.md`.

## Debugging an AssumeRole denial

`Not authorized to perform sts:AssumeRoleWithWebIdentity` comes with no detail
about which condition failed. Compare what the role expects against what the
token carried:

```bash
make output          # expected_oidc_sub, expected_apply_job_workflow_ref
```

Then check the job actually declared the environment, and that the workflow file
name and branch match the pinned `job_workflow_ref` exactly.

## Notes

- The OIDC provider is looked up with `data`, never declared. It already exists
  in both FLIP accounts (it backs `GitHubAction-AssumeRoleWithAction-FLIP`, used
  by the XNAT image build). A `resource` would fail with `EntityAlreadyExists`,
  and a later `destroy` of this root would delete a provider other workflows
  depend on.
- These roles are FLIP-scoped and deliberately **not** joined to the LZA
  cross-account chain. The LZA management role trusts `repo:${org}/${repo}:*`
  with `AdministratorAccess`; adding FLIP to it would grant org-wide admin to
  every branch of a public repository.
- `ReadOnlyAccess` on the plan role is a one-hour read session over the whole
  account, not just the four inputs the plan needs: state, the Cognito user list,
  CloudWatch log groups, every ECS task definition and its environment. That is
  not incidental — `terraform plan` cannot run without reading state, and state
  holds `AES_KEY_BASE64` and the internal service key in the clear either way.
  The containment is that the role cannot write anything.

  It does **not** extend to the KMS-encrypted buckets (model files, FL
  participant kits, FL results). `ReadOnlyAccess` carries no `kms:Decrypt`, and
  the grant added here is pinned by a `kms:ViaService` condition to Secrets
  Manager in one region — so a `GetObject` on those buckets fails at the KMS step.
  It is worth being precise in both directions: the read surface is wider than
  four secrets, and narrower than "every S3 object".
- For the LZA cutover (#749): apply this root into the new account and repoint
  `TF_PLAN_ROLE_ARN` / `TF_APPLY_ROLE_ARN` / `FLIP_TFSTATE_BUCKET_NAME` on the
  GitHub environment. No workflow change — no account ID or ARN is hard-coded in
  workflow YAML. Two *scripts* do default the state bucket to
  `flip-terraform-state-<env>`, which the cutover keeps; if a new account ever
  uses another name, override it (`CI_STATE_BUCKET=` for
  `scripts/setup-github-environments.sh`, `--bucket` for
  `scripts/reconcile_ci_env.py`).

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
root fail to apply until the FLIP root exists, and the ordering runs the other
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
   `iam:PolicyARN` condition naming the three AWS-managed policies this root
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
FLIP root means adding its name to `var.managed_role_names` and re-applying this
root from a laptop *first* — deliberate coupling, so a human is in the loop on
every new principal the pipeline can hand to a service.

**Re-apply this root after pulling a change to it** — `make -C ci apply` for
stag, `make -C ci apply PROD=true` for prod. The roles are not managed by the
pipeline they authorise, so a policy change here reaches AWS only when an
operator applies it from a laptop.
