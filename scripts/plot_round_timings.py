#!/usr/bin/env python3
#
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
"""Render seaborn boxplots of per-round FL timing metrics from a rounds.tsv.

Companion to extract_model_metrics.sh, which writes rounds.tsv (one row per
communication round, ms-precision epoch columns) and invokes this script
best-effort via `uv run --no-project --with pandas --with seaborn`. Not part of
any FLIP service — pandas/seaborn are deliberately NOT project dependencies.

One subplot per metric, each on its own linear seconds axis (aggregation is ~3
orders of magnitude below round duration, so a shared axis would flatten it):
  - Round duration       (end_ms - start_ms per round)
  - Aggregation time     (agg_end_ms - agg_start_ms; NVFLARE only — "-" for Flower)
  - Inter-round gap      (next round's start_ms - this round's end_ms)
"""

import argparse
import sys

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

# Reference dataviz palette (light mode): blue series hue on a near-white surface.
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3e0"

METRIC_ORDER = ["Round duration", "Aggregation", "Inter-round gap"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("rounds_tsv", help="rounds.tsv written by extract_model_metrics.sh")
    parser.add_argument("output_png", help="path of the PNG to write")
    parser.add_argument("--model-id", default="unknown", help="model UUID (title only)")
    parser.add_argument("--backend", default="unknown", help="detected FL backend (title only)")
    parser.add_argument("--dpi", type=int, default=150)
    return parser.parse_args()


def load_metrics(rounds_tsv: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (per-round wide frame, long frame of metric/seconds/round)."""
    df = pd.read_csv(rounds_tsv, sep="\t", na_values=["-"])
    if df.empty:
        sys.exit("rounds.tsv has no data rows — nothing to plot.")

    df = df.sort_values("round").reset_index(drop=True)
    df["round_duration_s"] = (df["end_ms"] - df["start_ms"]) / 1000
    df["aggregation_s"] = (df["agg_end_ms"] - df["agg_start_ms"]) / 1000
    # Gap between this round finishing and the next one starting (last round has none).
    df["inter_round_gap_s"] = (df["start_ms"].shift(-1) - df["end_ms"]) / 1000

    long = df.melt(
        id_vars=["round"],
        value_vars=["round_duration_s", "aggregation_s", "inter_round_gap_s"],
        var_name="metric",
        value_name="seconds",
    ).dropna(subset=["seconds"])
    long["metric"] = long["metric"].map(
        {
            "round_duration_s": "Round duration",
            "aggregation_s": "Aggregation",
            "inter_round_gap_s": "Inter-round gap",
        }
    )
    if long.empty:
        sys.exit("No finite timing values in rounds.tsv — nothing to plot.")
    return df, long


def main() -> None:
    args = parse_args()
    df, long = load_metrics(args.rounds_tsv)

    order = [m for m in METRIC_ORDER if m in set(long["metric"])]

    sns.set_theme(style="whitegrid", rc={"axes.facecolor": SURFACE, "figure.facecolor": SURFACE})
    fig, axes = plt.subplots(1, len(order), figsize=(3.4 * len(order) + 0.6, 4.8))
    axes = [axes] if len(order) == 1 else list(axes)

    for ax, metric in zip(axes, order):
        sub = long[long["metric"] == metric]
        sns.boxplot(
            data=sub, y="seconds", color=SERIES,
            width=0.4, linewidth=1.1, fliersize=0,
            boxprops={"alpha": 0.35, "edgecolor": SERIES},
            whiskerprops={"color": SERIES}, capprops={"color": SERIES},
            medianprops={"color": SERIES, "linewidth": 1.6},
            ax=ax,
        )
        sns.stripplot(data=sub, y="seconds", color=SERIES, size=3, alpha=0.55, jitter=0.1, ax=ax)

        median = sub["seconds"].median()
        ax.annotate(
            f"median {median:.3g}s", (0, median), xytext=(0, 9),
            textcoords="offset points", ha="center", fontsize=8.5, color=INK,
        )

        ax.set_title(metric, fontsize=10.5, color=INK)
        ax.set_xlabel("")
        ax.set_xticks([])
        ax.set_ylabel("seconds", color=INK_2)
        ax.tick_params(colors=INK)
        ax.grid(True, axis="y", color=GRID, linewidth=0.7)
        ax.margins(y=0.1)
        sns.despine(ax=ax, left=True, bottom=True)

    # Call out an extreme round-duration outlier (typically round 0: client startup).
    durations = df.dropna(subset=["round_duration_s"])
    if "Round duration" in order and len(durations) > 1:
        top = durations.loc[durations["round_duration_s"].idxmax()]
        if top["round_duration_s"] > 2 * durations["round_duration_s"].median():
            axes[order.index("Round duration")].annotate(
                f"round {int(top['round'])}: {top['round_duration_s']:.0f}s",
                (0, top["round_duration_s"]), xytext=(8, -3),
                textcoords="offset points", ha="left", fontsize=8.5, color=INK_2,
            )

    span_s = (df["end_ms"].max() - df["start_ms"].min()) / 1000
    started = pd.to_datetime(df["start_ms"].min(), unit="ms", utc=True)
    context = (
        f"{len(df)} rounds | backend: {args.backend} | "
        f"started {started:%Y-%m-%d %H:%M} UTC | rounds 0–{int(df['round'].max())} "
        f"span {span_s / 3600:.1f} h"
    )
    fig.suptitle(f"Per-round timing — model {args.model_id[:8]}", fontsize=12, color=INK, x=0.06, y=0.985, ha="left")
    fig.text(0.06, 0.925, context, fontsize=9, color=INK_2, ha="left")

    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(args.output_png, dpi=args.dpi, facecolor=SURFACE)
    print(f"Wrote {args.output_png}")


if __name__ == "__main__":
    main()
