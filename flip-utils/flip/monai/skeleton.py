# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Generate a ``monai-flip.yaml`` skeleton from a MONAI bundle directory (FLIP#1105).

Usage::

    python -m flip.monai.skeleton path/to/spleen_ct_segmentation [--source monai-model-zoo] [-o monai-flip.yaml]

The generator fills only what it can read from the bundle and leaves every value with clinical or
data meaning as a ``<FILL_ME ...>`` placeholder, which :func:`flip.monai.overlay.load_overlay` refuses.
That follows the precedent in ``flip/export/bundle.py``: FLIP reads bundle configs, it never
synthesises the ones that carry clinical meaning.

Where each ``bundle:`` value comes from:

- ``version``: ``configs/metadata.json`` (``version`` key).
- ``name``: the bundle directory name, which is the Model Zoo identifier (``spleen_ct_segmentation``).
  ``metadata.json`` does carry a ``name`` key, but it is a display title ("Spleen CT Segmentation").
- ``configs``: a listing of ``configs/``. ``evaluate`` is composed on top of ``train``, as MONAI does.
- ``source``: cannot be derived; the caller states it (default ``local``).

Every line is marked LOCKED or EDITABLE. LOCKED values identify the bundle or the governance profile;
changing one makes the overlay describe a different artefact. EDITABLE values are the researcher's
to set. Config mutability inside ``configs/`` itself is FLIP#1127, not this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from flip.monai.overlay import CONFIG_SUFFIXES, GOVERNANCE_PROFILE_V0_1, PLACEHOLDER_PREFIX, BundleSource


class SkeletonError(ValueError):
    """The directory does not look like a MONAI training bundle."""


def _q(value: str) -> str:
    """Quote a scalar for YAML. A JSON string is always a valid YAML double-quoted scalar."""
    return json.dumps(value)


def _find_config(configs_dir: Path, stem: str) -> str | None:
    matches = [
        p for p in sorted(configs_dir.iterdir()) if p.is_file() and p.stem == stem and p.suffix in CONFIG_SUFFIXES
    ]
    if len(matches) > 1:
        names = ", ".join(p.name for p in matches)
        raise SkeletonError(f"configs/ holds more than one {stem} config ({names}); keep exactly one")
    return f"configs/{matches[0].name}" if matches else None


def read_bundle_version(bundle_dir: Path) -> str:
    """Return ``version`` from ``configs/metadata.json``.

    Raises:
        SkeletonError: If the file is missing, unreadable, or has no string ``version``.
    """
    metadata_path = bundle_dir / "configs" / "metadata.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkeletonError(f"{metadata_path} not found; is {bundle_dir} a MONAI bundle?") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SkeletonError(f"{metadata_path}: cannot read: {exc}") from exc
    version = metadata.get("version") if isinstance(metadata, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise SkeletonError(f"{metadata_path} has no string 'version'")
    return version


def generate_skeleton(bundle_dir: str | Path, source: BundleSource | str = BundleSource.LOCAL) -> str:
    """Return the text of a ``monai-flip.yaml`` skeleton for ``bundle_dir``.

    Args:
        bundle_dir: Root of a MONAI bundle (holds ``configs/metadata.json``).
        source: Where the bundle came from; not derivable from the bundle itself.

    Returns:
        str: YAML text with LOCKED/EDITABLE markers and ``<FILL_ME ...>`` placeholders.

    Raises:
        SkeletonError: If the directory is not a MONAI training bundle.
    """
    bundle_dir = Path(bundle_dir).resolve()
    source = BundleSource(source)
    configs_dir = bundle_dir / "configs"
    if not configs_dir.is_dir():
        raise SkeletonError(f"{bundle_dir} has no configs/ directory; is it a MONAI bundle?")

    version = read_bundle_version(bundle_dir)
    train = _find_config(configs_dir, "train")
    if train is None:
        raise SkeletonError(
            f"{configs_dir} has no train config. The on-ramp imports training bundles; an inference-only "
            "bundle (such as the ones `python -m flip.export` writes) cannot be trained."
        )
    evaluate = _find_config(configs_dir, "evaluate")

    lines = [
        "# monai-flip.yaml: FLIP overlay for a MONAI training bundle (schema v0.1).",
        f"# Generated by `python -m flip.monai.skeleton`. Replace every {PLACEHOLDER_PREFIX} ...> value;",
        "# the platform refuses an overlay that still holds one.",
        "#   LOCKED   identifies the bundle or the governance profile; do not edit.",
        "#   EDITABLE is yours to set.",
        "bundle:",
        f"  source: {_q(source.value)}  # LOCKED (stated at generation, not derivable from the bundle)",
        f"  name: {_q(bundle_dir.name)}  # LOCKED (bundle directory name)",
        f"  version: {_q(version)}  # LOCKED (configs/metadata.json)",
        "  configs:  # LOCKED (listing of configs/)",
        f"    train: {_q(train)}",
    ]
    if evaluate is not None:
        lines.append(f"    evaluate: [{_q(train)}, {_q(evaluate)}]")
    lines += [
        "",
        "flip:",
        f'  query_ref: "{PLACEHOLDER_PREFIX}: path to the cohort .sql, e.g. query.sql>"  # EDITABLE',
        "  imaging:",
        f'    resource: "{PLACEHOLDER_PREFIX}: NIFTI | SEG | DICOM>"  # EDITABLE (the XNAT resource to read)',
        "    keys:  # EDITABLE (one entry per datalist key the bundle's configs read; 'image' is required)",
        f'      image: {{pattern: "{PLACEHOLDER_PREFIX}: file glob, e.g. input_*.nii.gz>"}}',
        f'      label: {{pattern: "{PLACEHOLDER_PREFIX}: file glob, e.g. label_*.nii.gz>"}}',
        "  fl:",
        "    local_epochs: 1  # EDITABLE",
        "    exchange:  # EDITABLE (draft until the adapter design is final)",
        "      weights: weight_diff",
        "      metrics: []",
        "      statistics: [data_count]",
        "  governance:",
        f"    profile: {_q(GOVERNANCE_PROFILE_V0_1)}  # LOCKED",
        "    allowed_outputs: [metrics, model]  # EDITABLE (may only be narrowed)",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: write a skeleton for one bundle directory."""
    parser = argparse.ArgumentParser(
        prog="python -m flip.monai.skeleton", description="Generate a monai-flip.yaml skeleton."
    )
    parser.add_argument("bundle_dir", type=Path, help="root of the MONAI bundle (holds configs/metadata.json)")
    parser.add_argument("--source", choices=[s.value for s in BundleSource], default=BundleSource.LOCAL.value)
    parser.add_argument("-o", "--output", type=Path, help="write here instead of stdout; refuses to overwrite")
    args = parser.parse_args(argv)
    try:
        text = generate_skeleton(args.bundle_dir, args.source)
    except SkeletonError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output is None:
        sys.stdout.write(text)
        return 0
    if args.output.exists():
        print(f"error: {args.output} exists; not overwriting", file=sys.stderr)
        return 1
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
