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

"""Client-side differential privacy for FLIP's Flower apps.

FLIP's NVFLARE apps privatise every training result before it leaves the client, via the
``PercentilePrivacy`` result filter wired into ``config_fed_client.json``. This module is the
Flower counterpart: a ``Mod`` that clips the local update to a fixed L2 norm and adds calibrated
Gaussian noise **on the SuperNode**, so the fl-server only ever receives a privatised update.
Unlike the NVFLARE filter — percentile sparsification with no noise — this is a real
(epsilon, delta) mechanism.

The maths is Flower's own (``compute_clip_model_update`` and ``add_gaussian_noise_inplace`` from
``flwr.supercore.differential_privacy``), and the noise scale is the formula used by
``flwr.clientapp.mod.LocalDpMod``. Two things are added on top of instantiating ``LocalDpMod``
directly:

* **A config toggle.** Mods are constructed at import time, before ``run_config`` exists, so a bare
  ``LocalDpMod(...)`` bakes its parameters into the module source. Reading them from
  ``context.run_config`` on each call gives the same ergonomics as the NVFLARE filter's ``off``
  flag: DP-on and DP-off runs use an identical app, with no component added or removed.
* **Integer buffers are passed through untouched.** Flower's ``clip_inputs_inplace`` scales each
  array in place by a float, which raises ``UFuncTypeError`` on an integer array the moment
  clipping actually engages. Real models hit this: MONAI's ``DenseNet121`` state dict carries 121
  int64 ``num_batches_tracked`` BatchNorm counters, and the spleen ``UNet`` (``norm="batch"``)
  carries its own. Those counters are step counts rather than learned parameters, so only the
  floating-point arrays are clipped and noised.

Usage — training only, mirroring the scope of the NVFLARE filter::

    from flip.flower.privacy import flip_local_dp_mod

    app = ClientApp()

    @app.train(mods=[flip_local_dp_mod])
    def train(msg: Message, context: Context) -> Message:
        ...

Like ``flip.flower.strategy``, importing this module requires the ``flwr`` package (present in the
Flower FL images).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from logging import INFO, WARNING
from typing import Any

import numpy as np
from flwr.app import Array, ArrayRecord, Context, Message
from flwr.clientapp.typing import ClientAppCallable
from flwr.common import MessageType
from flwr.common.logger import log
from flwr.supercore.differential_privacy import (
    add_gaussian_noise_inplace,
    compute_clip_model_update,
)

__all__ = ["LocalDpConfig", "flip_local_dp_mod"]

# ``run_config`` keys, as declared under ``[tool.flwr.app.config]`` in the app's pyproject.toml.
CONFIG_ENABLED = "dp-enabled"
CONFIG_CLIPPING_NORM = "dp-clipping-norm"
CONFIG_SENSITIVITY = "dp-sensitivity"
CONFIG_EPSILON = "dp-epsilon"
CONFIG_DELTA = "dp-delta"


@dataclass(frozen=True)
class LocalDpConfig:
    """Parameters of the local (epsilon, delta) mechanism, with FLIP's defaults.

    The defaults are deliberately **utility-first**: they keep the shipped tutorials trainable
    while the mechanism is live, so a DP-on run still converges and the effect stays visible. They
    are not a defensible privacy budget. A real budget calibrates ``sensitivity`` to the local
    dataset (``2 * clipping_norm / |D|`` for an average-of-examples update) and accounts for
    composition across rounds, neither of which this mechanism does — each round spends the budget
    again.

    Attributes:
        enabled (bool): Whether to privatise the update at all. False makes the mod a pass-through,
            mirroring the ``off`` flag on FLIP's NVFLARE ``PercentilePrivacy`` filter. Defaults to
            True — DP is on by default.
        clipping_norm (float): L2 norm the update is clipped to before noise is added. Defaults
            to 1.0.
        sensitivity (float): Sensitivity of the local update, i.e. how much one training example
            can move it. Defaults to 1e-4.
        epsilon (float): Privacy budget. Smaller means more privacy and more noise. Defaults
            to 10.0.
        delta (float): Probability that the guarantee fails outright. Defaults to 1e-5.
    """

    enabled: bool = True
    clipping_norm: float = 1.0
    sensitivity: float = 1e-4
    epsilon: float = 10.0
    delta: float = 1e-5

    def __post_init__(self) -> None:
        """Reject parameter values that have no valid mechanism.

        Raises:
            ValueError: If any parameter is outside its permitted range.
        """
        if self.clipping_norm <= 0:
            raise ValueError(f"{CONFIG_CLIPPING_NORM} must be positive, got {self.clipping_norm}.")
        if self.sensitivity < 0:
            raise ValueError(f"{CONFIG_SENSITIVITY} must be non-negative, got {self.sensitivity}.")
        # Stricter than Flower's LocalDpMod, which permits epsilon == 0 and then divides by it.
        if self.epsilon <= 0:
            raise ValueError(f"{CONFIG_EPSILON} must be positive, got {self.epsilon}.")
        if not 0 < self.delta < 1:
            raise ValueError(f"{CONFIG_DELTA} must lie in (0, 1), got {self.delta}.")

    @classmethod
    def from_run_config(cls, run_config: Mapping[str, Any]) -> LocalDpConfig:
        """Build a config from a Flower ``run_config``, falling back to the defaults per key.

        Args:
            run_config (Mapping[str, Any]): The app's ``context.run_config``. Keys absent from it
                take this class's default, so an app that declares none still gets DP on.

        Returns:
            LocalDpConfig: The validated configuration for this run.

        Raises:
            ValueError: If a declared key holds a value of the wrong type, or a value outside its
                permitted range.
        """
        defaults = cls()
        return cls(
            enabled=_config_bool(run_config, CONFIG_ENABLED, defaults.enabled),
            clipping_norm=_config_float(run_config, CONFIG_CLIPPING_NORM, defaults.clipping_norm),
            sensitivity=_config_float(run_config, CONFIG_SENSITIVITY, defaults.sensitivity),
            epsilon=_config_float(run_config, CONFIG_EPSILON, defaults.epsilon),
            delta=_config_float(run_config, CONFIG_DELTA, defaults.delta),
        )

    @property
    def noise_stddev(self) -> float:
        """Standard deviation of the Gaussian noise added to the clipped update.

        The analytic Gaussian mechanism's ``sensitivity * sqrt(2 * ln(1.25 / delta)) / epsilon``,
        the same expression Flower's ``LocalDpMod`` uses.

        Returns:
            float: The noise standard deviation, in the units of the model parameters.
        """
        return self.sensitivity * math.sqrt(2 * math.log(1.25 / self.delta)) / self.epsilon


def flip_local_dp_mod(msg: Message, context: Context, call_next: ClientAppCallable) -> Message:
    """Clip and noise the local model update before it leaves the SuperNode.

    Runs the app, then privatises the floating-point arrays of its reply in place of the raw
    update. Non-training messages, disabled configs and error replies all pass straight through.

    Args:
        msg (Message): The message received from the ServerApp, carrying the global arrays.
        context (Context): The ClientApp context; ``run_config`` supplies the DP parameters.
        call_next (ClientAppCallable): The next mod, or the ClientApp itself.

    Returns:
        Message: The reply, with its update clipped and noised unless DP was skipped.

    Raises:
        ValueError: If the DP parameters are invalid, or the messages do not carry exactly one
            ArrayRecord with matching keys. Failing loudly is deliberate — silently skipping the
            mechanism would release a raw update from the trust.
    """
    # ``@app.train(mods=[...])`` already scopes this to training, but the guard makes the scope
    # structural for an app that instead registers the mod on the whole ClientApp: only training
    # results are privatised, matching the NVFLARE filter's "train" task scope. Note that a query
    # reply CAN carry an ArrayRecord — an app that serves the model over query must not rely on
    # this mod to privatise it. Message types are "<category>" or "<category>.<action>", so
    # compare the category alone.
    if msg.metadata.message_type.split(".")[0] != MessageType.TRAIN:
        return call_next(msg, context)

    config = LocalDpConfig.from_run_config(context.run_config)
    if not config.enabled:
        # WARNING, not INFO: a raw update leaving the trust is the event an operator audits for.
        log(WARNING, "FLIP local DP is off (%s=false): the update is released unprivatised.", CONFIG_ENABLED)
        return call_next(msg, context)
    if config.noise_stddev == 0.0:
        log(
            WARNING,
            "FLIP local DP noise stddev is 0 (%s=0): the update will be clipped but NOT noised — "
            "clipping alone carries no (epsilon, delta) guarantee.",
            CONFIG_SENSITIVITY,
        )

    # Read the global arrays before running the app so a malformed message fails before a training
    # epoch is spent on it. The record is materialised to numpy only after the reply arrives, so
    # the two copies of the model do not coexist for the whole of training.
    _, global_record = _single_array_record(msg, "message from the ServerApp")

    reply = call_next(msg, context)
    if reply.has_error():
        return reply

    reply_key, reply_record = _single_array_record(reply, "reply from the ClientApp")
    reply_keys = list(reply_record.keys())
    if list(global_record.keys()) != reply_keys:
        raise ValueError(
            "FLIP local DP requires the reply's ArrayRecord to carry the same keys, in the same "
            "order, as the one sent by the ServerApp; the update cannot be aligned to the global "
            "model otherwise, so it is not being released."
        )

    global_arrays = global_record.to_numpy_ndarrays()
    # keep_input=False drops each serialized entry as it materialises (hence reply_keys above):
    # reply_record is replaced wholesale below, so keeping its buffered bytes alongside the numpy
    # copies would only double the update's peak memory. The incoming msg's record is left intact —
    # it belongs to the framework.
    reply_arrays = reply_record.to_numpy_ndarrays(keep_input=False)

    # Integer entries (BatchNorm's num_batches_tracked and friends) are step counters, not learned
    # parameters. They are excluded because Flower's in-place float scaling cannot write back into
    # an integer array once clipping engages. Any OTHER non-float dtype (bool masks, complex
    # weights, ...) has no counter precedent — it may carry learned values, so passing it through
    # would be a silent unprivatised release: fail loudly instead. (Integer-quantised weights would
    # also pass the integer test; dtype alone cannot tell them from counters — see the docs.)
    float_indices: list[int] = []
    unclassifiable: dict[str, str] = {}
    for i, array in enumerate(reply_arrays):
        if np.issubdtype(array.dtype, np.floating):
            float_indices.append(i)
        elif not np.issubdtype(array.dtype, np.integer):
            unclassifiable[reply_keys[i]] = str(array.dtype)
    if unclassifiable:
        raise ValueError(
            "FLIP local DP privatises floating-point arrays and passes through integer step "
            f"counters, but the update carries arrays it cannot classify ({unclassifiable}); "
            "they may hold learned values, so the update is not being released."
        )
    if not float_indices:
        raise ValueError(
            "FLIP local DP found no floating-point arrays in the update, so there is nothing it "
            "can privatise; the update is not being released."
        )

    updates = [reply_arrays[i] for i in float_indices]
    baselines = [global_arrays[i] for i in float_indices]

    # compute_clip_model_update rebinds list entries rather than mutating the arrays, so the
    # clipped values have to be read back off `updates` — which is also what the noise lands on.
    compute_clip_model_update(updates, baselines, config.clipping_norm)
    add_gaussian_noise_inplace(updates, config.noise_stddev)
    for position, index in enumerate(float_indices):
        reply_arrays[index] = updates[position]

    reply.content[reply_key] = ArrayRecord(
        dict(zip(reply_keys, (Array(np.asarray(v)) for v in reply_arrays), strict=True))
    )
    log(
        INFO,
        "FLIP local DP: update clipped to L2 norm %.4f and Gaussian noise of stddev %.3e added "
        "across %d array(s); %d integer array(s) passed through.",
        config.clipping_norm,
        config.noise_stddev,
        len(float_indices),
        len(reply_arrays) - len(float_indices),
    )
    return reply


def _single_array_record(message: Message, description: str) -> tuple[str, ArrayRecord]:
    """Return the message's one and only ArrayRecord, with the key it is stored under.

    Args:
        message (Message): The message to read.
        description (str): How to name the message in the error, e.g. "reply from the ClientApp".

    Returns:
        tuple[str, ArrayRecord]: The record's key and the record itself.

    Raises:
        ValueError: If the message does not carry exactly one ArrayRecord.
    """
    records = message.content.array_records
    if len(records) != 1:
        raise ValueError(
            f"FLIP local DP expects exactly one ArrayRecord in the {description}, found "
            f"{len(records)}. It cannot tell which one holds the model, so the update is not "
            "being released."
        )
    return next(iter(records.items()))


def _config_bool(run_config: Mapping[str, Any], key: str, default: bool) -> bool:
    """Read a boolean from ``run_config``.

    Args:
        run_config (Mapping[str, Any]): The app's run config.
        key (str): Key to read.
        default (bool): Value to use when the key is absent.

    Returns:
        bool: The configured value, or the default.

    Raises:
        ValueError: If the key holds a non-boolean.
    """
    value = run_config.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean, got {value!r}.")
    return value


def _config_float(run_config: Mapping[str, Any], key: str, default: float) -> float:
    """Read a float from ``run_config``, accepting an int for values written without a decimal.

    Args:
        run_config (Mapping[str, Any]): The app's run config.
        key (str): Key to read.
        default (float): Value to use when the key is absent.

    Returns:
        float: The configured value, or the default.

    Raises:
        ValueError: If the key holds something that is not a number. Booleans are rejected even
            though Python treats them as ints.
    """
    value = run_config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number, got {value!r}.")
    return float(value)
