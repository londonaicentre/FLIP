# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
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

"""Dedicated logger for database IAM-auth token minting.

`flip_api.utils.logger` exports `logging.getLogger("uvicorn")`, which only has
handlers once uvicorn boots. But the first DB connection — and therefore the
first IAM-token mint via the `do_connect` hook in `database.py` — happens inside
the standalone `seed_essential_data.py` process that `entrypoint.sh` runs before
uvicorn. At that point the "uvicorn" logger has no handlers and Python's
`lastResort` drops anything below WARNING, so a token-mint failure during seeding
would surface only as an opaque connection error with no breadcrumb pointing at
IAM auth.

This module owns a self-contained logger with its own `StreamHandler(stderr)` so
a mint failure is visible in CloudWatch on both the seed-phase and request paths.
Propagation is left enabled so pytest's `caplog` (which hooks the root logger)
can still capture records during tests; the double-handler risk under uvicorn is
theoretical because uvicorn configures the "uvicorn" logger, not root.
"""

import logging
import sys

logger = logging.getLogger("flip_api.db_auth")
logger.setLevel(logging.WARNING)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setLevel(logging.WARNING)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
