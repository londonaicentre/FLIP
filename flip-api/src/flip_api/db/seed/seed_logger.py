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
Propagation is disabled so we don't double-print when the seed module is
imported under uvicorn (e.g. in tests).
"""

import logging
import sys

logger = logging.getLogger("flip_api.seed")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False
