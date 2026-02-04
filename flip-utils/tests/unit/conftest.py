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

import os
import sys
from pathlib import Path


def pytest_sessionstart(session):
    """Make all modules under src importable as top-level for tests."""
    project_root = Path(__file__).resolve().parents[2]  # adjust if tests/ is not at repo root

    # Add custom (so 'flip' and 'utils' work as top-level modules)
    custom_path = project_root / "src" / "standard" / "app" / "custom"
    sys.path.insert(0, str(custom_path))
    print(f"[pytest setup] Added to sys.path:\n  {custom_path}")

    # Set environment variables for local development
    os.environ.setdefault("LOCAL_DEV", "true")
    os.environ.setdefault("DEV_DATAFRAME", "/data/local_dev/dev_dataframe.csv")
    os.environ.setdefault("DEV_IMAGES_DIR", "/data/local_dev/dev_images")
