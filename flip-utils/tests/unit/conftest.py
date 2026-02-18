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

    # Add flip package (root of repo) - insert at beginning so it takes precedence
    sys.path.insert(0, str(project_root))
    print(f"[pytest setup] Added to sys.path:\n  {project_root}")

    # Load environment variables from .env.test if it exists
    env_test_file = project_root / ".env.test"
    if env_test_file.exists():
        with open(env_test_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key, value)
        print("[pytest setup] Loaded environment variables from .env.test")

    # Set environment variables for local development (fallback if .env.test not loaded)
    os.environ.setdefault("LOCAL_DEV", "true")
    os.environ.setdefault("MIN_CLIENTS", "1")
    os.environ.setdefault("DEV_DATAFRAME", "/data/local_dev/dev_dataframe.csv")
    os.environ.setdefault("DEV_IMAGES_DIR", "/data/local_dev/dev_images")

    # Set production environment variables for testing prod mode (fallback)
    os.environ.setdefault("CENTRAL_HUB_API_URL", "https://hub.example.com")
    os.environ.setdefault("DATA_ACCESS_API_URL", "https://data.example.com")
    os.environ.setdefault("IMAGING_API_URL", "https://imaging.example.com")
    os.environ.setdefault("IMAGES_DIR", "/images")
    os.environ.setdefault("PRIVATE_API_KEY_HEADER", "x-api-key")
    os.environ.setdefault("PRIVATE_API_KEY", "test-key")
    os.environ.setdefault("NET_ID", "net-1")
    os.environ.setdefault("UPLOADED_FEDERATED_DATA_BUCKET", "s3://test-bucket")
