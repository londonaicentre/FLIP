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

from nvflare.apis.dxo import DXO
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable
from nvflare.app_common.filters.percentile_privacy import PercentilePrivacy as NVFlarePercentilePrivacy


class PercentilePrivacy(NVFlarePercentilePrivacy):
    """Stock NVFLARE ``PercentilePrivacy`` differential-privacy filter plus an ``off`` toggle.

    Subclasses ``nvflare.app_common.filters.percentile_privacy.PercentilePrivacy`` so the
    percentile/gamma filtering maths (Shokri and Shmatikov, "Privacy-preserving deep learning",
    CCS '15) tracks upstream instead of drifting from a vendored copy. The sole FLIP addition is
    ``off``: it lets the filter stay wired into ``config_fed_client.json``'s result-filter chain
    while being switched on/off from config (e.g. running DP-on vs DP-off experiments) without
    adding or removing the component.
    """

    def __init__(self, percentile=10, gamma=0.01, data_kinds: list[str] | None = None, off: bool = False):
        """Initialise the filter.

        Args:
            percentile (int): Only abs weight-diff greater than this percentile is shared — i.e.
                this fraction (%) of the update's smallest-magnitude components is ZEROED and only
                the top ``100 - percentile`` % survive. Allowed range 0..100. Defaults to 10
                (stock NVFLARE: share the top 90%).
            gamma (float): Absolute truncation bound applied to the surviving per-step weight-diff
                values (clipped to ``±gamma``). Defaults to 0.01 (stock NVFLARE).
            data_kinds (list[str] | None): Kinds of DXO to filter. Defaults (via stock) to
                ``[WEIGHT_DIFF, WEIGHTS]`` when None.
            off (bool): If True the filter is a no-op — ``process_dxo`` returns the DXO unchanged.
                Defaults to False.
        """
        super().__init__(percentile=percentile, gamma=gamma, data_kinds=data_kinds)
        self.off = off

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> None | DXO:
        """Return the DXO unchanged when ``off``; otherwise delegate to stock filtering.

        Args:
            dxo (DXO): Information from the client.
            shareable (Shareable): The shareable the DXO belongs to.
            fl_ctx (FLContext): Context provided by the workflow.

        Returns:
            None | DXO: The unchanged DXO when off, else the stock-filtered DXO (or None if the
            stock guard clauses reject it).
        """
        self.log_info(fl_ctx, f"Percentile filter is {'off' if self.off else 'on'}")
        if self.off:
            return dxo
        return super().process_dxo(dxo, shareable, fl_ctx)
