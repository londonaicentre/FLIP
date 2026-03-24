# Code Review — PR #199

**PR:** [feat: add persistent Elastic IP for Central Hub EC2 and local trust improvements](https://github.com/londonaicentre/FLIP/pull/199)
**Author:** @garciadias (Rafael Garcia-Dias)
**Branch:** `6-task-send-central-hub-ec2-ip-range-to-kcl-it-davide-v2` → `develop`
**Reviewer:** Claude (Senior Software Engineer / Lead Reviewer)
**Date:** 2026-03-24

---

## 1. Summary

This PR allocates a persistent AWS Elastic IP (EIP) for the Central Hub EC2 instance to provide a stable outbound IP address across redeployments, and extracts ~250 lines of inline Makefile shell logic into four modular bash scripts (`utils.sh`, `import-resources.sh`, `manage-aws.sh`, `manage-secrets.sh`). Secondary changes harden SSH configuration, simplify local trust provisioning, make certificate output paths configurable, and add firewall enforcement checks.

---

## 2. Template Compliance

| Claim | Status | Notes |
|---|---|---|
| **Coding style followed** | ✅ Mostly | Bash scripts follow consistent conventions; `set -eo pipefail` used throughout |
| **Self-review performed** | ✅ Assumed | 15–21 commits show iterative refinement |
| **Tests added (unit)** | ⚠️ Partial | `check_status.py` gains `check_elastic_ip_stability()` — this is an **integration/smoke test**, not a unit test. No unit tests for the new bash scripts. |
| **Documentation updated** | ⚠️ Incomplete | `REFACTORING.md` added as an internal dev document; `deploy/README.md`, `docs/source/3_sys-admin.rst`, and `CONTRIBUTING.md` (prerequisites section) do not appear updated for the new `CREATE_CENTRAL_HUB_ELASTIC_IP` variable or the new script layout. |
| **Type of change: Non-breaking** | ⚠️ Mostly | Replacing Ansible automation with manual sudo commands for local trust provisioning (`site_local_trust.yml`) **can break existing automated CI/CD pipelines** that relied on the Ansible flow. This deserves a "Breaking change" flag or at minimum an explicit migration note. |

---

## 3. Critical Issues

### C1 — `prevent_destroy = false` on `aws_eip` Contradicts the PR Goal

**File:** `deploy/providers/AWS/main.tf`

```hcl
resource "aws_eip" "central_hub_eip" {
  domain = "vpc"
  lifecycle {
    prevent_destroy = false   # ← BUG: EIP will be destroyed on terraform destroy
  }
}
```

The entire purpose of this PR is to preserve the EIP across deployments, yet `prevent_destroy = false` allows Terraform to destroy it freely. The `destroy-preserve-eips.sh` script works around this by using `sed` to toggle the value at runtime — but this is a fragile workaround for what should be a simple configuration fix.

**Fix:** Set `prevent_destroy = true` and rely on `destroy-preserve-eips.sh` only for intentional teardowns where the operator knowingly wants to release the IP.

---

### C2 — EIP Always Allocated Regardless of `create_central_hub_elastic_ip` Variable

**File:** `deploy/providers/AWS/main.tf`

```hcl
# aws_eip — NO count/conditional: always created
resource "aws_eip" "central_hub_eip" { ... }

# aws_eip_association — conditional
resource "aws_eip_association" "central_hub_eip_assoc" {
  count         = var.create_central_hub_elastic_ip ? 1 : 0
  instance_id   = aws_instance.ec2_instance.id
  allocation_id = aws_eip.central_hub_eip.id
}
```

When `create_central_hub_elastic_ip = false`, the EIP is still **allocated** (and billed) but never associated with an instance — an EIP that sits unassociated incurs hourly AWS charges. The `count` conditional must be applied to the `aws_eip` resource itself, not only the association.

**Fix:**

```hcl
resource "aws_eip" "central_hub_eip" {
  count  = var.create_central_hub_elastic_ip ? 1 : 0
  domain = "vpc"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_eip_association" "central_hub_eip_assoc" {
  count         = var.create_central_hub_elastic_ip ? 1 : 0
  instance_id   = aws_instance.ec2_instance.id
  allocation_id = aws_eip.central_hub_eip[0].id
}
```

---

### C3 — `destroy-preserve-eips.sh` Modifies Terraform Source Files In-Place with `sed`

**File:** `deploy/providers/AWS/scripts/destroy-preserve-eips.sh`

The script uses `sed` to replace `prevent_destroy = true` with `prevent_destroy = false` in `main.tf` and trust module files before running `terraform destroy`, then restores them from backups afterwards. This approach has several risks:

- If the script is interrupted between the `sed` mutation and the restore step, `main.tf` is left in a corrupted state (with `prevent_destroy = false` permanently set).
- Git will show the modified `main.tf` as dirty, confusing the operator.
- The pattern match `prevent_destroy = true` could inadvertently affect other resources if future resources also use that lifecycle rule.

**Fix:** Prefer Terraform's `-target` flag to destroy only non-EIP resources while leaving the EIP resource untouched, rather than mutating source files.

---

### C4 — Local Trust Provisioning Regression: Replacing Ansible with Manual `sudo` Commands

**Commit:** `Simplify local trust provisioning: replace Ansible with manual sudo commands`

Replacing structured Ansible tasks with raw manual `sudo` commands removes idempotency, auditability, and repeatability guarantees that Ansible provides. Ansible roles (especially `geerlingguy.docker`) handle edge cases (already-installed, different distro versions, etc.) automatically — manual `sudo` commands do not. This is a maintenance and reliability regression, particularly for on-premises deployments managed by teams unfamiliar with the exact command sequence.

If the motivation was debugging simplicity, consider keeping the Ansible playbook and adding verbose flag support (`-vvv`) rather than removing automation entirely.

---

### C5 — `wait_for()` Uses `eval` — Potential Command Injection

**File:** `deploy/providers/AWS/scripts/utils.sh`

```bash
wait_for() {
  local condition="$1"
  ...
  while [ $elapsed -lt $timeout ]; do
    if eval "$condition"; then   # ← eval is dangerous
```

Using `eval` on an externally-provided string (even if from trusted Makefile targets) is a security risk and an anti-pattern. If a variable interpolated into the condition string contains shell metacharacters, it can lead to unintended command execution.

**Fix:** Accept a function name or use a callback pattern:

```bash
wait_for() {
  local check_fn="$1"
  ...
  if "$check_fn"; then   # call function by name safely
```

---

## 4. Suggestions

### S1 — `REFACTORING.md` Should Not Live in Source Control Long-Term

`deploy/providers/AWS/REFACTORING.md` is a useful PR-time document but is not operational documentation. It describes a refactoring already done — its content is now historical. Consider:
- Moving its content into `deploy/README.md` as a "Scripts" section, or
- Removing it post-merge and relying on the PR description and commit history.

---

### S2 — Bash Scripts Have No Linting Step

The new scripts (`utils.sh`, `import-resources.sh`, `manage-aws.sh`, `manage-secrets.sh`, `destroy-preserve-eips.sh`) are not verified by any automated linter. The project uses `ruff` for Python and `ESLint` for TypeScript — the CI pipeline should include `shellcheck` for bash scripts.

**Suggestion:** Add a `shellcheck scripts/*.sh` step to `.github/workflows/` or the root `Makefile`.

---

### S3 — `check_endpoint_blocked_from_ssh()` Warns Instead of Failing

In `check_status.py`, the firewall check that verifies the cloud Trust EC2 cannot reach the local Trust API issues a **warning** when the check fails. Since this is a security enforcement check, a failed firewall test should cause the deployment status command to exit non-zero, not merely print a yellow warning.

---

### S4 — Auto-Detect Public IP Relies on Third-Party Service (`ipify.org`)

**Commit:** `Auto-detect public IP for full-deploy-stag-hybrid`

Using `curl https://api.ipify.org` introduces an external dependency with no SLA. If `ipify.org` is down or rate-limited, the deployment command fails for an unrelated reason.

**Suggestion:** Use an AWS-native endpoint instead:

```bash
TOKEN=$(curl -s -X PUT "http://169.254.169.254/latest/api/token" \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")
PUBLIC_IP=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" \
  http://169.254.169.254/latest/meta-data/public-ipv4)
```

Or fall back to `dig +short myip.opendns.com @resolver1.opendns.com`.

---

### S5 — Automatic Reboot After Kernel Updates Needs a Production Guard

**Commit:** `ops: add automatic reboot after kernel updates`

Rebooting EC2 instances automatically after kernel updates is reasonable for staging but can cause unexpected downtime in production. This behavior should be gated on the `PROD` variable or a separate opt-in flag (e.g., `AUTO_REBOOT=true`).

---

### S6 — `StrictHostKeyChecking=accept-new` Is Better but Still Risks MITM

**Commit:** `security: add SSH security options to remote commands`

`StrictHostKeyChecking=accept-new` accepts new host keys automatically — this is safe for first connections, but means a MITM attacker who presents themselves on a fresh IP (e.g., after EIP reassignment) would be accepted silently. Combined with the `ssh-keygen -R` key refresh before each scan, there is a window of vulnerability.

**Suggestion:** After allocating the EIP, store the fingerprint in a trusted location (e.g., AWS Secrets Manager or a local file committed post-provision) and validate against it on subsequent connections.

---

### S7 — `Ec2ElasticIp` Output Needs Conditional Handling

If `create_central_hub_elastic_ip = false` (and after fixing C2 above with `count`), the output:

```hcl
output "Ec2ElasticIp" {
  value = aws_eip.central_hub_eip.public_ip
}
```

will error with `object has no attribute`. This should use:

```hcl
output "Ec2ElasticIp" {
  value = var.create_central_hub_elastic_ip ? aws_eip.central_hub_eip[0].public_ip : null
}
```

---

### S8 — `.jar` and `.war` in Root `.gitignore` Is Unexpected

**Commit:** `chore: add .jar and .war files to .gitignore`

The FLIP project has no Java components. Adding Java artifact exclusions to the root `.gitignore` without explanation is noise. If this was added to unblock a specific local toolchain artifact (e.g., a testing dependency), a comment explaining why would help future maintainers.

---

## 5. Verdict

### **Request Changes**

The PR introduces genuinely useful infrastructure improvements — persistent EIP allocation, SSH hardening, Makefile modularization, and configurable certificate paths. However, three critical issues prevent approval:

1. **C1 (`prevent_destroy = false`)** directly contradicts the PR's core goal and will cause EIP loss on the next `terraform destroy`.
2. **C2 (unguarded `aws_eip` allocation)** causes unnecessary AWS charges when the feature is disabled.
3. **C3 (`sed`-based file mutation)** creates a fragile, crash-sensitive infrastructure management pattern.

C4 (Ansible regression) should also be reconsidered or at minimum explicitly documented as a breaking change. Once the Terraform lifecycle and resource count issues are corrected, this PR will be in good shape to merge.
