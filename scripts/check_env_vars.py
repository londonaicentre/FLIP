#!/usr/bin/env python3
"""
Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at
    http://www.apache.org/licenses/LICENSE-2.0
Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

Pre-commit hook to verify that all variables in .env.development.example
are present in .env.development.

This ensures that the example file is kept up to date and developers
are aware of any new environment variables that need to be configured.
"""

import re
import sys
from pathlib import Path


def extract_variable_names(file_path: Path) -> set[str]:
    """
    Extract environment variable names from a .env file.

    Args:
        file_path: Path to the .env file

    Returns:
        Set of variable names found in the file
    """
    if not file_path.exists():
        return set()

    variables = set()
    # Pattern to match variable assignments, ignoring comments and blank lines
    pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)=", re.MULTILINE)

    try:
        content = file_path.read_text()
        for match in pattern.finditer(content):
            variables.add(match.group(1))
    except Exception as e:
        print(f"Error reading {file_path}: {e}", file=sys.stderr)
        return set()

    return variables


def main() -> int:
    """
    Main function to validate environment variables.

    Returns:
        0 if validation passes, 1 if validation fails
    """
    # Get the repository root directory
    repo_root = Path(__file__).parent.parent

    example_file = repo_root / ".env.development.example"
    dev_file = repo_root / ".env.development"

    # Check if example file exists
    if not example_file.exists():
        print(f"❌ ERROR: {example_file} not found!", file=sys.stderr)
        return 1

    # Extract variables from both files
    example_vars = extract_variable_names(example_file)
    dev_vars = extract_variable_names(dev_file)

    if not example_vars:
        print(f"⚠️  WARNING: No variables found in {example_file}", file=sys.stderr)
        return 0

    # Check if .env.development exists
    if not dev_file.exists():
        print(f"❌ ERROR: {dev_file} not found!", file=sys.stderr)
        print(f"   Please create {dev_file} based on {example_file}", file=sys.stderr)
        return 1

    # Find missing variables
    missing_vars = example_vars - dev_vars

    if missing_vars:
        print("❌ ERROR: Environment variable validation failed!", file=sys.stderr)
        print(file=sys.stderr)
        print(f"   The following variables are defined in {example_file.name}", file=sys.stderr)
        print(f"   but are missing from {dev_file.name}:", file=sys.stderr)
        print(file=sys.stderr)
        for var in sorted(missing_vars):
            print(f"     • {var}", file=sys.stderr)
        print(file=sys.stderr)
        print("   This indicates that .env.development.example has been updated", file=sys.stderr)
        print("   with new required variables.", file=sys.stderr)
        print(file=sys.stderr)
        print("   ACTION REQUIRED:", file=sys.stderr)
        print(f"   1. Review the new variables in {example_file.name}", file=sys.stderr)
        print(f"   2. Add the missing variables to {dev_file.name}", file=sys.stderr)
        print("   3. Set appropriate values for your local environment", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    # Optional: Check for extra variables in dev file (informational only)
    extra_vars = dev_vars - example_vars
    if extra_vars:
        print(f"ℹ️  INFO: The following variables exist in {dev_file.name}", file=sys.stderr)
        print(f"   but not in {example_file.name}:", file=sys.stderr)
        for var in sorted(extra_vars):
            print(f"     • {var}", file=sys.stderr)
        print("   Consider adding them to the example file if they should be documented.", file=sys.stderr)
        print(file=sys.stderr)

    print("✅ Environment variable validation passed!")
    print(f"   All {len(example_vars)} variables from {example_file.name} are present in {dev_file.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
