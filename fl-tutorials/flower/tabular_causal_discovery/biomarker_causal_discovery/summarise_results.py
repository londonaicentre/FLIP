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

"""Read a finished run's global model back into an edge-frequency table and a PAG figure.

The "model" a FLIP run of this tutorial produces (``FL_global_model.pt``, downloaded from the
hub or written by the simulator) is the federated state: the bootstrap PAGs, the pooled
statistics or voted skeleton, and the site-count slots. This script decodes it and prints
what the researcher actually wants — how often each edge appeared across bootstraps — and
optionally writes the table as CSV and draws the stable graph.

    python summarise_results.py <FL_global_model.pt> [--min-prob 0.5] [--csv out.csv] [--figure out.png]

Runs in the flip-utils environment (``make summarise``); the figure needs matplotlib, which
the ``dev`` group provides, and is skipped with a note when it is not importable. Kept
byte-identical between the NVFLARE and Flower tutorials (the app module is found beside the
script in either ``app_files/`` or ``app/``).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent
APP_DIR = next((d for d in (HERE / "app_files", HERE / "app") if (d / "federated_fci.py").exists()), None)
if APP_DIR is None:
    raise SystemExit(f"no app_files/ or app/ with federated_fci.py beside {HERE}")
sys.path.insert(0, str(APP_DIR))

from federated_fci import (  # noqa: E402
    EDGE_MEANINGS,
    STATE_KEYS,
    as_pags,
    decode,
    edge_frequencies,
    federation_mode,
    format_edges,
    recover_site_counts,
)

# Column of the figure each variable is drawn in, by its role in the background knowledge:
# exogenous roots, then the fluid markers, then brain volumes, then the sink(s).
_ROLE_ORDER = ("exogenous", "fluid", "mri", "sink")


def load_state(path: Path) -> dict[str, np.ndarray]:
    """The decoded federated state from a saved ``state_dict`` (any of the backends' formats)."""
    raw = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(raw, dict) and "model" in raw and isinstance(raw["model"], dict):  # NVFLARE persistor wraps it
        raw = raw["model"]
    arrays = {}
    for key in STATE_KEYS:
        if key not in raw:
            raise KeyError(f"{path} has no array {key!r}; keys are {sorted(raw)}")
        arrays[key] = raw[key].detach().cpu().numpy() if hasattr(raw[key], "detach") else np.asarray(raw[key])
    return decode(arrays)


def variable_roles(config: dict) -> dict[str, str]:
    knowledge = config.get("KNOWLEDGE", {})
    roles = {}
    for name in config["VARIABLES"]:
        if name in knowledge.get("EXOGENOUS", []):
            roles[name] = "exogenous"
        elif name in knowledge.get("SINKS", []):
            roles[name] = "sink"
        elif name.startswith(("plasma_", "csf_")):
            roles[name] = "fluid"
        else:
            roles[name] = "mri"
    return roles


def draw_pag(rows: list[dict], config: dict, min_prob: float, title: str, output: Path) -> bool:
    """Draw the edges at or above ``min_prob`` with PAG endpoint glyphs; False if matplotlib is missing."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyArrowPatch
    except ImportError:
        return False

    roles = variable_roles(config)
    columns = {role: [v for v in config["VARIABLES"] if roles[v] == role] for role in _ROLE_ORDER}
    position: dict[str, tuple[float, float]] = {}
    for x, role in enumerate(_ROLE_ORDER):
        names = columns[role]
        for k, name in enumerate(names):
            position[name] = (x * 3.6, -(k - (len(names) - 1) / 2) * 1.9)
    half_w, half_h = 0.95, 0.3  # the label box, in data units: edges start and stop at its border

    def border(ux: float, uy: float) -> float:
        """Distance from a node centre to its box border along the unit direction (ux, uy)."""
        candidates = [
            half_w / abs(ux) if abs(ux) > 1e-9 else float("inf"),
            half_h / abs(uy) if abs(uy) > 1e-9 else float("inf"),
        ]
        return min(candidates)

    fig, ax = plt.subplots(figsize=(13, 6.5))
    shown = [r for r in rows if r["probability"] >= min_prob]
    for r in shown:
        (x0, y0), (x1, y1) = position[r["source"]], position[r["target"]]
        length = max(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1e-9)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length  # unit vector source -> target
        shrink = border(ux, uy) + 0.05
        sx, sy, tx, ty = x0 + shrink * ux, y0 + shrink * uy, x1 - shrink * ux, y1 - shrink * uy
        style = {"-->": "-|>", "o->": "-|>", "<->": "<|-|>", "o-o": "-", "---": "-", "o--": "-"}[r["edge"]]
        colour = "#b4541c" if r["edge"] == "<->" else "#0f6e6a"
        # Edges spanning more than one column bow slightly so they pass beside, not through, the boxes between.
        rad = 0.18 if abs(x1 - x0) > 3.7 else 0.0
        ax.add_patch(
            FancyArrowPatch(
                (sx, sy),
                (tx, ty),
                arrowstyle=style,
                mutation_scale=15,
                lw=0.8 + 3.0 * r["probability"],
                color=colour,
                shrinkA=0,
                shrinkB=0,
                connectionstyle=f"arc3,rad={rad}",
                zorder=1,
            )
        )
        if r["edge"].startswith("o"):
            ax.plot([sx + 0.09 * ux], [sy + 0.09 * uy], "o", mfc="white", mec=colour, mew=1.6, ms=8, zorder=2)
        if r["edge"].endswith("o"):
            ax.plot([tx - 0.09 * ux], [ty - 0.09 * uy], "o", mfc="white", mec=colour, mew=1.6, ms=8, zorder=2)
        # Frequency label at 38 % of the way along, pushed off the line along its normal.
        bow = -rad * length / 2  # arc3's control point sits at rad*length off the chord's midpoint
        lx, ly = x0 + 0.38 * (x1 - x0) - (0.18 + bow) * uy, y0 + 0.38 * (y1 - y0) + (0.18 + bow) * ux
        ax.text(
            lx,
            ly,
            f"{r['probability']:.2f}",
            fontsize=7.5,
            ha="center",
            va="center",
            color=colour,
            zorder=3,
            bbox={"boxstyle": "round,pad=0.12", "fc": "white", "ec": "none", "alpha": 0.9},
        )
    for name, (x, y) in position.items():
        ax.text(
            x,
            y,
            name,
            ha="center",
            va="center",
            fontsize=8.5,
            zorder=4,
            bbox={"boxstyle": "round,pad=0.35", "fc": "#f3f5f4", "ec": "#5b6a67"},
        )
    ys = [y for _, y in position.values()]
    ax.set_xlim(-1.4, 3.6 * (len(_ROLE_ORDER) - 1) + 1.4)
    ax.set_ylim(min(ys) - 0.9, max(ys) + 0.9)
    ax.set_axis_off()
    ax.set_title(
        f"{title}\nedges with bootstrap frequency ≥ {min_prob:g}; → cause, ↔ latent confounder, o undetermined mark",
        fontsize=10,
    )
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120)
    plt.close(fig)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="FL_global_model.pt from a simulator run or a FLIP download")
    parser.add_argument("--config", type=Path, default=APP_DIR / "config.json", help="the app's config.json")
    parser.add_argument(
        "--min-prob",
        type=float,
        default=None,
        help="edge-frequency threshold for the figure (default: config STABLE_EDGE_PROB)",
    )
    parser.add_argument("--csv", type=Path, help="write the full edge-frequency table here")
    parser.add_argument("--figure", type=Path, help="draw the stable PAG here (PNG)")
    args = parser.parse_args(argv)

    config = json.loads(args.config.read_text())
    state = load_state(args.model)
    variables = list(config["VARIABLES"])
    pags = as_pags(state["pag"])
    if not np.any(pags):
        raise SystemExit("the model holds no PAGs yet: the run did not reach its orientation round")
    pooled_n, sizes = recover_site_counts(state["site_counts"], federation_mode(config) == "pooled_statistics")
    residual = max(float(np.max(np.abs(state["stats_mean"]))), float(np.max(np.abs(state["stats_moment"]))))
    if residual > 1e-6:
        print(
            f"WARNING: the statistics buffers are not zero (max |value| {residual:.3g}): this model holds the "
            "pooled statistics, which a finished pooled_statistics run consumes in round 2 — the job was stopped "
            "or failed after round 1. Treat the file as sensitive and do not share it."
        )
    rows = edge_frequencies(pags, variables)
    min_prob = args.min_prob if args.min_prob is not None else float(config.get("STABLE_EDGE_PROB", 0.5))

    print(
        f"federation: {federation_mode(config)}; {pags.shape[0]} bootstrap PAG(s); "
        f"{len(sizes)} site(s), {pooled_n} rows pooled "
        f"({', '.join(f'slot {j}: {n}' for j, n in sorted(sizes.items()))})"
    )
    print(f"\nEdges with frequency >= {min_prob:g}:")
    print(format_edges(rows, min_prob))
    print("\nEdge marks: " + "; ".join(f"{k}: {v}" for k, v in EDGE_MEANINGS.items()))

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(args.csv, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=["source", "edge", "target", "count", "probability", "p_adjacent"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nwrote {args.csv}")
    if args.figure:
        if draw_pag(rows, config, min_prob, f"Federated FCI ({federation_mode(config)}, n={pooled_n})", args.figure):
            print(f"wrote {args.figure}")
        else:
            print("matplotlib is not importable here: figure skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
