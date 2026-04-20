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

"""Minimal strategy that wires handle_client_exception into FedAvg.

This mirrors the wiring in `src/standard/app/strategy.py` but without the
per-client metrics bookkeeping — the integration test only cares about the
ERROR-status transition on a crashed reply.
"""

from typing import Iterable

from flip import FLIP
from flip.flower.metrics import handle_client_exception
from flwr.app import ArrayRecord, Message
from flwr.serverapp.strategy import FedAvg


class CrashTestStrategy(FedAvg):
    def __init__(self, flip: FLIP, model_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.flip = flip
        self.model_id = model_id

    def aggregate_train(
        self,
        server_round: int,
        replies: Iterable[Message],
    ) -> ArrayRecord | None:
        replies = list(replies)
        for msg in replies:
            handle_client_exception(msg, self.model_id, self.flip)
        return super().aggregate_train(server_round, replies)
