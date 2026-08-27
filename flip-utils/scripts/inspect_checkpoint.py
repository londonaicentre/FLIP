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
#

"""Inspect a FLIP training checkpoint and optionally round-trip it into an app's model.

A thin command-line front end over :mod:`flip.export.checkpoint`, for checking what is actually on
disk before packaging anything. Run it before an export: if the round-trip fails here, nothing
downstream will work either.

Usage::

    # Structure only
    uv run python scripts/inspect_checkpoint.py <checkpoint.pt>

    # Structure plus a strict round-trip into the application's declared architecture
    uv run python scripts/inspect_checkpoint.py <checkpoint.pt> --app-dir <path/to/app_files>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flip.export.checkpoint import describe_checkpoint, load_app_model, load_checkpoint, state_dict_from


def report(path: Path, app_dir: Path | None, max_keys: int, allow_pickle: bool) -> bool:
    """Print a checkpoint's structure and, when an app directory is given, round-trip it.

    Args:
        path (Path): Checkpoint to inspect.
        app_dir (Path | None): Application directory, enabling the strict round-trip check.
        max_keys (int): Leading and trailing parameter keys to display.
        allow_pickle (bool): Load with ``weights_only=False``.

    Returns:
        bool: True unless a requested round-trip failed.
    """
    data = load_checkpoint(path, allow_pickle=allow_pickle)
    facts = describe_checkpoint(data, path)
    state_dict = state_dict_from(data)

    print("=" * 88)
    print(f"Checkpoint : {path}")
    print(f"Size       : {path.stat().st_size / 1e6:.1f} MB")
    print(f"Format     : {'NVFLARE persistence format' if facts.is_persistence_format else 'bare state dict'}")
    print(f"Parameters : {facts.num_entries} entries, {facts.num_elements:,} elements")
    print(f"Dtypes     : {facts.dtype_histogram}")
    print(f"Prefixes   : {facts.prefixes}")
    print(f"train_conf : {json.dumps(facts.train_conf, default=str) if facts.train_conf else '(absent)'}")
    print(f"meta_props : {json.dumps(facts.meta_props, default=str) if facts.meta_props else '(absent)'}")

    keys = list(state_dict)
    print("\nParameter keys:")
    for key in keys[:max_keys]:
        print(f"  {key}: {tuple(state_dict[key].shape)} {state_dict[key].dtype}")
    if len(keys) > 2 * max_keys:
        print(f"  ... {len(keys) - 2 * max_keys} more ...")
    for key in keys[-max_keys:]:
        print(f"  {key}: {tuple(state_dict[key].shape)} {state_dict[key].dtype}")

    print("\nKey mangling:")
    print(f"  'module.'-prefixed keys (DataParallel): {len(facts.dataparallel_keys)}")
    print(f"  integer buffers promoted to float     : {len(facts.promoted_buffers)}")
    if facts.promoted_buffers:
        print(f"    e.g. {facts.promoted_buffers[0]}")
        print("    load_state_dict casts these back on copy, so strict loading is unaffected.")

    if app_dir is None:
        return True

    print("\n" + "=" * 88)
    print(f"Round-trip against {app_dir}")
    model = load_app_model(app_dir)
    expected = model.state_dict()
    print(f"  architecture : {type(model).__name__} ({sum(p.numel() for p in model.parameters()):,} parameters)")
    print(f"  checkpoint   : {len(state_dict)} entries; architecture expects {len(expected)}")

    for label, keys_found in (
        ("missing", [k for k in expected if k not in state_dict]),
        ("unexpected", [k for k in state_dict if k not in expected]),
    ):
        print(f"  {label:16}: {len(keys_found)}{'' if not keys_found else f' -> {keys_found[:5]}'}")

    try:
        model.load_state_dict(state_dict, strict=True)
    except Exception as exc:  # noqa: BLE001 — the failure mode is the finding; report it verbatim.
        print(f"\n  ✗ strict load FAILED: {type(exc).__name__}: {exc}")
        return False

    print("\n  ✓ strict load OK — checkpoint and declared architecture agree.")
    return True


def main() -> int:
    """Entry point.

    Returns:
        int: 0 on success, 1 if a requested round-trip failed.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("checkpoint", type=Path, help="Path to the checkpoint (.pt) to inspect")
    parser.add_argument("--app-dir", type=Path, default=None, help="App directory with models.py; enables round-trip")
    parser.add_argument("--max-keys", type=int, default=5, help="Leading/trailing parameter keys to display")
    parser.add_argument(
        "--allow-pickle",
        action="store_true",
        help="Load with weights_only=False. Only for checkpoints of known provenance.",
    )
    args = parser.parse_args()
    return 0 if report(args.checkpoint, args.app_dir, args.max_keys, args.allow_pickle) else 1


if __name__ == "__main__":
    raise SystemExit(main())
