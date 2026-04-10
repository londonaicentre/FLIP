# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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


# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from nvflare.apis.event_type import EventType
from nvflare.apis.fl_component import FLComponent
from nvflare.apis.fl_context import FLContext

from cleanup import CleanupImages


class ClientEventHandler(FLComponent):
    """ ClientEventHandler is a generic component that handles system events triggered by nvflare
        or custom flip events. It executes logic inside its own event handler but may also call
        other component's event handlers directly to help overcome the non-deterministic order
        in which nvflare handles events.

        Args:
            cleanup_id (string, required)
        Raises:
    """

    def __init__(self, cleanup_id: str = "cleanup_images"):
        super(ClientEventHandler, self).__init__()

        self.cleanup_id = cleanup_id
        self.cleanup = None

    def handle_event(self, event_type: str, fl_ctx: FLContext):
        self.__set_dependencies(fl_ctx)

        if event_type == EventType.ABORT_TASK:
            self.log_info(fl_ctx, "Abort task event received")
            self.cleanup.execute(fl_ctx)

        if event_type == EventType.END_RUN:
            self.log_info(fl_ctx, "End run event received")
            self.cleanup.execute(fl_ctx)

    def __set_dependencies(self, fl_ctx: FLContext):
        if self.cleanup is None:
            engine = fl_ctx.get_engine()
            self.cleanup = engine.get_component(self.cleanup_id)

            if self.cleanup is None \
                    or not isinstance(self.cleanup, CleanupImages):
                self.system_panic(
                    f"'cleanup_id' component must be CleanupImages. "
                    f"But got: {type(self.cleanup)}",
                    fl_ctx,
                )
                return
