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

"""Plot an `autoencoder` run's loss curves from its simulator logs.

The tutorial already writes reconstructions to ``debug_samples`` but not the numbers behind them,
and an adversarial autoencoder is the case where the two have to be read together: a reconstruction
that stops improving looks the same whether the model has converged or whether the discriminator has
started to dominate the generator. The scalars distinguish those.

**This reads the logs; it does not instrument the trainer.** Nothing under ``app_files/`` changes, so
what runs at a trust is unaffected and this script is not walked by the shipped-app guards. It
recovers two series that the trainer already prints:

* per-iteration, the four **weighted** generator terms (``KL``/``l1``/``perceptual``/``gan``) — these
  are the products ``w_* x loss``, i.e. the actual contributions to the generator objective, so they
  are directly comparable to one another and are what you tune the ``w_*`` keys against;
* per-epoch, the totals plus the discriminator loss, validation L1 and foreground SSIM.

Reading the result: the generator and discriminator losses should fall *together*. A discriminator
loss that keeps falling while the generator's ``gan`` term climbs is the discriminator winning, and
is usually followed by validation L1 and SSIM turning back up and down respectively.

Usage:

    python process_tools/plot_losses.py
    python process_tools/plot_losses.py --workspace /tmp/nvflare/autoencoder/flip_fedavg
    python process_tools/plot_losses.py --out-dir <dir>       # default: $DEBUG_SAMPLES_DIR
"""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402

DEFAULT_WORKSPACE = Path("/tmp/nvflare/autoencoder/flip_fedavg")

# The per-iteration print and the per-epoch summary, both emitted by app_files/trainer.py.
ITER_RE = re.compile(
    r"KL: (?P<kl>[\d.eE+-]+), l1: (?P<l1>[\d.eE+-]+), "
    r"perceptual: (?P<perceptual>[\d.eE+-]+), gan: (?P<gan>[\d.eE+-]+)"
)
EPOCH_RE = re.compile(
    r"Total loss D: (?P<d>[\d.eE+-]+),Validation loss \(L1\): (?P<val_l1>[\d.eE+-]+), "
    r"Validation SSIM \(foreground\): (?P<ssim>[\d.eE+-]+)"
)

# One colour per loss type, held fixed across sites so two figures can be compared side by side.
COLOURS = {
    "l1": "#4ea1ff",
    "perceptual": "#ffd166",
    "gan": "#ff6b6b",
    "kl": "#9b7bff",
    "d": "#7bffb0",
    "val_l1": "#4ea1ff",
    "ssim": "#ff9f4e",
}
SERIES = (("l1", "L1 (reconstruction)"), ("perceptual", "Perceptual"), ("gan", "Adversarial"), ("kl", "KL"))

EMA_ALPHA = 0.02


def _ema(values: list[float], alpha: float = EMA_ALPHA) -> list[float]:
    """Exponential moving average — the per-iteration series is too noisy to read raw."""
    out: list[float] = []
    acc = values[0] if values else 0.0
    for value in values:
        acc = alpha * value + (1 - alpha) * acc
        out.append(acc)
    return out


def parse_log(path: Path) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Return ``(per_iteration, per_epoch)`` series parsed from one site's ``log.txt``."""
    iters: dict[str, list[float]] = {key: [] for key in ("kl", "l1", "perceptual", "gan")}
    epochs: dict[str, list[float]] = {key: [] for key in ("d", "val_l1", "ssim")}
    with path.open() as handle:
        for line in handle:
            match = ITER_RE.search(line)
            if match:
                for key, value in match.groupdict().items():
                    iters[key].append(float(value))
                continue
            match = EPOCH_RE.search(line)
            if match:
                for key, value in match.groupdict().items():
                    epochs[key].append(float(value))
    return iters, epochs


def plot_site(site: str, iters: dict[str, list[float]], epochs: dict[str, list[float]], out_dir: Path) -> Path:
    """Write the two-panel figure for one site and return its path."""
    figure, (top, bottom) = plt.subplots(2, 1, figsize=(11, 8), facecolor="#111111")

    # Top: the weighted generator terms. Log y — they span roughly two decades, and the KL term is
    # invisible on a linear axis, which is itself worth being able to see.
    top.set_facecolor("#111111")
    for key, label in SERIES:
        values = iters[key]
        if not values:
            continue
        top.plot(values, color=COLOURS[key], alpha=0.15, linewidth=0.6)
        top.plot(_ema(values), color=COLOURS[key], linewidth=1.8, label=label)
    top.set_yscale("log")
    top.set_xlabel("training iteration")
    top.set_ylabel("weighted contribution to $\\mathcal{L}_G$")
    top.set_title(f"{site} — generator loss terms (weighted)", color="#eeeeee")
    top.legend(loc="upper right", facecolor="#1c1c1c", edgecolor="#444444", labelcolor="#eeeeee")

    # Bottom: the per-epoch picture, where divergence between D and validation shows up.
    bottom.set_facecolor("#111111")
    if epochs["d"]:
        steps = range(1, len(epochs["d"]) + 1)
        bottom.plot(steps, epochs["d"], color=COLOURS["d"], marker="o", markersize=3, label="Discriminator loss")
        bottom.plot(steps, epochs["val_l1"], color=COLOURS["val_l1"], marker="o", markersize=3, label="Validation L1")
        bottom.set_xlabel("local epoch")
        bottom.set_ylabel("loss")
        twin = bottom.twinx()
        twin.plot(steps, epochs["ssim"], color=COLOURS["ssim"], marker="s", markersize=3, label="Validation SSIM")
        twin.set_ylabel("SSIM (foreground)", color=COLOURS["ssim"])
        twin.tick_params(axis="y", colors=COLOURS["ssim"])
        for spine in twin.spines.values():
            spine.set_color("#444444")
        handles, labels = bottom.get_legend_handles_labels()
        extra_handles, extra_labels = twin.get_legend_handles_labels()
        bottom.legend(
            handles + extra_handles,
            labels + extra_labels,
            loc="upper right",
            facecolor="#1c1c1c",
            edgecolor="#444444",
            labelcolor="#eeeeee",
        )
    bottom.set_title(f"{site} — discriminator vs validation", color="#eeeeee")

    for axis in (top, bottom):
        axis.tick_params(colors="#bbbbbb")
        axis.grid(True, color="#333333", linewidth=0.5)
        for spine in axis.spines.values():
            spine.set_color("#444444")
        axis.xaxis.label.set_color("#bbbbbb")
        axis.yaxis.label.set_color("#bbbbbb")

    figure.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"losses_{site}.png"
    figure.savefig(destination, dpi=110, facecolor=figure.get_facecolor())
    plt.close(figure)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="simulator workspace root")
    parser.add_argument("--out-dir", type=Path, default=None, help="defaults to $DEBUG_SAMPLES_DIR, else ./")
    args = parser.parse_args()

    out_dir = args.out_dir or Path(os.environ.get("DEBUG_SAMPLES_DIR", "."))

    logs = sorted(args.workspace.glob("site-*/log.txt"))
    if not logs:
        raise SystemExit(f"no site logs under {args.workspace} — pass --workspace")

    for log in logs:
        site = log.parent.name
        iters, epochs = parse_log(log)
        if not iters["l1"]:
            print(f"{site}: no loss lines yet, skipping")
            continue
        destination = plot_site(site, iters, epochs, out_dir)
        print(f"{site}: {len(iters['l1'])} iterations, {len(epochs['d'])} epochs -> {destination}")


if __name__ == "__main__":
    main()
