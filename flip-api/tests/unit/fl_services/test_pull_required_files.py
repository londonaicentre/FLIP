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

import pytest

from flip_api.fl_services.services import pull_required_files
from flip_api.utils.constants import job_types_required_files_name


def test_pull_required_files_json_to_assets_success(tmp_path, monkeypatch):
    # Mock S3Client.get_object to return valid JSON
    class DummyBody:
        def read(self):
            return b'{"standard": ["trainer.py", "validator.py", "models.py", "config.json"]}'

    captured = {}

    class DummyS3:
        def get_object(self, bucket_path):
            captured["bucket_path"] = bucket_path
            return {"Body": DummyBody()}

    monkeypatch.setattr(pull_required_files, "S3Client", lambda: DummyS3())
    monkeypatch.setattr(
        pull_required_files,
        "get_settings",
        lambda: type("X", (), {"FL_APP_BASE_BUCKET": "bucket"})(),
    )
    assets_dir = tmp_path / "assets"
    # The per-backend file is resolved via required_job_types_file; point it at tmp_path.
    monkeypatch.setattr(
        pull_required_files,
        "required_job_types_file",
        lambda fl_backend: assets_dir / job_types_required_files_name(fl_backend),
    )

    pull_required_files.pull_required_files_json_to_assets("nvflare")

    # Pulled from the per-backend S3 prefix
    assert captured["bucket_path"] == "bucket/nvflare/required_files.json"
    # Written to the per-backend local file
    dest = assets_dir / job_types_required_files_name("nvflare")
    with open(dest) as f:
        data = json.load(f)
    assert data["standard"] == ["trainer.py", "validator.py", "models.py", "config.json"]


def test_pull_required_files_json_to_assets_raises_on_s3_failure(tmp_path, monkeypatch):
    # S3 is the single source of truth — a failure raises (no checked-in/in-code fallback).
    class DummyS3:
        def get_object(self, bucket_path):
            raise Exception("S3 unavailable")

    monkeypatch.setattr(pull_required_files, "S3Client", lambda: DummyS3())
    monkeypatch.setattr(
        pull_required_files,
        "get_settings",
        lambda: type("X", (), {"FL_APP_BASE_BUCKET": "bucket"})(),
    )
    assets_dir = tmp_path / "assets"
    monkeypatch.setattr(
        pull_required_files,
        "required_job_types_file",
        lambda fl_backend: assets_dir / job_types_required_files_name(fl_backend),
    )

    with pytest.raises(Exception, match="S3 unavailable"):
        pull_required_files.pull_required_files_json_to_assets("nvflare")

    # Nothing was written
    assert not (assets_dir / job_types_required_files_name("nvflare")).exists()
