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

Consolidate dependencies from all sub-project pyproject.toml files
into the root pyproject.toml file.
"""

import re
import sys
from pathlib import Path
from typing import Dict, List

try:
    if sys.version_info >= (3, 11):
        import tomllib
    else:
        import tomli as tomllib
except ImportError:
    print("Error: tomli package required for Python < 3.11")
    print("Install with: uv pip install tomli")
    sys.exit(1)


def parse_dependency(dep: str) -> tuple[str, str]:
    """Parse a dependency string into (name, version_spec).

    Examples:
        "fastapi>=0.115.11" -> ("fastapi", ">=0.115.11")
        "fastapi[standard]>=0.115.11" -> ("fastapi", "[standard]>=0.115.11")
        "pyjwt[crypto]>=2.10.1" -> ("pyjwt", "[crypto]>=2.10.1")
    """
    # Match package name (including extras) and version spec
    match = re.match(r"^([a-zA-Z0-9_-]+)(\[.*?\])?(.*)", dep)
    if match:
        name = match.group(1).lower()
        extras = match.group(2) or ""
        version = match.group(3) or ""
        return name, extras + version
    return dep.lower(), ""


def compare_versions(v1: str, v2: str) -> str:
    """Compare two version specs and return the more restrictive one.

    For simplicity, we keep the newer/higher minimum version.
    """

    # Extract version numbers from strings like ">=0.115.11" or "[standard]>=0.115.11"
    def extract_version(spec: str) -> tuple:
        # Remove extras like [standard]
        spec = re.sub(r"\[.*?\]", "", spec)
        # Extract version number
        match = re.search(r"(\d+(?:\.\d+)*)", spec)
        if match:
            return tuple(map(int, match.group(1).split(".")))
        return (0,)

    ver1 = extract_version(v1)
    ver2 = extract_version(v2)

    # Return the spec with the higher version
    if ver1 >= ver2:
        return v1
    return v2


def consolidate_dependencies(root_dir: Path, sub_projects: List[str]) -> tuple[List[str], List[str]]:
    """Consolidate dependencies from sub-projects.

    Args:
        root_dir: Root directory of the workspace
        sub_projects: List of subdirectories containing pyproject.toml files

    Returns:
        Tuple of (production_deps, dev_deps) as sorted lists
    """
    prod_deps: Dict[str, str] = {}
    dev_deps: Dict[str, str] = {}

    # Get local package names from root pyproject.toml to exclude them
    local_packages = get_local_packages(root_dir)
    print(f"Excluding local packages: {', '.join(local_packages)}\n")

    for sub_project in sub_projects:
        pyproject_path = root_dir / sub_project / "pyproject.toml"

        if not pyproject_path.exists():
            print(f"Warning: {pyproject_path} not found, skipping")
            continue

        print(f"Processing: {pyproject_path}")

        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        # Process production dependencies
        if "project" in data and "dependencies" in data["project"]:
            for dep in data["project"]["dependencies"]:
                name, spec = parse_dependency(dep)
                # Skip local packages
                if name in local_packages:
                    continue
                if name in prod_deps:
                    # Keep the higher version
                    prod_deps[name] = compare_versions(prod_deps[name], spec)
                else:
                    prod_deps[name] = spec

        # Process dev dependencies
        if "dependency-groups" in data and "dev" in data["dependency-groups"]:
            for dep in data["dependency-groups"]["dev"]:
                name, spec = parse_dependency(dep)
                # Skip local packages
                if name in local_packages:
                    continue
                if name in dev_deps:
                    dev_deps[name] = compare_versions(dev_deps[name], spec)
                else:
                    dev_deps[name] = spec

    # Convert back to list format and sort
    prod_list = sorted([f"{name}{spec}" for name, spec in prod_deps.items()])
    dev_list = sorted([f"{name}{spec}" for name, spec in dev_deps.items()])

    return prod_list, dev_list


def get_local_packages(root_dir: Path) -> set[str]:
    """Extract local package names from root pyproject.toml [tool.uv.sources] section.

    Args:
        root_dir: Root directory of the workspace

    Returns:
        Set of local package names (normalized to lowercase)
    """
    pyproject_path = root_dir / "pyproject.toml"

    if not pyproject_path.exists():
        return set()

    try:
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)

        # Extract package names from [tool.uv.sources]
        if "tool" in data and "uv" in data["tool"] and "sources" in data["tool"]["uv"]:
            sources = data["tool"]["uv"]["sources"]
            return {name.lower() for name in sources.keys()}
    except Exception as e:
        print(f"Warning: Could not extract local packages from root pyproject.toml: {e}")

    return set()


def format_toml_array(items: List[str], indent: int = 4) -> str:
    """Format a list of items as a TOML array."""
    if not items:
        return "[]"

    indent_str = " " * indent
    lines = ["["]
    for item in items:
        lines.append(f'{indent_str}"{item}",')
    lines.append("]")
    return "\n".join(lines)


def update_root_pyproject(root_dir: Path, prod_deps: List[str], dev_deps: List[str], dry_run: bool = False):
    """Update the root pyproject.toml with consolidated dependencies."""
    pyproject_path = root_dir / "pyproject.toml"

    if not pyproject_path.exists():
        print(f"Error: {pyproject_path} not found")
        sys.exit(1)

    # Read the current pyproject.toml to extract local packages
    with open(pyproject_path, "rb") as f:
        current_data = tomllib.load(f)

    # Get local package names and add them to dependencies
    local_packages = get_local_packages(root_dir)
    local_package_list = sorted(list(local_packages))

    # Merge local packages with external dependencies (keep them sorted)
    all_deps = sorted(prod_deps + local_package_list)

    content = pyproject_path.read_text()

    # Prepare the new dependencies sections
    all_deps_str = format_toml_array(all_deps)
    dev_deps_str = format_toml_array(dev_deps)

    # Replace production dependencies - match until the closing bracket, handling [tool.uv.sources]
    # This pattern stops at the first ] followed by a newline and either [ or EOF
    prod_pattern = r"(dependencies\s*=\s*)\[[^\]]*(?:\][^\]]*)*?\](?=\s*\n(?:\[|$))"
    content = re.sub(prod_pattern, f"dependencies = {all_deps_str}", content, flags=re.DOTALL)

    # Replace dev dependencies
    dev_pattern = r"(\[dependency-groups\]\s*dev\s*=\s*)\[.*?\]"
    content = re.sub(dev_pattern, f"[dependency-groups]\ndev = {dev_deps_str}", content, flags=re.DOTALL)

    if dry_run:
        print("\n" + "=" * 80)
        print("DRY RUN - Changes that would be made:")
        print("=" * 80)

        print("\nProduction dependencies (including local packages):")
        for dep in all_deps:
            marker = " (local)" if dep in local_package_list else ""
            print(f"  - {dep}{marker}")
        print(f"\nTotal: {len(all_deps)} packages ({len(local_package_list)} local, {len(prod_deps)} external)")

        print("\nDevelopment dependencies:")
        for dep in dev_deps:
            print(f"  - {dep}")
        print(f"\nTotal: {len(dev_deps)} packages")
        print("\n" + "=" * 80)
    else:
        pyproject_path.write_text(content)
        print(f"\n✓ Updated {pyproject_path}")
        print(
            f"  - Production dependencies: {len(all_deps)} ({len(local_package_list)} local, {len(prod_deps)} external)"
        )
        print(f"  - Development dependencies: {len(dev_deps)}")


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Consolidate dependencies from sub-projects into root pyproject.toml")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without modifying files")
    parser.add_argument(
        "--projects",
        nargs="+",
        default=[
            "flip-api",
            "trust/trust-api",
            "trust/imaging-api",
            "trust/data-access-api",
            "docs",
        ],
        help="List of sub-project directories to consolidate (relative to root)",
    )

    args = parser.parse_args()

    # Determine root directory
    root_dir = Path(__file__).parent

    print(f"Root directory: {root_dir}")
    print(f"Sub-projects: {', '.join(args.projects)}\n")

    # Consolidate dependencies
    prod_deps, dev_deps = consolidate_dependencies(root_dir, args.projects)

    # Update root pyproject.toml
    update_root_pyproject(root_dir, prod_deps, dev_deps, dry_run=args.dry_run)

    if args.dry_run:
        print("\nRun without --dry-run to apply changes")


if __name__ == "__main__":
    main()
