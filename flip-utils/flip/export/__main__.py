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

"""Command-line interface for exporting a FLIP model as a MONAI Bundle.

Invoked as a module rather than a console script, because ``flip-utils`` declares no entry points
yet and introducing a top-level ``flip`` command is a decision about the package's public surface:

.. code-block:: bash

    python -m flip.export \\
        --checkpoint FL_global_model.pt \\
        --app-dir    app_files \\
        --out        bundle/model.ts \\
        --input-shape 1,1,96,96,96
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from flip.export.bundle import export_bundle
from flip.export.provenance import Provenance


def _shape(value: str) -> tuple[int, ...]:
    """Parse a comma-separated input shape.

    Args:
        value (str): Shape as ``"1,1,96,96,96"``.

    Returns:
        tuple[int, ...]: The parsed dimensions.

    Raises:
        argparse.ArgumentTypeError: If any dimension is not a positive integer.
    """
    try:
        dims = tuple(int(part) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid input shape {value!r}; expected e.g. 1,1,96,96,96") from exc
    if not dims or any(dim <= 0 for dim in dims):
        raise argparse.ArgumentTypeError(f"invalid input shape {value!r}; dimensions must be positive")
    return dims


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="python -m flip.export",
        description="Export a FLIP training checkpoint as a MONAI Bundle (TorchScript + configs).",
    )
    parser.add_argument("--checkpoint", type=Path, required=True, help="Aggregated checkpoint, e.g. FL_global_model.pt")
    parser.add_argument("--app-dir", type=Path, required=True, help="Application directory containing models.py")
    parser.add_argument("--out", type=Path, required=True, help="Destination bundle path, conventionally model.ts")
    parser.add_argument("--inference-config", type=Path, help="Override <app-dir>/../export/inference.json")
    parser.add_argument("--metadata-config", type=Path, help="Override <app-dir>/../export/metadata.json")
    parser.add_argument(
        "--method", choices=("script", "trace"), default="script", help="TorchScript method (default: script)"
    )
    parser.add_argument(
        "--input-shape",
        type=_shape,
        help="Probe input shape, e.g. 1,1,96,96,96. Required for --method trace; "
        "with script it enables the numerical equivalence check.",
    )
    parser.add_argument("--job-type", default="", help="Originating job type, used only to warn on eligibility")
    parser.add_argument(
        "--allow-pickle",
        action="store_true",
        help="Load the checkpoint with weights_only=False. Only for checkpoints of known provenance.",
    )

    group = parser.add_argument_group("provenance", "Recorded inside the bundle's metadata.json")
    group.add_argument("--model-id", default="", help="FLIP model UUID")
    group.add_argument("--project-id", default="", help="FLIP project ID")
    group.add_argument("--trusts", default="", help="Comma-separated participating Trusts")
    group.add_argument("--global-rounds", type=int, help="Number of federated rounds")
    group.add_argument("--local-rounds", type=int, help="Local epochs per round")
    group.add_argument("--metric", default="", help="Final aggregate metric, e.g. 'Dice=0.91'")
    group.add_argument("--fl-backend", default="nvflare", help="FL backend (default: nvflare)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point.

    Args:
        argv (list[str] | None): Argument vector, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = build_parser().parse_args(argv)

    from flip import __version__

    result = export_bundle(
        checkpoint=args.checkpoint,
        app_dir=args.app_dir,
        out=args.out,
        inference_config=args.inference_config,
        metadata_config=args.metadata_config,
        provenance=Provenance(
            model_id=args.model_id,
            project_id=args.project_id,
            participating_trusts=[trust.strip() for trust in args.trusts.split(",") if trust.strip()],
            global_rounds=args.global_rounds,
            local_rounds=args.local_rounds,
            final_aggregate_metric=args.metric,
            fl_backend=args.fl_backend,
            flip_version=__version__,
        ),
        method=args.method,
        example_input_shape=args.input_shape,
        job_type=args.job_type,
        allow_pickle=args.allow_pickle,
    )

    print(f"Wrote {result.output} ({result.output.stat().st_size / 1e6:.1f} MB)")
    print(f"  method            : {result.method}")
    print(f"  parameters        : {result.num_parameters:,}")
    print(f"  state dict entries: {result.num_state_entries}")
    if result.max_abs_delta >= 0:
        print(f"  max|delta| vs eager: {result.max_abs_delta:.3e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
