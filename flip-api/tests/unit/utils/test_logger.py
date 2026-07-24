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

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from flip_api import config
from flip_api.utils import logger as logger_module


@pytest.mark.parametrize(
    ("level_name", "expected_level"),
    [
        ("DEBUG", logging.DEBUG),
        ("INFO", logging.INFO),
        ("WARNING", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("CRITICAL", logging.CRITICAL),
    ],
)
def test_logger_module_honours_settings_log_level(level_name, expected_level):
    """Reloading the logger module with a patched LOG_LEVEL setting applies it to the uvicorn logger."""
    fake_settings = SimpleNamespace(LOG_LEVEL=level_name)
    with patch.object(config, "get_settings", return_value=fake_settings):
        reloaded = importlib.reload(logger_module)
        try:
            assert reloaded.logger.level == expected_level
        finally:
            importlib.reload(logger_module)


def test_logger_default_level_is_info():
    """A logger module reloaded against the canonical Settings default emits at INFO, not DEBUG."""
    fake_settings = SimpleNamespace(LOG_LEVEL=config.Settings.model_fields["LOG_LEVEL"].default)
    with patch.object(config, "get_settings", return_value=fake_settings):
        reloaded = importlib.reload(logger_module)
        try:
            assert reloaded.logger.level == logging.INFO
        finally:
            importlib.reload(logger_module)


def test_logger_uses_uvicorn_logger():
    """The exported logger is the uvicorn logger so FastAPI's own messages share configuration."""
    assert logger_module.logger is logging.getLogger("uvicorn")


def test_logger_picks_up_log_level_from_real_basesettings():
    """End-to-end: a real ``BaseSettings`` (not ``SimpleNamespace``) drives ``logger.setLevel``.

    Locks down the chain ``Settings.LOG_LEVEL`` → ``get_settings()`` → ``logger.setLevel`` so
    a refactor that renames or retypes the field (e.g. to an Enum) is caught here, not in prod.
    LOG_LEVEL is passed as a kwarg so it takes priority over any env var (CI sets
    ``LOG_LEVEL=DEBUG`` which would otherwise shadow the field default).
    """
    from typing import Literal

    from pydantic_settings import BaseSettings

    class _RealishSettings(BaseSettings):
        LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    real_settings = _RealishSettings(LOG_LEVEL="WARNING")
    with patch.object(config, "get_settings", return_value=real_settings):
        reloaded = importlib.reload(logger_module)
        try:
            assert reloaded.logger.level == logging.WARNING
        finally:
            importlib.reload(logger_module)


class TestLogLevelCoercion:
    """The Settings.coerce_log_level validator runs on raw input before type checking.

    Tested directly so the assertions don't depend on the rest of the Settings
    model (which requires a populated env file to instantiate end-to-end).
    """

    @pytest.mark.parametrize("blank", ["", None])
    def test_empty_or_none_falls_back_to_info(self, blank):
        """Empty/None LOG_LEVEL is coerced to the secure INFO default."""
        assert config.Settings.coerce_log_level(blank) == "INFO"

    @pytest.mark.parametrize("supplied", ["DEBUG", "debug", "Debug", "dEbUg"])
    def test_case_insensitive_input(self, supplied):
        """Lower- and mixed-case env values are normalised to upper-case."""
        assert config.Settings.coerce_log_level(supplied) == "DEBUG"

    def test_field_default_is_info(self):
        """The Pydantic field default is INFO so unknown environments don't leak debug output."""
        assert config.Settings.model_fields["LOG_LEVEL"].default == "INFO"

    def test_unknown_levels_are_uppercased_not_silently_replaced(self):
        """The validator uppercases unknown input rather than silently coercing to INFO.

        Locks the upper-case-then-validate contract: garbage values must reach
        Literal validation as their (uppercased) selves, so the downstream rejection
        in ``test_invalid_level_rejected_at_validation_time`` fails for the right reason.
        """
        assert config.Settings.coerce_log_level("verbose") == "VERBOSE"
        assert config.Settings.coerce_log_level("Garbage") == "GARBAGE"

    def test_non_string_input_passes_through_unchanged(self):
        """Non-string input flows through so Literal validation rejects it without AttributeError."""
        assert config.Settings.coerce_log_level(10) == 10
        assert config.Settings.coerce_log_level(True) is True

    def test_invalid_level_rejected_at_validation_time(self):
        """Unknown level names fail Literal validation when applied through Settings."""
        # Build a tiny Settings subclass that only exposes LOG_LEVEL so the
        # other required fields don't muddy the assertion.
        from typing import Literal

        from pydantic import field_validator
        from pydantic_settings import BaseSettings

        class _LogLevelOnly(BaseSettings):
            LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

            @field_validator("LOG_LEVEL", mode="before")
            @classmethod
            def _coerce(cls, v):
                return config.Settings.coerce_log_level(v)

        with pytest.raises(ValidationError):
            _LogLevelOnly(LOG_LEVEL="VERBOSE")
