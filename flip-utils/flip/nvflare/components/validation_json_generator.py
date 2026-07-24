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

from nvflare.apis.fl_context import FLContext
from nvflare.app_common.widgets.validation_json_generator import (
    ValidationJsonGenerator as NVFlareValidationJsonGenerator,
)


class ValidationJsonGenerator(NVFlareValidationJsonGenerator):
    """Stock NVFLARE ``ValidationJsonGenerator``, dispatched manually by FLIP's ServerEventHandler.

    Subclasses ``nvflare.app_common.widgets.validation_json_generator.ValidationJsonGenerator`` so
    the results accumulation (METRICS *and* COLLECTION / T2 leaf handling), numpy-float JSON
    encoding and path-safe output track upstream instead of a drifting vendored copy (the fork had
    only the METRICS branch and a plain ``json.dump``). The default ``results_dir`` /
    ``json_file_name`` are identical to stock, so the constructor is inherited unchanged.

    The only FLIP-specific behaviour is dispatch. NVFLARE would auto-handle events through
    ``handle_event``; FLIP instead routes them through ``ServerEventHandler``, which calls
    ``handle_evaluation_events``. Because this component is still registered (so its
    ``handle_event`` is auto-fired for every event), ``handle_event`` is suppressed here — otherwise
    each validation result would be recorded twice — and the real work is delegated to stock's
    implementation from ``handle_evaluation_events``.
    """

    def handle_event(self, event_type: str, fl_ctx: FLContext) -> None:
        # No-op: FLIP drives these events manually via handle_evaluation_events (ServerEventHandler),
        # so the auto-dispatched path must not also process them.
        pass

    def handle_evaluation_events(self, event_type: str, fl_ctx: FLContext) -> None:
        """FLIP integration point — ServerEventHandler dispatches events here, not via handle_event."""
        super().handle_event(event_type, fl_ctx)
