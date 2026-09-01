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

"""Settings parsing for the model-file scan pipeline (#52) and the email backend (#919).

The suffix-list fields carry ``NoDecode`` so the env value reaches the
validator raw — these tests pin every accepted input shape (JSON list,
comma-separated, empty -> default) plus the lowercase/leading-dot
normalisation the suffix matching relies on.
"""

import pytest
from pydantic import ValidationError

from flip_api.config import DevSettings, ProdSettings, Settings


def test_allowed_extensions_default():
    settings = Settings()
    assert ".py" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    assert ".pt" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    assert ".safetensors" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
    # Flower ships a config.toml in every app; without this the backend's
    # uploads would all be refused.
    assert ".toml" in settings.ALLOWED_MODEL_FILE_EXTENSIONS
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
    assert Settings(BANDIT_TIMEOUT_SECONDS="").BANDIT_TIMEOUT_SECONDS == 60
    assert Settings(BANDIT_TIMEOUT_SECONDS="30").BANDIT_TIMEOUT_SECONDS == 30


def test_suffix_list_passes_through_unexpected_types_for_pydantic_to_reject():
    """A non-string, non-list value must reach Pydantic's own validation.

    The parser only knows how to normalise strings and lists; anything else is
    returned untouched so the field's ``list[str]`` validator rejects it
    loudly, rather than the parser silently coercing nonsense into a config
    that would then decide what may be uploaded. Same shape as the
    non-string branch of ``coerce_log_level``.
    """
    with pytest.raises(ValidationError):
        Settings(ALLOWED_MODEL_FILE_EXTENSIONS=123)


def test_email_backend_defaults_secure_but_dev_uses_console():
    """Unknown environments send real email; development logs instead (#919)."""
    assert Settings().EMAIL_BACKEND == "ses"
    assert DevSettings().EMAIL_BACKEND == "console"


def test_email_backend_empty_string_falls_back_to_the_per_class_default():
    """Same env-file empty-string trap as the scan ints, resolved per class."""
    assert Settings(EMAIL_BACKEND="").EMAIL_BACKEND == "ses"
    assert DevSettings(EMAIL_BACKEND="").EMAIL_BACKEND == "console"


def test_console_email_backend_is_rejected_in_production():
    """The dev substitute must be impossible to enable in production (#919).

    ``ProdSettings`` narrows ``EMAIL_BACKEND`` to ``Literal["ses"]``, so the
    console backend is a boot-time validation error rather than a silently
    accepted setting that would drop every production email.
    """
    with pytest.raises(ValidationError) as exc_info:
        ProdSettings(
            EMAIL_BACKEND="console",
            AWS_SES_ADMIN_EMAIL_ADDRESS="admin@example.com",
            AWS_SES_SENDER_EMAIL_ADDRESS="sender@example.com",
        )
    assert "EMAIL_BACKEND" in str(exc_info.value)


def test_dev_ses_addresses_are_optional_with_defaults():
    """Development boots with no SES configuration at all (#919).

    Asserted on the fields rather than an instance: a developer's own
    ``.env.development`` may still hold real addresses, which legitimately
    override these defaults. What must hold everywhere is that neither field
    is *required* in dev, so a checkout with no SES lines still starts.
    """
    for name, expected in (
        ("AWS_SES_ADMIN_EMAIL_ADDRESS", "flip-admin@example.com"),
        ("AWS_SES_SENDER_EMAIL_ADDRESS", "flip-no-reply@example.com"),
    ):
        assert DevSettings.model_fields[name].is_required() is False
        assert DevSettings.model_fields[name].default == expected
        # ...while production still demands an explicit address.
        assert ProdSettings.model_fields[name].is_required() is True


def test_dev_ses_addresses_tolerate_empty_strings():
    """Empty strings matter as much as absent values.

    A stale ``.env.development`` that still carries (even commented-out)
    ``AWS_SES_*`` lines exports the bare name, and ``EmailStr`` would otherwise
    reject the empty value and take flip-api down at import.
    """
    blanked = DevSettings(AWS_SES_ADMIN_EMAIL_ADDRESS="", AWS_SES_SENDER_EMAIL_ADDRESS="")
    assert blanked.AWS_SES_ADMIN_EMAIL_ADDRESS == "flip-admin@example.com"
    assert blanked.AWS_SES_SENDER_EMAIL_ADDRESS == "flip-no-reply@example.com"


@pytest.mark.parametrize("blank", [",,,", " , ", " ", ",", "[]"])
def test_suffix_list_separator_only_values_fall_back_to_default(blank):
    """A value that normalises to nothing must not yield an empty list.

    An empty ``PICKLESCAN_FILE_SUFFIXES`` fails open in the worst way: no
    suffix matches, so every upload silently skips scanning. An empty
    ``ALLOWED_MODEL_FILE_EXTENSIONS`` fails the other way, rejecting every
    upload. Both fall back to the documented defaults instead.
    """
    settings = Settings(ALLOWED_MODEL_FILE_EXTENSIONS=blank, PICKLESCAN_FILE_SUFFIXES=blank)

    assert settings.ALLOWED_MODEL_FILE_EXTENSIONS == Settings().ALLOWED_MODEL_FILE_EXTENSIONS
    assert settings.PICKLESCAN_FILE_SUFFIXES == Settings().PICKLESCAN_FILE_SUFFIXES
