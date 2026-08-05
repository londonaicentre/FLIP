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

import numpy as np
from nvflare.apis.dxo import DXO, MetaKey
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable

from flip.constants import FlipMetaKey
from flip.nvflare.components.custom_percentile_privacy import PercentilePrivacy


class StagePercentilePrivacy(PercentilePrivacy):
    """Percentile-privacy filter that groups parameters by training stage before filtering.

    Subclasses FLIP's :class:`PercentilePrivacy` to inherit its constructor (``percentile`` /
    ``gamma`` / ``data_kinds`` / ``off``) and — transitively — stock NVFLARE's differential-privacy
    maths. It overrides ``process_dxo`` to compute the percentile cutoff *per stage* (from
    ``FlipMetaKey.STAGE``) rather than across all parameters at once, so each stage of a staged
    model (e.g. autoencoder then diffusion model) is filtered independently.
    """

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> None | DXO:
        """Compute the percentile on the abs delta_W, per stage.

        Only shares the params whose absolute delta_W is greater than the percentile value,
        grouping parameters by the stages listed in ``FlipMetaKey.STAGE``.

        Args:
            dxo (DXO): Information from the client.
            shareable (Shareable): The shareable the DXO belongs to.
            fl_ctx (FLContext): Context provided by the workflow.

        Returns:
            None | DXO: The filtered DXO, the unchanged DXO (off / no stage info), or None (invalid
            gamma/percentile).
        """
        self.log_info(fl_ctx, f"Percentile filter is {'off' if self.off else 'on'}")
        if self.off:
            return dxo
        self.logger.debug("check gamma")
        if self.gamma <= 0:
            self.log_debug(fl_ctx, "no partial model: gamma: {}".format(self.gamma))
            return None
        if self.percentile < 0 or self.percentile > 100:
            self.log_debug(fl_ctx, "no partial model: percentile: {}".format(self.percentile))
            return None  # do nothing

        # invariant to local steps
        model_diff = dxo.data
        total_steps = dxo.get_meta_prop(MetaKey.NUM_STEPS_CURRENT_ROUND, 1)
        stages = dxo.get_meta_prop(FlipMetaKey.STAGE, [])
        if len(stages) == 0:
            self.log_info(fl_ctx, "Stage info is missing in DXO meta; not applying privacy filter.")
            return dxo

        # One output dict for the whole update: each stage filters only its own parameters in it,
        # so every stage's filtering survives, and parameters outside all stages pass through
        # untouched (they never get the per-step scaling applied).
        filtered = dict(model_diff)
        for stage in stages:
            # We handle the privacy filtering for each stage separately
            named_parameters_to_filter = [name for name in model_diff if name.split(".")[0] in stage]
            if not named_parameters_to_filter:
                self.log_info(fl_ctx, f"Stage {stage!r} matches no parameters; skipping.")
                continue
            delta_w = {name: model_diff[name] / total_steps for name in named_parameters_to_filter}
            # abs delta
            all_abs_values = np.concatenate([np.abs(delta_w[name].ravel()) for name in named_parameters_to_filter])
            cutoff = np.percentile(a=all_abs_values, q=self.percentile, overwrite_input=False)
            self.log_info(
                fl_ctx,
                f"Stage: {stage}"
                f"Max abs delta_w: {np.max(all_abs_values)}, Min abs delta_w: {np.min(all_abs_values)},"
                f"cutoff: {cutoff}, scale: {total_steps}.",
            )

            for name, diff_w in delta_w.items():
                if np.ndim(diff_w) == 0:  # single scalar, no clipping
                    filtered[name] = diff_w * total_steps
                    continue
                selector = (diff_w > -cutoff) & (diff_w < cutoff)
                diff_w[selector] = 0.0
                diff_w = np.clip(diff_w, a_min=-self.gamma, a_max=self.gamma)
                filtered[name] = diff_w * total_steps

        dxo.data = filtered
        return dxo
