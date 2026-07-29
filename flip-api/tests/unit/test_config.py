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

"""Settings parsing for the model-file scan pipeline (#52).

The suffix-list fields carry ``NoDecode`` so the env value reaches the
validator raw — these tests pin every accepted input shape (JSON list,
comma-separated, empty -> default) plus the lowercase/leading-dot
normalisation the suffix matching relies on.
"""

from flip_api.config import Settings


def test_allowed_extensions_default():
    settings = Settings()
    assert ".py" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    assert ".pt" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    assert ".safetensors" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    # Opaque archives are deliberately not allowed by default.
    assert ".zip" not in settings.ALLOWED_MODEL_FILE_EXTENSIONS


def test_suffix_list_accepts_json_list():
    settings = Settings(ALLOWED_MODEL_FILE_EXTENSIONS='[".py", ".onnx"]')
    assert settings.ALLOWED_MODEL_FILE_EXTENSIONS == [".py", ".onnx"]


def test_suffix_list_accepts_comma_separated_string():
    settings = Settings(ALLOWED_MODEL_FILE_EXTENSIONS=".py, .onnx,.pt")
    assert settings.ALLOWED_MODEL_FILE_EXTENSIONS == [".py", ".onnx", ".pt"]


def test_suffix_list_empty_string_falls_back_to_default():
    """GH Actions injects empty strings for unset env vars — that must mean
    'use the default', never 'allow nothing'."""
    settings = Settings(ALLOWED_MODEL_FILE_EXTENSIONS="")
    assert settings.ALLOWED_MODEL_FILE_EXTENSIONS == Settings().ALLOWED_MODEL_FILE_EXTENSIONS


def test_suffix_list_normalises_case_and_leading_dot():
    settings = Settings(ALLOWED_MODEL_FILE_EXTENSIONS="PY, .Pt,safetensors")
    assert settings.ALLOWED_MODEL_FILE_EXTENSIONS == [".py", ".pt", ".safetensors"]


def test_picklescan_suffixes_default_and_parsing():
    assert Settings().PICKLESCAN_FILE_SUFFIXES == [".pt", ".pth", ".pkl", ".pickle"]
    assert Settings(PICKLESCAN_FILE_SUFFIXES="pt,PKL").PICKLESCAN_FILE_SUFFIXES == [".pt", ".pkl"]
    assert Settings(PICKLESCAN_FILE_SUFFIXES="").PICKLESCAN_FILE_SUFFIXES == Settings().PICKLESCAN_FILE_SUFFIXES


def test_scan_int_settings_empty_string_falls_back_to_default():
    """Empty-string ints must fall back, not blow up at import time.

    The Makefile exports env-file keys with ``sed 's/=.*//'``, which strips the
    value from *every* line — including commented-out ones — so a setting that
    merely appears in `.env.development.example` as a comment arrives as an
    empty string. Pydantic treats that as a real override, which took down the
    whole app at import (CI, 2026-07-29).
    """
    assert Settings(PICKLESCAN_TIMEOUT_SECONDS="").PICKLESCAN_TIMEOUT_SECONDS == 120
    assert Settings(SCHEDULER_MALWARE_SCAN_RECONCILE_RATE="").SCHEDULER_MALWARE_SCAN_RECONCILE_RATE == 1
    assert Settings(PICKLESCAN_TIMEOUT_SECONDS="45").PICKLESCAN_TIMEOUT_SECONDS == 45
