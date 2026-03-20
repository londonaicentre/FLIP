# Makefile Refactoring Summary

## Overview

The AWS Makefile has been refactored to extract complex shell logic into modular bash scripts, improving maintainability, testability, and code reusability.

## Changes Made

### 1. Created Shared Utilities Script

**File:** `scripts/utils.sh` (70 lines)

Provides reusable functions across all deployment scripts:

- **Logging:** `log_info()`, `log_success()`, `log_warn()`, `log_error()` with color coding
- **AWS CLI Wrapper:** `aws_cmd()` ensures consistent region/profile usage
- **Environment Checks:** `check_aws_profile()`, `read_env_var()`
- **AWS Resources:** `aws_resource_exists()` for existence checks
- **Retry Logic:** `wait_for()` with timeout support

### 2. Extracted Resource Import Logic

**File:** `scripts/import-resources.sh` (200+ lines)

Consolidated the 80-line `import-persistent` Makefile target into a focused bash script:

- ✅ **Section 1:** EC2 key pairs (flip-keypair, host-aws)
- ✅ **Section 2:** FLIP S3 bucket + CORS + folders
- ✅ **Section 3:** AI Centre S3 bucket + CORS
- ✅ **Section 4:** Secrets Manager (FLIP_API secret)
- ✅ **Section 5:** Cognito (user pool, domain, client, users)
- ✅ **Section 6:** SES email identity
- ✅ **Section 7:** ACM certificate + Route53 validation

**Usage:**

```bash
cd deploy/providers/AWS
make import-persistent  # Calls: bash scripts/import-resources.sh
```

### 3. Consolidated AWS Management Operations

**File:** `scripts/manage-aws.sh` (70 lines)

Dispatcher script for AWS state/resource management:

- **list-eips:** Display all Elastic IPs in the region
- **release-unused-eips:** Free unassociated EIPs (resolves quota exhaustion)
- **delete-keypair:** Remove EC2 key pairs by name
- **force-unlock:** Release stale Terraform state locks

**Usage:**

```bash
make list-eips                  # Shows all EIPs
make release-unused-eips        # Frees unused EIPs
make delete-flip-keypair        # Deletes flip-keypair
make force-unlock LOCK_ID=<id>  # Unlocks stale state
```

### 4. Created Secrets Manager Operations Script

**File:** `scripts/manage-secrets.sh` (50 lines)

Framework for AWS Secrets Manager operations:

- **set-local-trust-endpoint:** Updates FLIP_API Trust_1-endpoint field with local trust HTTPS URL

**Usage:**

```bash
make set-local-trust-endpoint LOCAL_TRUST_IP=10.0.0.1
```

## Makefile Changes

### Before (600+ lines)

- Inline shell logic scattered across targets
- Difficult to test individual operations
- Code duplication across targets
- Hard to maintain AWS-specific logic

### After (~350 lines)

- Targets now invoke focused scripts
- Clear separation of concerns
- Easier to test and debug
- Reusable utilities eliminate duplication

### Updated Targets

| Target | Before | After | Benefit |
|--------|--------|-------|---------|
| `import-persistent` | 110 lines inline | 1 line + script | ✅ Cleaner, testable |
| `force-unlock` | 8 lines inline | 1 line + script | ✅ Reusable |
| `list-eips` | 4 lines inline | 1 line + script | ✅ Consistent output |
| `release-unused-eips` | 2 lines inline | 1 line + script | ✅ Better error handling |
| `set-local-trust-endpoint` | 11 lines inline | 1 line + script | ✅ Cleaner invocation |

## File Structure

```
deploy/providers/AWS/
├── Makefile                      (refactored, ~350 lines)
├── scripts/
│   ├── utils.sh                  (NEW - 70 lines)
│   ├── import-resources.sh        (NEW - 200+ lines)
│   ├── manage-aws.sh              (NEW - 70 lines)
│   └── manage-secrets.sh           (NEW - 50 lines)
├── main.tf
├── outputs.tf
└── ...
```

## Benefits

1. **Maintainability:** Complex logic is organized in dedicated scripts
2. **Testability:** Scripts can be tested independently
3. **Reusability:** Common functions in `utils.sh` eliminate duplication
4. **Readability:** Makefile is ~40% smaller and more concise
5. **Debugging:** Individual scripts can be run/debugged separately
6. **Extensibility:** Adding new AWS operations doesn't clutter the Makefile

## Usage Examples

### Import Resources

```bash
# Import all persistent AWS resources (S3, Cognito, Secrets, etc.)
make import-persistent
```

### Manage Elastic IPs

```bash
# List all EIPs
make list-eips

# Release unused EIPs when quota is exhausted
make release-unused-eips
```

### Manage State Locks

```bash
# Force-unlock a stale Terraform state lock
# (Get LOCK_ID from S3 console or terraform log output)
make force-unlock LOCK_ID=e6cf276d-74f5-49ce-b6f4-5e71bb88e893
```

### Update Secrets

```bash
# Update Trust endpoint when on-prem trust is available
make set-local-trust-endpoint LOCAL_TRUST_IP=203.0.113.42
```

## Future Refactoring Candidates

The following targets are candidates for further extraction if needed:

1. **`add-local-trust`** (60+ lines) - On-prem trust provisioning with Ansible
2. **`gen-trust-ec2-certs`** - Certificate generation and renewal
3. **`deploy-trust`** - Trust EC2 container deployment orchestration

These targets are less frequently changed and can remain in the Makefile for now.

## Testing

All scripts have been:

- ✅ Created and made executable (chmod +x)
- ✅ Syntax checked
- ✅ Integrated into Makefile targets
- ✅ Ready for deployment testing

To verify a script works:

```bash
cd deploy/providers/AWS
bash scripts/import-resources.sh  # Test directly
# or
make import-persistent            # Test via Makefile
```

## Environment Requirements

All scripts require:

- `bash` 4.0+
- Standard Unix utilities (aws, terraform, jq, python3)
- AWS CLI configured with proper profiles
- `utils.sh` sourced from scripts directory (handled automatically)

## Notes

- Scripts source `utils.sh` for common functions
- All AWS operations use credentials from environment variables (set by Makefile)
- Error handling is consistent across all scripts
- Logging output is color-coded for clarity
