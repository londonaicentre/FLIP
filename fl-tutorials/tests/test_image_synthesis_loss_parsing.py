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

"""The log parser behind ``plot_utils``' loss curves, which is generic and therefore guessy.

``plot_utils.parse_log`` recovers training curves by scraping ``name: number`` pairs out of a
simulator log, rather than by matching one tutorial's loss names. That is what lets the same module
ship to all three image-synthesis tutorials, which log different terms under different names — but
it means the parser has to tell metrics apart from every other number a log contains, using only
the shape of the text.

Both halves of that go wrong quietly, and both did:

* **Missing a series.** The per-epoch summary is logged as a single multi-line string, so its
  numbers sit on continuation lines with no ``- INFO -`` prefix. A parser that keys on the prefix
  silently drops them, and the figure comes out with an empty panel rather than an error.
* **Inventing one.** A log is full of incidental pairs — ``localhost: 34953``, ``PID: 1005143``,
  ``train size: 30``. They are not distinguishable from a metric by name or by value, only by
  cadence: a metric appears once per iteration or once per epoch, an incidental pair appears once
  or twice however long the run is. The first version of this plotted a port number as a loss
  curve.

The fixtures below are real lines from an autoencoder run, kept verbatim — including the awkward
ones (the missing space after a comma, the trailing whitespace, the multi-line summary).
"""

from __future__ import annotations

import math
import sys

import pytest
from tutorial_apps import TUTORIALS_ROOT

_SYNTHESIS = TUTORIALS_ROOT / "nvflare" / "image_synthesis"
_AUTOENCODER = _SYNTHESIS / "autoencoder"

# Verbatim from /tmp/nvflare/autoencoder/flip_fedavg/site-1/log.txt.
ITERATION_LINE = (
    "2026-09-24 21:22:20,945 - TaskScriptRunner - INFO - "
    "KL: 0.0018423980800434947, l1: 0.036178652197122574, "
    "perceptual: 0.0003310332540422678, gan: 0.02848029136657715\n"
)
EPOCH_LINES = (
    "2026-09-24 21:22:22,884 - __main__ - INFO - Epoch 59 / 150;\n"
    " Total loss G: 0.06306873741559685, GAN: 0.025914924452081323 "
    "Perceptual: 0.0005602158879810304, L1: 0.034761849930509924 KLD: 0.0018317474095965736 \n"
    "Total loss D: 0.025639045494608582,Validation loss (L1): 0.04388631265610456, "
    "Validation SSIM (foreground): 0.21366519071161746\n"
)
NOISE_LINES = (
    "2026-09-24 08:41:02,001 - CoreCell - INFO - server: created backbone external connector "
    "to tcp://localhost: 34953\n"
    "2026-09-24 08:41:02,002 - ClientRunner - INFO - PID: 1005143\n"
    "2026-09-24 08:41:05,113 - __main__ - INFO - train size: 30\n"
)


@pytest.fixture(scope="module")
def plot_utils():  # noqa: ANN201 - imported from the app dir, so it has no import-time type
    sys.path.insert(0, str(_AUTOENCODER / "app_files"))
    try:
        import plot_utils as module
    finally:
        sys.path.remove(str(_AUTOENCODER / "app_files"))
    return module


def _write(tmp_path, text: str):  # noqa: ANN001, ANN202
    log = tmp_path / "log.txt"
    log.write_text(text)
    return log


def test_a_single_line_message_yields_one_point_per_term(plot_utils, tmp_path) -> None:
    series = plot_utils.parse_log(_write(tmp_path, ITERATION_LINE))
    assert {name: len(values) for name, values in series.items()} == {
        "KL": 1,
        "l1": 1,
        "perceptual": 1,
        "gan": 1,
    }
    assert series["l1"] == [0.036178652197122574]


def test_a_multi_line_message_is_not_dropped(plot_utils, tmp_path) -> None:
    """The per-epoch summary's numbers live on unprefixed continuation lines.

    Keying on the ``- INFO -`` prefix loses every one of them, leaving an empty per-epoch panel and
    no error to say why.
    """
    series = plot_utils.parse_log(_write(tmp_path, EPOCH_LINES))
    assert "Total loss D" in series, "the continuation lines were dropped"
    assert series["Validation SSIM (foreground)"] == [0.21366519071161746]
    # No space after the comma, and parenthesised names: both must survive.
    assert series["Validation loss (L1)"] == [0.04388631265610456]


def test_the_timestamp_is_not_read_as_data(plot_utils, tmp_path) -> None:
    """``21:22:20,945`` is three colon-separated numbers at the head of every single line."""
    series = plot_utils.parse_log(_write(tmp_path, ITERATION_LINE))
    assert not [name for name in series if any(character.isdigit() for character in name)] or set(
        series
    ) == {"KL", "l1", "perceptual", "gan"}


def test_non_finite_values_are_kept_rather_than_skipped(plot_utils, tmp_path) -> None:
    """A run that NaNs must still plot up to the point it broke, so the break is visible."""
    line = ITERATION_LINE.replace("perceptual: 0.0003310332540422678", "perceptual: nan")
    series = plot_utils.parse_log(_write(tmp_path, line))
    assert len(series["perceptual"]) == 1
    assert math.isnan(series["perceptual"][0])


def test_incidental_pairs_are_kept_out_of_the_figure(plot_utils, tmp_path) -> None:
    """Ports and PIDs parse as ``name: number`` and must not reach a panel.

    Cadence is the only thing separating them from a metric, so the log needs enough iterations and
    epochs for that to be meaningful — as any real one has.
    """
    text = NOISE_LINES + ITERATION_LINE * 400 + EPOCH_LINES * 5
    dense, sparse = plot_utils.split_by_cadence(plot_utils.parse_log(_write(tmp_path, text)))

    assert set(dense) == {"KL", "l1", "perceptual", "gan"}
    assert "Total loss D" in sparse and "Validation SSIM (foreground)" in sparse
    for panel in (dense, sparse):
        assert "localhost" not in panel and "PID" not in panel and "train size" not in panel


def test_a_tutorial_logging_only_per_epoch_gets_one_populated_panel(plot_utils, tmp_path) -> None:
    """The two diffusion tutorials log no per-iteration line, and must not yield an empty panel."""
    text = "".join(
        f"2026-09-24 08:4{index % 10}:00,000 - __main__ - INFO - Total loss DM: 0.3{index}\n"
        for index in range(20)
    )
    dense, sparse = plot_utils.split_by_cadence(plot_utils.parse_log(_write(tmp_path, text)))
    assert set(dense) == {"Total loss DM"}
    assert sparse == {}


def test_the_default_workspace_follows_the_tutorial_the_copy_sits_in(plot_utils) -> None:
    """One shared file, three tutorials, three simulator workspaces.

    The module is byte-identical in all three, so the default cannot be a literal — it is derived
    from the directory the copy sits in, and a wrong derivation sends every tutorial to the
    autoencoder's logs.
    """
    assert plot_utils.DEFAULT_WORKSPACE.parent.name == "autoencoder"
    assert plot_utils.DEFAULT_WORKSPACE.name == "flip_fedavg"
