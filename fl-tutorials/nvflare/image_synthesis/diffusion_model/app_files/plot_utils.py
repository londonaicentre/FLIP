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

"""Figures for the image-synthesis tutorials: debug images during training, loss curves after it.

Two halves that are always read together, so they live together.

**The training half** writes the pixels themselves. A generative model is the one kind of model
whose scalars barely tell you whether it is working: an autoencoder's L1 falls steadily while it
learns to emit a blur, and a diffusion model's MSE on predicted noise is almost uninformative about
sample quality. ``save_grid`` and ``save_triplanar`` are called from ``trainer.py`` during a run.

**The analysis half** recovers the numbers behind those images from a finished (or running)
simulator log, and is run by hand on the host — ``python app_files/plot_utils.py``. The pairing is
the point: a reconstruction that stops improving looks identical whether the model has converged or
whether the discriminator has started to dominate the generator, and only the scalars tell those
apart. Conversely a loss curve cannot tell you the model has settled on a plausible silhouette with
no anatomy inside it, and the tri-planar figures show that at a glance.

**Nothing here puts an image on the wire.** The files are written beside the running training script
— i.e. inside that client's own job workspace — and no code path reads them back, adds them to an
``FLModel``, or hands them to the metrics writer (``SummaryWriter`` could not carry them anyway: the
Client API's writer exposes only ``add_scalar``/``add_scalars``). An image saved here leaves the site
only if a human deliberately copies it out, which at a real trust is a disclosure decision governed
by the same rules as any other data egress, not something a training run can do by itself.

Off by default, and enabled per job by ``SAVE_DEBUG_SAMPLES`` in ``config.json``. Treat it as a
local-development instrument: leaving it on at a trust accumulates patient-derived reconstructions
on that trust's disk, round after round, with no retention policy attached.

The analysis half is **inert at a trust**: it runs only under ``__main__``, reads simulator paths
that do not exist there, and is never imported by ``trainer.py``. It ships regardless, because this
module does — worth knowing when reviewing what an app bundle contains.

Usage of the analysis half::

    python app_files/plot_utils.py
    python app_files/plot_utils.py --workspace /tmp/nvflare/<tutorial>/flip_fedavg
    python app_files/plot_utils.py --out-dir <dir>       # default: $DEBUG_SAMPLES_DIR
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path

import numpy as np
import torch
from torchvision.utils import make_grid, save_image

logger = logging.getLogger(__name__)

#: Directory created next to the training script. Gitignored, and never read back by any code path.
DEBUG_DIR_NAME = "debug_samples"

#: Environment variable that relocates the output. Set by the tutorial Makefiles for simulator runs
#: only — see :func:`debug_dir` for why it is an env var rather than a ``config.json`` key.
DEBUG_DIR_ENV = "DEBUG_SAMPLES_DIR"

#: How many images to write per grid when ``DEBUG_SAMPLES_MAX`` is absent from the config.
DEFAULT_MAX_IMAGES = 8

#: Training iterations between tri-planar figures when ``DEBUG_PLOT_EVERY`` is absent.
DEFAULT_PLOT_EVERY = 100

#: The three orthogonal views: display name, the array axis each one holds fixed, and whether the
#: slice needs a left-right flip after the shared rotation.
#:
#: Volumes reach here as ``(C, X, Y, Z)`` with the chain's ``Orientationd(axcodes="RAS")``
#: guaranteeing what the axes mean: +X is right, +Y anterior, +Z superior. So a sagittal view fixes
#: X, a coronal view fixes Y, and an axial view fixes Z. Without that guarantee these labels — and
#: the flip below — would be guesses.
#:
#: One ``np.rot90`` puts the vertical axis the right way up in all three (superior up for sagittal
#: and coronal, anterior up for axial), but it does not settle the horizontal one, and the sagittal
#: view is the odd one out. Coronal and axial both have **X** across the page, so rot90 leaves them
#: with the patient's left on the viewer's left — neurological convention. Sagittal has **Y** across
#: the page instead, which leaves anterior on the *right*: the head faces backwards compared with how
#: every other view here, and every standard neuroimaging viewer, shows it. Hence the flip.
_PLANES = (("sagittal", 0, True), ("coronal", 1, False), ("axial", 2, False))


def samples_enabled(config: dict) -> bool:
    """Whether this job asked for client-local debug images.

    Absent key means off, so an existing ``config.json`` keeps its current behaviour.

    Args:
        config (dict): The parsed ``config.json``.

    Returns:
        bool: True when ``SAVE_DEBUG_SAMPLES`` is truthy.
    """
    return bool(config.get("SAVE_DEBUG_SAMPLES", False))


def plot_every(config: dict) -> int:
    """Iterations between tri-planar figures, from ``DEBUG_PLOT_EVERY`` (default 100).

    Args:
        config (dict): The parsed ``config.json``.

    Returns:
        int: The interval. Zero or negative disables the figures while leaving the grids on.
    """
    try:
        return int(config.get("DEBUG_PLOT_EVERY", DEFAULT_PLOT_EVERY))
    except (TypeError, ValueError):
        logger.warning(f"[DEBUG] DEBUG_PLOT_EVERY is not an integer; falling back to {DEFAULT_PLOT_EVERY}.")
        return DEFAULT_PLOT_EVERY


def due_for_plot(iteration: int, config: dict) -> bool:
    """Whether ``iteration`` (0-based) is one of the every-N steps that gets a tri-planar figure.

    True on iteration 0 as well as every Nth after it, so a run that crashes early still leaves one
    figure behind — the first is also the most diagnostic, being the only look at an untrained model.

    Args:
        iteration (int): Zero-based training step, counted across rounds and epochs (not within one
            epoch — an epoch shorter than the interval would otherwise fire once per epoch).
        config (dict): The parsed ``config.json``.

    Returns:
        bool: True when a figure is due and debug output is enabled at all.
    """
    if not samples_enabled(config):
        return False
    interval = plot_every(config)
    return interval > 0 and iteration % interval == 0


def debug_dir() -> Path:
    """Return (creating if needed) the directory debug images are written to.

    **Default: beside this app's own training script.** Resolved from ``__file__`` rather than the
    process working directory, which for an NVFLARE client is the workspace root shared by every app
    in the run. Each client unpacks its own copy of the app, so that path is inherently per-client
    and per-run — two simulated sites cannot overwrite each other's images, and nothing is written
    outside the job the trust agreed to run. That containment is the default precisely because it is
    the behaviour that has to hold on a trust.

    **Override: ``DEBUG_SAMPLES_DIR``.** In the simulator the default buries the images somewhere
    like ``/tmp/nvflare/<job>/flip_fedavg/site-1/simulate_job/app_site-1/custom/debug_samples/``,
    which is tedious to find and is wiped by the next run. The tutorial Makefiles therefore point
    this at ``fl-tutorials/data/debug_samples/<tutorial>/`` for local runs, beside the datasets and
    under the same gitignore.

    It is an environment variable rather than a ``config.json`` key on purpose: ``config.json`` is
    uploaded with the app and read on the trust, so a path in it would follow the job to production
    and ask a client to write patient-derived images to an operator-chosen location. The env var is
    set only by the local Makefile, is absent on a trust, and so cannot.
    """
    override = os.environ.get(DEBUG_DIR_ENV, "").strip()
    target = Path(override).expanduser().resolve() if override else Path(__file__).parent.resolve() / DEBUG_DIR_NAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _to_2d(batch: torch.Tensor) -> torch.Tensor:
    """Reduce a batch to something ``make_grid`` can tile, taking a mid-slice of any volume.

    Args:
        batch (torch.Tensor): ``(B, C, H, W)`` or ``(B, C, H, W, D)``.

    Returns:
        torch.Tensor: ``(B, C, H, W)`` — volumes sliced at the centre of the last axis, which for the
        RAS-oriented volumetric chain is the axial mid-slice.
    """
    if batch.ndim == 5:
        return batch[..., batch.shape[-1] // 2]
    return batch


def _name_parts(name: str, site_name: str, step: int | None) -> list[str]:
    """Filename parts shared by both writers: what, where, when — in that order."""
    parts = [name]
    if site_name:
        parts.append(site_name)
    if step is not None:
        parts.append(f"step{step:04d}")
    return parts


def _prepare(batch: torch.Tensor, max_images: int) -> torch.Tensor:
    """Detach, truncate and flatten a batch to greyscale 2-D images ready for tiling."""
    prepared = _to_2d(batch.detach().float().cpu()[:max_images])
    if prepared.shape[1] > 1:
        # Multi-channel here means a latent, not a colour image; show the first channel rather than
        # letting save_image interpret 3 channels as RGB.
        prepared = prepared[:, :1]
    return prepared


def save_grid(
    tensors: dict[str, torch.Tensor],
    name: str,
    config: dict,
    *,
    site_name: str = "",
    step: int | None = None,
) -> Path | None:
    """Write one PNG tiling the given batches, one row per entry. No-op unless enabled.

    Rows are normalised **jointly**, not per image: independent scaling would rescale a washed-out
    reconstruction back onto its input's range and hide precisely the intensity drift the comparison
    is meant to expose.

    Never raises. Debug output must not be able to fail a training round, so any error (a read-only
    workspace, a tensor of an unexpected rank) is logged and swallowed.

    Args:
        tensors (dict[str, torch.Tensor]): Batches to tile, one row each, in insertion order — e.g.
            ``{"input": images, "reconstruction": recon}``. Keys appear in the filename.
        name (str): Short label for what this grid shows, e.g. ``"reconstruction"``.
        config (dict): The parsed ``config.json``; read for ``SAVE_DEBUG_SAMPLES``/``DEBUG_SAMPLES_MAX``.
        site_name (str): Client name, included in the filename so a shared workspace cannot collide.
        step (int | None): Round or epoch counter, included in the filename to order the images.

    Returns:
        Path | None: The file written, or None when disabled or on any failure.
    """
    if not samples_enabled(config) or not tensors:
        return None

    try:
        max_images = int(config.get("DEBUG_SAMPLES_MAX", DEFAULT_MAX_IMAGES))
        rows = [_prepare(batch, max_images) for batch in tensors.values()]
        columns = min(row.shape[0] for row in rows)
        grid = make_grid(
            torch.cat([row[:columns] for row in rows]),
            nrow=columns,
            normalize=True,
            scale_each=False,
        )

        label = "_".join([name, *tensors.keys()]) if len(tensors) > 1 else name
        path = debug_dir() / ("_".join(_name_parts(label, site_name, step)) + ".png")

        save_image(grid, str(path))
        logger.info(f"[DEBUG] Wrote {columns} sample(s) to {path}")
        return path
    except Exception as err:
        logger.warning(f"[DEBUG] Could not write debug samples {name!r}: {err}")
        return None


def _central_slice(volume: torch.Tensor, axis: int, flip_lr: bool = False) -> np.ndarray:
    """The central slice of one 3-D volume along ``axis``, oriented for display.

    Args:
        volume (torch.Tensor): A single volume, ``(X, Y, Z)``, channel already removed.
        axis (int): Spatial axis to hold fixed — 0 sagittal, 1 coronal, 2 axial.
        flip_lr (bool): Mirror the slice horizontally after the rotation. True for the sagittal
            view only; see :data:`_PLANES` for why it alone needs it.

    Returns:
        np.ndarray: The 2-D slice, rotated so the axis that runs superior (or anterior, for the
        axial view) points up the page, and mirrored if this view needs it.
    """
    plane = volume.index_select(axis, torch.tensor([volume.shape[axis] // 2], device=volume.device)).squeeze(axis)
    rotated = np.rot90(plane.numpy())
    return np.fliplr(rotated) if flip_lr else rotated


def save_triplanar(
    volumes: dict[str, torch.Tensor],
    name: str,
    config: dict,
    *,
    site_name: str = "",
    step: int | None = None,
    sample: int = 0,
) -> Path | None:
    """Write a 3x2 tri-planar figure: one row per view, one column per batch. No-op unless enabled.

    Rows are sagittal / coronal / axial central cuts; columns are the entries of ``volumes`` in
    insertion order — so ``{"input": images, "reconstruction": recon}`` puts the input beside what
    the model made of it, in the same three planes.

    This is the figure that answers questions a mid-slice grid cannot. An autoencoder can look
    plausible on one axial slice while having learnt nothing about through-plane structure, and that
    shows up immediately in the sagittal and coronal views.

    **Each panel is scaled to its own range**, unlike :func:`save_grid`, which normalises jointly.
    The trade-off is deliberate and goes the other way here: a joint scale is the honest one for
    spotting intensity drift, but an early reconstruction spans a far wider range than the input
    (noise, including negatives), so a shared scale compresses the input to flat grey and neither
    column can be read. These figures exist to show *structure* — is there a brain, are the
    ventricles in the right place, does the tumour survive — and structure needs per-panel contrast.
    Read absolute intensity off the grids, not off these.

    Never raises: debug output must not be able to fail a training round.

    Args:
        volumes (dict[str, torch.Tensor]): Batches to compare, ``(B, C, X, Y, Z)``, one column each.
        name (str): Short label for what this figure shows, used in the filename.
        config (dict): The parsed ``config.json``.
        site_name (str): Client name, included in the filename so a shared workspace cannot collide.
        step (int | None): Iteration or epoch counter, included in the filename to order the figures.
        sample (int): Which item of the batch to show.

    Returns:
        Path | None: The file written, or None when disabled, when the tensors are not volumetric,
        or on any failure.
    """
    if not samples_enabled(config) or not volumes:
        return None

    try:
        import matplotlib

        # Agg before pyplot: an FL client has no display, and the default backend would try to pick
        # an interactive one and fail at import inside a training round.
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        picked = {}
        for label, batch in volumes.items():
            if batch.ndim != 5:
                logger.info(f"[DEBUG] {name!r}: {label} is not volumetric ({tuple(batch.shape)}); skipping figure.")
                return None
            picked[label] = batch.detach().float().cpu()[min(sample, batch.shape[0] - 1), 0]

        figure, axes = plt.subplots(len(_PLANES), len(picked), figsize=(3.2 * len(picked), 3.2 * len(_PLANES)))
        axes = np.atleast_2d(axes).reshape(len(_PLANES), len(picked))
        figure.patch.set_facecolor("black")

        for row, (view, axis, flip_lr) in enumerate(_PLANES):
            for column, (label, volume) in enumerate(picked.items()):
                axis_handle = axes[row][column]
                # No vmin/vmax: matplotlib scales each panel to its own min/max, which is the point
                # (see the note on contrast above). A flat panel is left to matplotlib, which
                # renders a constant array as mid-grey rather than dividing by a zero range.
                axis_handle.imshow(_central_slice(volume, axis, flip_lr), cmap="gray")
                axis_handle.set_facecolor("black")
                axis_handle.axis("off")
                if row == 0:
                    axis_handle.set_title(label, color="0.85", fontsize=11)
                if column == 0:
                    # A y-label survives axis("off"), which is why the view name rides here rather
                    # than in a title that would collide with the column headings.
                    axis_handle.set_ylabel(view, color="0.85", fontsize=10)
                    axis_handle.axis("on")
                    axis_handle.set_xticks([])
                    axis_handle.set_yticks([])
                    for spine in axis_handle.spines.values():
                        spine.set_visible(False)

        figure.tight_layout()
        path = debug_dir() / ("_".join(_name_parts(name, site_name, step)) + ".png")
        figure.savefig(path, facecolor=figure.get_facecolor(), dpi=110)
        plt.close(figure)
        logger.info(f"[DEBUG] Wrote tri-planar figure to {path}")
        return path
    except Exception as err:
        logger.warning(f"[DEBUG] Could not write tri-planar figure {name!r}: {err}")
        return None


# --------------------------------------------------------------------------------------------
# Analysis half: loss curves from a simulator log. Host-side only — see the module docstring.
# --------------------------------------------------------------------------------------------

#: Where ``make sim`` puts this tutorial's simulator workspace. Derived from the directory this file
#: sits in rather than hard-coded, so the same module is correct in every tutorial that ships it.
DEFAULT_WORKSPACE = Path("/tmp/nvflare") / Path(__file__).resolve().parent.parent.name / "flip_fedavg"

#: Log lines arrive as ``<timestamp> - <logger> - INFO - <message>``. Splitting the message off is
#: what stops the timestamp's own colons ("20:10:04") being read as data. A line that does *not*
#: match is a continuation of the message above it — the per-epoch summary is logged as one
#: multi-line string, so its numbers live on unprefixed lines — and is parsed whole.
_MESSAGE_RE = re.compile(r" - (?:DEBUG|INFO|WARNING|ERROR|CRITICAL) - (?P<message>.*)$")

#: One ``name: number`` pair inside a message. Deliberately generic: every tutorial here logs its
#: losses in that shape but with different names, and hard-coding one tutorial's names is what made
#: the previous version of this dead weight in the other two. The name charset excludes quotes,
#: commas and hyphens, which is what keeps a name from bridging across ``a: 1, b: 2`` or reaching
#: back into the logger prefix; ``nan``/``inf`` are accepted as values so a run that broke still
#: plots up to the point it broke.
_PAIR_RE = re.compile(
    r"(?P<name>[A-Za-z][\w ()]*?)\s*:\s*(?P<value>[-+]?(?:\d+\.?\d*(?:[eE][-+]?\d+)?|nan|inf))\b"
)

#: Fixed colours for the names this tree logs today, so two sites' figures can be read side by side
#: and successive runs stay comparable. Anything unrecognised is assigned from _PALETTE in sorted
#: order, which is deterministic for a given set of names.
COLOURS = {
    "l1": "#4ea1ff",
    "perceptual": "#ffd166",
    "gan": "#ff6b6b",
    "KL": "#9b7bff",
    "Total loss D": "#7bffb0",
    "Total loss DM": "#4ea1ff",
    "Validation loss (L1)": "#4ea1ff",
    "Validation SSIM (foreground)": "#ff9f4e",
    "Validation DM": "#ff9f4e",
    # The per-epoch aggregates, coloured to match the per-iteration term each one totals.
    "L1": "#4ea1ff",
    "Perceptual": "#ffd166",
    "GAN": "#ff6b6b",
    "KLD": "#9b7bff",
    "Total loss G": "#eeeeee",
}
_PALETTE = ("#4ea1ff", "#ffd166", "#ff6b6b", "#9b7bff", "#7bffb0", "#ff9f4e", "#59d4d4", "#d98cc4")

#: Smoothing for the per-iteration panel, which is unreadable raw at BATCH_SIZE 2.
EMA_ALPHA = 0.02

#: A series needs at least this many points to be worth an axis; it also filters out the stray
#: ``name: number`` pairs that appear once in a startup banner.
MIN_POINTS = 2

#: Series logged at the same cadence share a length. Anything within this fraction of the longest
#: series is treated as per-iteration; the rest is per-epoch. A tutorial that logs only per-epoch
#: (the two diffusion ones) therefore yields a single panel rather than an empty one.
#:
#: The same ratio is then applied *within* the per-epoch band, which is what keeps incidental
#: ``name: number`` pairs out of the figure. A log is full of them — ``localhost: 34953``,
#: ``PID: 1005143``, ``train size: 30`` — and they are not distinguishable from a metric by name or
#: by value. They are distinguishable by cadence: a metric is logged once per iteration or once per
#: epoch, so it appears as many times as there were iterations or epochs, while a startup line
#: appears once or twice however long the run goes on.
DENSE_RATIO = 0.9


def _ema(values: list[float], alpha: float = EMA_ALPHA) -> list[float]:
    """Exponential moving average, skipping non-finite points rather than propagating them."""
    out: list[float] = []
    acc = next((v for v in values if v == v and abs(v) != float("inf")), 0.0)
    for value in values:
        if value == value and abs(value) != float("inf"):
            acc = alpha * value + (1 - alpha) * acc
        out.append(acc)
    return out


def parse_log(path: Path) -> dict[str, list[float]]:
    """Return every ``name: number`` series found in one site's ``log.txt``, in order of appearance.

    Args:
        path (Path): The site's ``log.txt``.

    Returns:
        dict[str, list[float]]: Series name to its values, longest series first.
    """
    series: dict[str, list[float]] = {}
    with path.open(errors="replace") as handle:
        for line in handle:
            prefixed = _MESSAGE_RE.search(line)
            message = prefixed.group("message") if prefixed else line
            for match in _PAIR_RE.finditer(message):
                series.setdefault(match.group("name"), []).append(float(match.group("value")))
    return dict(sorted(series.items(), key=lambda item: -len(item[1])))


def split_by_cadence(series: dict[str, list[float]]) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
    """Split series into per-iteration and per-epoch groups by how often each was logged.

    Args:
        series (dict[str, list[float]]): Output of :func:`parse_log`.

    Returns:
        tuple: ``(dense, sparse)``; ``sparse`` is empty when everything shares one cadence.
    """
    usable = {name: values for name, values in series.items() if len(values) >= MIN_POINTS}
    if not usable:
        return {}, {}
    longest = max(len(values) for values in usable.values())
    dense = {n: v for n, v in usable.items() if len(v) >= longest * DENSE_RATIO}

    remainder = {n: v for n, v in usable.items() if len(v) < longest * DENSE_RATIO}
    if not remainder:
        return dense, {}
    epoch_cadence = max(len(values) for values in remainder.values())
    sparse = {n: v for n, v in remainder.items() if len(v) >= epoch_cadence * DENSE_RATIO}
    return dense, sparse


def _colour_for(name: str, index: int) -> str:
    return COLOURS.get(name, _PALETTE[index % len(_PALETTE)])


def _style(axis, title: str) -> None:
    """Apply the shared dark styling to one axis."""
    axis.set_facecolor("#111111")
    axis.set_title(title, color="#eeeeee")
    axis.tick_params(colors="#bbbbbb")
    axis.grid(True, color="#333333", linewidth=0.5)
    for spine in axis.spines.values():
        spine.set_color("#444444")
    axis.xaxis.label.set_color("#bbbbbb")
    axis.yaxis.label.set_color("#bbbbbb")


def _legend(axis, handles=None, labels=None) -> None:
    """Legend outside the axes on the right, so it cannot sit on top of the curves.

    The generic parser finds however many series a tutorial logs — eight for the autoencoder's
    per-epoch panel — which is more than fits inside the plot area.
    """
    axis.legend(
        *([handles, labels] if handles is not None else []),
        loc="upper left",
        bbox_to_anchor=(1.12, 1.0),
        fontsize=8,
        facecolor="#1c1c1c",
        edgecolor="#444444",
        labelcolor="#eeeeee",
    )


def plot_site(site: str, series: dict[str, list[float]], out_dir: Path) -> Path:
    """Write the loss figure for one site and return its path.

    The per-iteration panel is log-scaled when every value is positive: the terms routinely span two
    decades, and on a linear axis the smallest of them — usually the one that is misweighted, which
    is exactly what you are looking for — is a flat line on the floor.

    Args:
        site (str): Site name, used in the title and filename.
        series (dict[str, list[float]]): Output of :func:`parse_log`.
        out_dir (Path): Directory to write into; created if absent.

    Returns:
        Path: The figure written.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dense, sparse = split_by_cadence(series)
    panels = [p for p in (dense, sparse) if p]
    figure, axes = plt.subplots(len(panels), 1, figsize=(13, 4 * len(panels)), facecolor="#111111", squeeze=False)

    for axis, panel in zip(axes.flatten(), panels, strict=True):
        is_dense = panel is dense
        # SSIM is bounded [0, 1] where the losses are not, so it gets its own axis or it flattens
        # every other series against the top of the plot.
        twin_names = [n for n in panel if "SSIM" in n] if not is_dense else []
        twin = axis.twinx() if twin_names else None

        for index, (name, values) in enumerate(panel.items()):
            colour = _colour_for(name, index)
            target = twin if name in twin_names else axis
            if is_dense:
                target.plot(values, color=colour, alpha=0.15, linewidth=0.6)
                target.plot(_ema(values), color=colour, linewidth=1.8, label=name)
            else:
                steps = range(1, len(values) + 1)
                target.plot(steps, values, color=colour, marker="o", markersize=3, label=name)

        # Log y whenever everything is positive. The terms routinely span two or three decades, and
        # on a linear axis the smallest of them — usually the misweighted one, which is exactly what
        # you are looking for — is a flat line on the floor. On the per-epoch panel the first epoch
        # is several times any later one, so a linear axis spends most of its height on it.
        plotted = [values for name, values in panel.items() if name not in twin_names]
        finite = [v for values in plotted for v in values if v == v and abs(v) != float("inf")]
        if finite and min(finite) > 0:
            axis.set_yscale("log")
        axis.set_xlabel("training iteration" if is_dense else "local epoch")
        axis.set_ylabel("weighted contribution to $\\mathcal{L}_G$" if is_dense else "loss")
        _style(axis, f"{site} — {'per-iteration loss terms' if is_dense else 'per-epoch summary'}")

        if twin is not None:
            twin.set_ylabel(" / ".join(twin_names), color=COLOURS.get(twin_names[0], _PALETTE[5]))
            twin.tick_params(axis="y", colors=COLOURS.get(twin_names[0], _PALETTE[5]))
            for spine in twin.spines.values():
                spine.set_color("#444444")
            handles, labels = axis.get_legend_handles_labels()
            extra_handles, extra_labels = twin.get_legend_handles_labels()
            _legend(axis, handles + extra_handles, labels + extra_labels)
        else:
            _legend(axis)

    figure.tight_layout()
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"losses_{site}.png"
    figure.savefig(destination, dpi=110, facecolor=figure.get_facecolor())
    plt.close(figure)
    return destination


def main() -> None:
    """Plot every site's losses from a simulator workspace. Host-side entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE, help="simulator workspace root")
    parser.add_argument("--out-dir", type=Path, default=None, help="defaults to $DEBUG_SAMPLES_DIR, else ./")
    args = parser.parse_args()

    out_dir = args.out_dir or Path(os.environ.get(DEBUG_DIR_ENV, "."))

    logs = sorted(args.workspace.glob("site-*/log.txt"))
    if not logs:
        raise SystemExit(f"no site logs under {args.workspace} — pass --workspace")

    for log in logs:
        site = log.parent.name
        series = parse_log(log)
        dense, sparse = split_by_cadence(series)
        if not dense and not sparse:
            print(f"{site}: no scalar lines yet, skipping")
            continue
        destination = plot_site(site, series, out_dir)
        counts = ", ".join(f"{name} x{len(values)}" for name, values in list(dense.items())[:4])
        print(f"{site}: {counts} -> {destination}")


if __name__ == "__main__":
    main()
