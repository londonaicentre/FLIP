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

"""Dedicated logger for the database seeding phase.

`flip_api.utils.logger` exports `logging.getLogger("uvicorn")`, which is the
right logger for request-path code (uvicorn attaches handlers when it boots).
But `entrypoint.sh` runs `python src/flip_api/db/seed/seed_essential_data.py`
as a standalone process before uvicorn boots — at that point the "uvicorn"
logger has no handlers and Python's `lastResort` silently drops everything
below WARNING. Every seed-phase `logger.info`/`logger.debug` is invisible.

This module owns a self-contained logger with its own `StreamHandler(stderr)`
so seed-phase output reaches CloudWatch regardless of who runs the script.
Propagation is left enabled so pytest's `caplog` (which hooks the root
logger) can still capture records during tests — the "double-print" risk
under uvicorn is theoretical: uvicorn configures the "uvicorn" logger, not
root, so a "flip_api.seed" record propagating to root finds no extra
handlers in normal production.
"""

import logging
import sys

# Logger level is DEBUG so individual records pass through to handlers and
# propagation. The StreamHandler is set to INFO so production stderr stays
# at INFO+; tests that use `caplog.at_level("DEBUG")` can still capture the
# DEBUG records via propagation to root (where caplog hooks).
logger = logging.getLogger("flip_api.seed")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
