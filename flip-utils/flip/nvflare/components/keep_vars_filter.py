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

import re

from nvflare.apis.dxo import DataKind
from nvflare.apis.dxo_filter import DXO, DXOFilter
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable


class KeepOnlyVars(DXOFilter):
    """Keep ONLY the weights whose key matches ``include_vars`` (regex); drop the rest.

    The include-only inverse of NVFLARE's ``ExcludeVars`` (which is exclude-only). Used as a
    client-side ``task_result_filter`` to shrink a per-round update to just the trainable
    parameters: a frozen-backbone fine-tune sends only its head (~KB) instead of the full model
    (~759 MiB), which fixes the large-payload client→server ``SubmitUpdate`` timeout (FLIP#684).

    Keeping only the head is correct for a frozen backbone: the backbone is identical on every
    client after round 0, so it need not be re-aggregated; the server retains it from the round-0
    global model (delivered by ``InitialCheckpointPTModelPersistor``) and the aggregator merges the
    head diff into it. The server's WEIGHT_DIFF reconstruction is partial-safe (only keys present in
    the diff are applied), so a head-only update reconstructs correctly.
    """

    def __init__(self, include_vars: str | None = None, data_kinds: list[str] | None = None):
        """
        Args:
            include_vars: regex; only weight keys whose name matches (``re.search``) are kept.
                If empty/None the filter is a no-op (nothing dropped).
            data_kinds: DXO kinds to filter; defaults to WEIGHTS and WEIGHT_DIFF.
        """
        if not data_kinds:
            data_kinds = [DataKind.WEIGHT_DIFF, DataKind.WEIGHTS]
        super().__init__(
            supported_data_kinds=[DataKind.WEIGHTS, DataKind.WEIGHT_DIFF],
            data_kinds_to_filter=data_kinds,
        )
        self.skip = not (isinstance(include_vars, str) and include_vars)
        self.pattern = re.compile(include_vars) if not self.skip else None

    def process_dxo(self, dxo: DXO, shareable: Shareable, fl_ctx: FLContext) -> DXO | None:
        if self.skip:
            return None

        weights = dxo.data
        var_names = list(weights.keys())  # copy: we mutate `weights` below
        kept = 0
        for var_name in var_names:
            if self.pattern.search(var_name):
                kept += 1
            else:
                weights.pop(var_name, None)

        if kept == 0:
            # Dropping everything is almost certainly a misconfigured regex (wrong key names) —
            # warn loudly rather than silently submit an empty update.
            self.log_warning(
                fl_ctx,
                f"KeepOnlyVars: regex {self.pattern.pattern!r} matched no keys; "
                f"dropped ALL {len(var_names)} variable(s).",
            )
        else:
            self.log_info(
                fl_ctx,
                f"KeepOnlyVars: kept {kept} of {len(var_names)} variable(s) matching {self.pattern.pattern!r}.",
            )

        dxo.data = weights
        return dxo
