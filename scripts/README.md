# Secret Scanning Scripts

This directory contains scripts for detecting secrets and sensitive information in the FLIP monorepo.

## Quick Start

### Prerequisites

This monorepo uses `uv` for Python package management. If you haven't installed it yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Initial Setup

Run the setup script once to install and configure all secret scanning tools:

```bash
./scripts/setup-secret-scanning.sh
```

This will:

- Install `pre-commit`, `detect-secrets`, `trufflehog`, and `gitleaks`
- Set up pre-commit hooks
- Create a baseline for detect-secrets
- Update .gitignore to exclude security reports

### Running Scans

#### Full Monorepo Scan

To scan the entire monorepo for secrets:

```bash
./scripts/scan-secrets.sh
```

#### Pre-commit Checks

Check staged files before committing:

```bash
pre-commit run
```

Check all files:

```bash
pre-commit run --all-files
```

**Available Pre-commit Hooks:**

- **check-env-vars**: Verifies that all variables in `.env.development.example` exist in `.env.development`. This ensures the example file stays up to date and developers are aware of new required environment variables.
- **gitleaks**: Detects secrets and sensitive information
- **trufflehog**: Scans for high-entropy strings and verified secrets
- **detect-secrets**: Pattern-based secret detection with baseline support
- **check-added-large-files**: Prevents committing files larger than 1MB
- **check-merge-conflict**: Detects merge conflict markers
- **check-yaml**: Validates YAML syntax
- **end-of-file-fixer**: Ensures files end with a newline
- **trailing-whitespace**: Removes trailing whitespace
- **detect-private-key**: Detects private SSH/SSL keys

#### Component-Specific Scans

To scan specific components (flip-api, trust, etc.):

```bash
cd flip-api
trufflehog git file://. --only-verified
```

## Emergency: Remove Secrets from Git History

If secrets were accidentally committed, use the cleanup script:

```bash
./scripts/remove-env-from-history.sh
```

⚠️ **WARNING**: This rewrites git history. Only use when absolutely necessary.

This script will:

- Remove `.env.development` from all git history
- Update `.gitignore` to prevent future commits
- Create a backup branch before making changes
- Provide instructions for force-pushing and team coordination

**See [SECURITY_INCIDENT.md](../SECURITY_INCIDENT.md) for complete incident response procedures.**

## Tools Used

### TruffleHog

High-entropy string and secret scanner with built-in detector patterns.

**Direct usage:**

```bash
trufflehog git file://. --only-verified --json
```

### Gitleaks

Fast secret scanner with customizable rules.

**Direct usage:**

```bash
gitleaks detect --source . --verbose
```

### detect-secrets

Pattern-based scanner with baseline support.

**Direct usage:**

```bash
detect-secrets scan --all-files
```

## CI/CD Integration

Secret scanning runs automatically on:

- Push to `main` or `develop` branches
- Pull requests
- Weekly schedule (Sundays at 2 AM UTC)

See [.github/workflows/secret-scanning.yml](../.github/workflows/secret-scanning.yml) for details.

## Managing False Positives

### detect-secrets Baseline

To update the baseline with new false positives:

```bash
detect-secrets scan --baseline .secrets.baseline --update
```

To audit the baseline:

```bash
detect-secrets audit .secrets.baseline
```

### Gitleaks Configuration

Create a `.gitleaks.toml` file in the repo root to customize Gitleaks rules and add allowlists.

## Troubleshooting

### uv Not Found

This project uses `uv` for Python package management. Install it with:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installation, ensure `~/.local/bin` is in your PATH:

```bash
# Add to your ~/.bashrc, ~/.zshrc, or ~/.config/fish/config.fish
export PATH="$HOME/.local/bin:$PATH"
```

### Commands Not Found After Installation

If `pre-commit` or `detect-secrets` aren't found after running the setup script, the uv tool directory may not be in your PATH. Add it:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Or reinstall the tools globally via Homebrew:

```bash
brew install pre-commit gitleaks
pip install detect-secrets  # Only if using pipx or virtual environment
```

### TruffleHog Not Found

Install via Homebrew:

```bash
brew install trufflesecurity/trufflehog/trufflehog
```

Or use Docker:

```bash
docker pull trufflesecurity/trufflehog:latest
```

### Gitleaks Not Found

Install via Homebrew:

```bash
brew install gitleaks
```

Or download from: <https://github.com/gitleaks/gitleaks/releases>

### Pre-commit Hook Failures

If pre-commit hooks fail due to missing tools:

1. Ensure tools are installed: `which trufflehog gitleaks`
2. Reinstall pre-commit hooks: `pre-commit install`
3. Update pre-commit: `pre-commit autoupdate`

## Best Practices

1. **Run scans before committing**: Use `pre-commit run` to catch secrets early
2. **Review scan results carefully**: Not all findings are real secrets
3. **Never commit real secrets**: Use environment variables or secret management
4. **Update baselines regularly**: Keep false positive baselines current
5. **Scan the entire history**: Use full scans periodically to find historical secrets

## Security Reports

Scan reports are saved to `.security-reports/` (gitignored). Review these carefully before sharing or opening issues.

## Support

For issues or questions about secret scanning:

- Check tool documentation: [TruffleHog](https://github.com/trufflesecurity/trufflehog), [Gitleaks](https://github.com/gitleaks/gitleaks), [detect-secrets](https://github.com/Yelp/detect-secrets)
- Review [CONTRIBUTING.md](../CONTRIBUTING.md) for security guidelines
