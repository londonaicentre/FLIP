# Terraform CI roles

GitHub Actions OIDC roles for the FLIP Terraform pipeline (FLIP#962). Two roles
per AWS account:

| Role | Assumed by | Permissions |
| --- | --- | --- |
| `AICentre-FLIPTerraformPlanRole` | PR plans, nightly drift | `ReadOnlyAccess`, plus an explicit `Deny` on state writes |
| `AICentre-FLIPTerraformApplyRole` | Applies on the environment's branch | `PowerUserAccess`, the IAM actions `iam_ecs.tf` needs, state read/write — and an explicit `Deny` on changing either CI role |

## Applying

From a laptop, per account. **Never from CI** — that is the point of the separate
state (`flip/ci/terraform.tfstate`): the role that runs Terraform must not be the
only manager of its own permissions, or a bad apply locks the pipeline out of the
apply that would fix it.

```bash
make init && make plan && make apply                          # stag
make init PROD=true && make plan PROD=true && make apply PROD=true   # prod
```

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
- the plan role accepts `terraform_plan.yml@refs/pull/*/merge`,
  `terraform_plan.yml@refs/heads/<branch>` and
  `terraform_drift.yml@refs/heads/<branch>`, and can only read.

The environment's own deployment branch policy is a second, independent control
on the same thing. Keep `aws-prod` restricted to `main` (plus the default branch,
so the nightly drift job can plan); `aws-stag` must stay open so PRs can plan.

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
- `ReadOnlyAccess` on the plan role does let it read every S3 object in the
  account, state included. That is not incidental: `terraform plan` cannot run
  without reading state, and state holds `AES_KEY_BASE64` and the DB credentials
  either way. The containment is that the role cannot write.
- For the LZA cutover (#749): apply this root into the new account and repoint
  `TF_PLAN_ROLE_ARN` / `TF_APPLY_ROLE_ARN` / `FLIP_TFSTATE_BUCKET_NAME` on the
  GitHub environment. No workflow change — no account ID or ARN is hard-coded in
  workflow YAML.
