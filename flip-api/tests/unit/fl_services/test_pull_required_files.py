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

import json

from flip_api.fl_services.services import pull_required_files
from flip_api.utils.constants import JOB_TYPES_REQUIRED_FILES_FILE


def test_pull_required_files_json_to_assets_success(tmp_path, monkeypatch):
    # Mock S3Client.get_object to return valid JSON
    class DummyBody:
        def read(self):
            return b'{"standard": ["trainer.py", "validator.py", "models.py", "config.json"]}'

    class DummyS3:
        def get_object(self, bucket_path):
            return {"Body": DummyBody()}

    monkeypatch.setattr(pull_required_files, "S3Client", lambda: DummyS3())
    monkeypatch.setattr(
        pull_required_files,
        "get_settings",
        lambda: type("X", (), {"FL_APP_BASE_BUCKET": "bucket", "FL_BACKEND": "nvflare"})(),
    )
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    monkeypatch.setattr(
        pull_required_files, "REQUIRED_JOB_TYPES_FILE", assets_dir / JOB_TYPES_REQUIRED_FILES_FILE
    )
    pull_required_files.pull_required_files_json_to_assets()
    with open(assets_dir / JOB_TYPES_REQUIRED_FILES_FILE) as f:
        data = json.load(f)
    assert data["standard"] == ["trainer.py", "validator.py", "models.py", "config.json"]


def test_pull_required_files_json_to_assets_fallback(tmp_path, monkeypatch):
    # Mock S3Client.get_object to raise Exception
    class DummyS3:
        def get_object(self, bucket_path):
            raise Exception("S3 unavailable")

    monkeypatch.setattr(pull_required_files, "S3Client", lambda: DummyS3())
    monkeypatch.setattr(
        pull_required_files,
        "get_settings",
        lambda: type("X", (), {"FL_APP_BASE_BUCKET": "bucket", "FL_BACKEND": "nvflare"})(),
    )
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    monkeypatch.setattr(
        pull_required_files, "REQUIRED_JOB_TYPES_FILE", assets_dir / JOB_TYPES_REQUIRED_FILES_FILE
    )
    pull_required_files.pull_required_files_json_to_assets()
    with open(assets_dir / JOB_TYPES_REQUIRED_FILES_FILE) as f:
        data = json.load(f)
    assert data["standard"] == ["trainer.py", "validator.py", "models.py", "config.json"]
