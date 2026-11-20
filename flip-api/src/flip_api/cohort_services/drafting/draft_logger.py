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

"""Dedicated logger for cohort SQL drafting: metadata only, never the description or the model's output.

Own handler, as in ``db/seed/seed_logger.py``: uvicorn configures only its own loggers, so without one
INFO records here would be dropped. Propagation stays on for pytest's ``caplog``.
"""

import logging
import sys

logger = logging.getLogger("flip_api.cohort_draft")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setLevel(logging.INFO)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)
