# Environment Variable Validation Pre-commit Hook

## Overview

This pre-commit hook ensures that the `.env.development.example` file stays synchronized with `.env.development` by verifying that all variables defined in the example file are present in the development environment file.

## How It Works

1. **Trigger**: The hook runs automatically on commits that modify either `.env.development` or `.env.development.example`.

2. **Validation**: The script ([scripts/check_env_vars.py](../scripts/check_env_vars.py)) extracts all variable names from both files and compares them.

3. **Results**:
   - ✅ **Pass**: If all variables from the example file are present in the development file
   - ❌ **Fail**: If any variables are missing, with a helpful error message listing what needs to be added

## Usage

### Automatic (Pre-commit)

The hook runs automatically when you commit changes to `.env.development` or `.env.development.example`:

```bash
git add .env.development.example
git commit -m "Add new environment variable"
```

If validation fails, you'll see an error like:

```
❌ ERROR: Environment variable validation failed!

   The following variables are defined in .env.development.example
   but are missing from .env.development:

     • NEW_VARIABLE_NAME

   ACTION REQUIRED:
   1. Review the new variables in .env.development.example
   2. Add the missing variables to .env.development
   3. Set appropriate values for your local environment
```

### Manual Testing

Run the validation script directly:

```bash
python3 scripts/check_env_vars.py
```

Or run just this specific pre-commit hook:

```bash
pre-commit run check-env-vars --all-files
```

## Why This Matters

1. **Consistency**: Ensures all developers have the same environment variables configured
2. **Documentation**: Keeps the example file accurate as a reference for new team members
3. **Error Prevention**: Catches missing environment variables before runtime errors occur
4. **Onboarding**: New developers can easily see what environment variables need to be configured

## Maintaining the Files

### Adding a New Variable

When adding a new environment variable:

1. Add it to `.env.development.example` with a placeholder value
2. Add it to your local `.env.development` with your actual value
3. Commit `.env.development.example` (but never commit `.env.development`)
4. The pre-commit hook will validate that both files are synchronized

### Example

```bash
# 1. Add to .env.development.example
echo "NEW_API_KEY=<your-api-key>" >> .env.development.example

# 2. Add to .env.development
echo "NEW_API_KEY=actual-secret-key-value" >> .env.development

# 3. Commit the example (the hook will validate before committing)
git add .env.development.example
git commit -m "Add NEW_API_KEY environment variable"
```

## Configuration

The pre-commit hook is configured in [`.pre-commit-config.yaml`](../.pre-commit-config.yaml):

```yaml
- repo: local
  hooks:
    - id: check-env-vars
      name: Verify .env.development variables
      entry: python3 scripts/check_env_vars.py
      language: system
      pass_filenames: false
      files: ^\.env\.development(\.example)?$
      always_run: false
```

## Troubleshooting

### Hook Not Running

If the hook doesn't run when expected:

```bash
# Reinstall pre-commit hooks
pre-commit install

# Test manually
pre-commit run check-env-vars --all-files
```

### Script Errors

If the script itself has issues:

```bash
# Run the script directly to see detailed errors
python3 scripts/check_env_vars.py

# Check that both files exist
ls -la .env.development .env.development.example
```

### False Positives

If you see variables reported as missing but they exist:

1. Ensure the variable name in `.env.development` exactly matches `.env.development.example`
2. Check for typos or extra spaces
3. Variable names must start with a letter or underscore and contain only letters, numbers, and underscores
4. Variable assignments must use the format: `VARIABLE_NAME=value`

## Related Documentation

- [scripts/README.md](../scripts/README.md) - Overview of all pre-commit hooks
- [SECURITY_INCIDENT.md](../SECURITY_INCIDENT.md) - Security incident response procedures
- [Pre-commit Framework](https://pre-commit.com/) - Official pre-commit documentation
