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
"""Turn the per-site evaluation results into the federation-wide summary.

``EvaluationJsonGenerator`` writes each site's returned counts to ``evaluation_results/``. This
derives the federated figures from them and prints the readable table.

It runs after the job rather than inside it because the arithmetic is the *lesson*, and a reader can
follow a 100-line script far more easily than a custom NVFLARE aggregation component. Nothing here
needs data that did not already leave the sites: the input is counts, and counts are all that left.

Two numbers are reported side by side, deliberately:

* **pooled** -- derived from summed TP/FP/FN, so each site is weighted by its evidence.
* **macro** -- the unweighted mean across sites, so a small site counts as much as a large one.

Averaging per-site F1 and calling it a global score is the mistake this separation exists to prevent.
The pooled figure is the headline; the gap between the two is itself informative.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_APP_FILES_DIR = Path(__file__).resolve().parents[1] / "app_files"
sys.path.insert(0, str(_APP_FILES_DIR))

from matching import MatchCounts  # noqa: E402
from metrics_utils import detection_metrics, federated_summary  # noqa: E402

# Site results are keyed by the server-side model name, which NVFLARE prefixes when it broadcasts.
_SERVER_MODEL_PREFIX = "SRV_"


def load_site_counts(results_path: Path) -> tuple[dict[str, MatchCounts], dict[str, dict]]:
    """Read the per-site counts and raw metric blocks from an evaluation results file."""
    payload = json.loads(results_path.read_text())
    counts: dict[str, MatchCounts] = {}
    raw: dict[str, dict] = {}
    for site, models in payload.items():
        if not isinstance(models, dict):
            continue
        for model_name, metrics in models.items():
            if not isinstance(metrics, dict) or "tp" not in metrics:
                continue
            counts[site] = MatchCounts(tp=int(metrics["tp"]), fp=int(metrics["fp"]), fn=int(metrics["fn"]))
            raw[site] = dict(metrics)
            raw[site]["model"] = model_name.removeprefix(_SERVER_MODEL_PREFIX)
    return counts, raw


def _format(value: object, spec: str = ".3f") -> str:
    """Format a metric, showing undefined values as ``n/a`` rather than inventing a zero."""
    return format(value, spec) if isinstance(value, (int, float)) else "n/a"


def render_table(counts: dict[str, MatchCounts], raw: dict[str, dict]) -> str:
    """Render the per-site and federated results as a fixed-width table."""
    header = (
        f"{'Site':<10}{'Slides':>7}{'Tiles':>7}{'Ref nuclei':>12}"
        f"{'Precision':>11}{'Recall':>9}{'F1':>8}{'Patient F1 IQR':>16}"
    )
    lines = ["", "Federated nuclei detection evaluation - IDC digital pathology", "", header, "-" * len(header)]
    for site in sorted(counts):
        metrics = detection_metrics(counts[site])
        extra = raw.get(site, {})
        lines.append(
            f"{site:<10}{int(extra.get('n_slides', 0)):>7}{int(extra.get('n_tiles', 0)):>7}"
            f"{metrics['n_references']:>12}{_format(metrics['precision']):>11}"
            f"{_format(metrics['recall']):>9}{_format(metrics['f1']):>8}"
            f"{_format(extra.get('patient_f1_iqr')):>16}"
        )
    pooled = detection_metrics(MatchCounts(*[sum(getattr(c, f) for c in counts.values()) for f in ("tp", "fp", "fn")]))
    lines.append("-" * len(header))
    lines.append(
        f"{'POOLED':<10}{'':>7}{'':>7}{pooled['n_references']:>12}{_format(pooled['precision']):>11}"
        f"{_format(pooled['recall']):>9}{_format(pooled['f1']):>8}{'':>16}"
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("/tmp/nvflare/idc_pathology_nuclei_detection/flip_evaluation/server/simulate_job")
        / "evaluation_results",
        help="Directory (or file) holding the evaluation results JSON written by the job.",
    )
    parser.add_argument("--output", type=Path, default=Path("results/federated_evaluation.json"))
    args = parser.parse_args()

    candidates = (
        sorted(p for p in args.results.glob("*.json") if p.stat().st_size > 2)
        if args.results.is_dir()
        else [args.results]
    )
    if not candidates:
        raise SystemExit(f"No evaluation results found under {args.results}. Run 'make sim' first.")

    counts, raw = load_site_counts(candidates[-1])
    if not counts:
        raise SystemExit(f"{candidates[-1]} contains no site results with counts.")

    summary = federated_summary(counts)
    summary["site_details"] = raw
    summary["source"] = str(candidates[-1])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, default=float) + "\n")

    print(render_table(counts, raw))
    print()
    print(f"  pooled F1        {_format(summary['pooled']['f1'])}")
    print(f"  macro site F1    {_format(summary['macro_site_f1'])}")
    if "site_f1_gap" in summary:
        print(
            f"  best/worst site  {summary['best_site']} {_format(summary['best_site_f1'])} / "
            f"{summary['worst_site']} {_format(summary['worst_site_f1'])}"
        )
        print(f"  cross-site gap   {_format(summary['site_f1_gap'])}")
        widest = max((raw[s].get("patient_f1_iqr", 0.0) or 0.0) for s in raw)
        if widest >= summary["site_f1_gap"]:
            print()
            print(
                "  NOTE: the widest within-site patient F1 IQR "
                f"({widest:.3f}) is at least as large as the between-site gap "
                f"({summary['site_f1_gap']:.3f}), so these data do not support a site effect."
            )
    print()
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
