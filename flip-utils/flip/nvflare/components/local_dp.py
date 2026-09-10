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
"""Local ``(epsilon, delta)`` differential privacy for NVFLARE clients.

The NVFLARE counterpart of :mod:`flip.flower.privacy`: a client result filter that clips the local
update to a fixed L2 norm and adds calibrated Gaussian noise before the result leaves the site, so
the FL server only ever receives a privatised update.

This exists because FLIP's two backends otherwise offer different things under the same name.
``PercentilePrivacy`` — NVFLARE's default here — zeroes the smallest-magnitude components of the
update and truncates the survivors. That bounds what one round reveals, but adds no noise and
carries no ``(epsilon, delta)`` guarantee; NVIDIA's own documentation calls it "truncation of
weights by percentile" and reserves the differential-privacy label for its sparse-vector filter.
That sparse-vector filter is not the answer either: it is a *selection* primitive whose advantage is
that below-threshold queries are free, which never applies to FedAvg's dense per-round update. See
FLIP#1145 for the measurements.

Two limits worth stating plainly, both shared with the Flower mod:

* **No composition accounting.** Each round spends the budget again. The parameters describe one
  round's mechanism, not a per-run guarantee.
* **Sensitivity is a parameter, not a derivation.** A defensible budget calibrates it to the local
  dataset (``2 * clipping_norm / |D|`` for an average-of-examples update); the default is
  utility-first so a DP-on tutorial still converges.

The numerics deliberately mirror ``flwr.supercore.differential_privacy``'s ``clip_inputs_inplace``
and ``add_gaussian_noise_inplace`` rather than importing them: ``flwr`` is a dev-group dependency of
flip-utils, never a runtime one, so the NVFLARE path must not import it. A parity test asserts the
two implementations agree element-for-element wherever ``flwr`` is installed.
"""

from __future__ import annotations

import math

import numpy as np
from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.dxo_filter import DXOFilter
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

__all__ = ["LocalDifferentialPrivacy"]


def _global_l2_norm(arrays: list[np.ndarray]) -> float:
    """The L2 norm of the arrays taken together, as one flat vector.

    Reduced per array and combined in Python floats, exactly as Flower's ``get_norm`` does. One
    concatenated ``np.linalg.norm`` is the same quantity mathematically but rounds differently in
    float32, which would put the two backends' clipped updates a few ulps apart.
    """
    return float(math.sqrt(sum(float(np.linalg.norm(np.asarray(array).flat)) ** 2 for array in arrays)))


def _clip_inplace(arrays: list[np.ndarray], clipping_norm: float) -> float:
    """Scale ``arrays`` in place so their combined L2 norm is at most ``clipping_norm``.

    The FlatClip of https://arxiv.org/abs/1710.06963, matching Flower's ``clip_inputs_inplace``: one
    scaling factor across the whole update, so the update's direction is preserved exactly and only
    its length is bounded.

    Args:
        arrays (list[np.ndarray]): Floating-point arrays making up the update. Modified in place.
        clipping_norm (float): The L2 bound.

    Returns:
        float: The scaling factor applied — 1.0 when the update was already inside the bound.
    """
    norm = _global_l2_norm(arrays)
    # Flower divides unguarded and relies on `min(1, inf)` for the all-zero update; the guard is
    # explicit here so an empty round cannot raise a warning storm. Same resulting factor.
    scaling_factor = 1.0 if norm == 0.0 else min(1.0, clipping_norm / norm)
    for array in arrays:
        array *= scaling_factor
    return scaling_factor


def _add_gaussian_noise_inplace(arrays: list[np.ndarray], std_dev: float) -> None:
    """Add ``N(0, std_dev)`` noise to every element, in place.

    Mirrors Flower's ``add_gaussian_noise_inplace``, including drawing from numpy's global RNG and
    casting the noise to each array's dtype before adding, so a seeded run reproduces exactly.

    Args:
        arrays (list[np.ndarray]): Floating-point arrays to noise. Modified in place.
        std_dev (float): Standard deviation of the noise.
    """
    for array in arrays:
        array += np.random.normal(0, std_dev, array.shape).astype(array.dtype)


class LocalDifferentialPrivacy(DXOFilter):
    """Clip and noise a client's weight diff before it leaves the site.

    Wired as a client ``TASK_RESULT`` filter on the train task. It must be ordered AFTER
    ``KeepOnlyVars`` on a head-only job, so the norm is computed over the trainable parameters
    rather than the frozen backbone's all-zero diffs.
    """

    def __init__(
        self,
        clipping_norm: float = 1.0,
        sensitivity: float = 1e-4,
        epsilon: float = 10.0,
        delta: float = 1e-5,
        off: bool = False,
    ) -> None:
        """Initialise the filter.

        Args:
            clipping_norm (float): L2 norm the update is clipped to before noise is added. Defaults
                to 1.0, matching ``flip.flower.privacy``.
            sensitivity (float): How much one training example can move the update. Defaults to
                1e-4. See the module docstring: this is a parameter, not a derivation.
            epsilon (float): Privacy budget for one round. Smaller means more privacy and more
                noise. Defaults to 10.0.
            delta (float): Probability the guarantee fails outright. Defaults to 1e-5.
            off (bool): When True the filter is a pass-through, mirroring ``PercentilePrivacy``'s
                ``off`` so DP-on and DP-off runs use an identical job. Defaults to False.

        Raises:
            ValueError: If any parameter is outside its permitted range.
        """
        super().__init__(
            supported_data_kinds=[DataKind.WEIGHTS, DataKind.WEIGHT_DIFF],
            # WEIGHTS is declared filterable so it reaches process_dxo and can be REFUSED there.
            # Leaving it out would make the filter skip such a result silently, which is exactly
            # the unprivatised release this component exists to prevent.
            data_kinds_to_filter=[DataKind.WEIGHTS, DataKind.WEIGHT_DIFF],
        )
        # isfinite guards are load-bearing: NaN compares False against everything, so a bare range
        # check waves it through and poisons the noise stddev; inf silently disables clipping or
        # zeroes the noise. Delta needs none — NaN and inf both fail its interval test already.
        if not math.isfinite(clipping_norm) or clipping_norm <= 0:
            raise ValueError(f"clipping_norm must be positive and finite, got {clipping_norm}.")
        if not math.isfinite(sensitivity) or sensitivity < 0:
            raise ValueError(f"sensitivity must be non-negative and finite, got {sensitivity}.")
        if not math.isfinite(epsilon) or epsilon <= 0:
            raise ValueError(f"epsilon must be positive and finite, got {epsilon}.")
        if not 0 < delta < 1:
            raise ValueError(f"delta must lie in (0, 1), got {delta}.")
        self.clipping_norm = clipping_norm
        self.sensitivity = sensitivity
        self.epsilon = epsilon
        self.delta = delta
        self.off = off

    @property
    def noise_stddev(self) -> float:
        """Standard deviation of the Gaussian noise, in the units of the model parameters.

        The analytic Gaussian mechanism's ``sensitivity * sqrt(2 ln(1.25 / delta)) / epsilon`` —
        the same expression ``flip.flower.privacy.LocalDpConfig.noise_stddev`` uses, so a job
        configured identically on either backend gets the same mechanism.

        Returns:
            float: The noise standard deviation.
        """
        return self.sensitivity * math.sqrt(2 * math.log(1.25 / self.delta)) / self.epsilon

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> None | DXO:
        """Clip and noise the update carried by ``dxo``.

        Args:
            dxo (DXO): The client's training result.
            shareable (Shareable): The shareable the DXO belongs to.
            fl_ctx (FLContext): Context provided by the workflow.

        Returns:
            None | DXO: The privatised DXO, or the unchanged DXO when the filter is off.

        Raises:
            ValueError: If the result is not a weight diff, carries an array whose dtype cannot be
                classified, or carries no floating-point array at all. Each of those is refused
                rather than forwarded: releasing a raw update from a trust is the failure this
                component exists to prevent, so it fails closed.
        """
        if self.off:
            # WARNING, not INFO: a raw update leaving the trust is the event an operator audits for.
            self.log_warning(fl_ctx, "FLIP local DP is off: the update is released unprivatised.")
            return dxo

        if dxo.data_kind != DataKind.WEIGHT_DIFF:
            raise ValueError(
                f"FLIP local DP privatises a weight diff, but this result is {dxo.data_kind}. The "
                "mechanism clips the UPDATE's norm, which absolute weights do not carry, and this "
                "filter has no copy of the global model to subtract. Return the result with "
                'params_type="DIFF"; the update is not being released.'
            )

        float_names: list[str] = []
        passthrough_names: list[str] = []
        unclassifiable: dict[str, str] = {}
        for name, value in dxo.data.items():
            dtype = np.asarray(value).dtype
            if np.issubdtype(dtype, np.floating):
                float_names.append(name)
            elif np.issubdtype(dtype, np.integer):
                # BatchNorm's num_batches_tracked and friends are step counters, not learned
                # parameters, and float noise cannot be written back into them. Integer-QUANTISED
                # weights would also land here; dtype alone cannot tell the two apart.
                passthrough_names.append(name)
            else:
                unclassifiable[name] = str(dtype)
        if unclassifiable:
            raise ValueError(
                "FLIP local DP privatises floating-point arrays and passes through integer step "
                f"counters, but the update carries arrays it cannot classify ({unclassifiable}); "
                "they may hold learned values, so the update is not being released."
            )
        if not float_names:
            raise ValueError(
                "FLIP local DP found no floating-point arrays in the update, so there is nothing "
                "it can privatise; the update is not being released."
            )

        updates = [np.array(dxo.data[name], copy=True) for name in float_names]
        scaling_factor = _clip_inplace(updates, self.clipping_norm)
        _add_gaussian_noise_inplace(updates, self.noise_stddev)
        for name, array in zip(float_names, updates, strict=True):
            dxo.data[name] = array

        self.log_info(
            fl_ctx,
            f"FLIP local DP: update clipped to L2 norm {self.clipping_norm:.4f} "
            f"(scaling factor {scaling_factor:.4f}) and Gaussian noise of stddev "
            f"{self.noise_stddev:.3e} added across {len(float_names)} array(s); "
            f"{len(passthrough_names)} integer array(s) passed through.",
        )
        return dxo
