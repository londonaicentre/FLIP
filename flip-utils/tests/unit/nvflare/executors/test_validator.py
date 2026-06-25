# Copyright (c) 2026 Guy's and St Thomas'NHS Foundation Trust & King's College London
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

from unittest.mock import MagicMock, patch

from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.executors.validator import RUN_VALIDATOR


class TestRunValidator:
    def test_init_default_values(self):
        """Test initialization with default values"""
        validator = RUN_VALIDATOR()
        assert validator._validate_task_name == AppConstants.TASK_VALIDATION
        assert validator._project_id == ""
        assert validator._query == ""
        assert validator._validator is None

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        validator = RUN_VALIDATOR(
            validate_task_name="custom_validate", project_id="proj_789", query="SELECT * FROM validation_data"
        )
        assert validator._validate_task_name == "custom_validate"
        assert validator._project_id == "proj_789"
        assert validator._query == "SELECT * FROM validation_data"

    def test_execute_imports_validator_dynamically(self):
        """Test that execute dynamically imports the FLIP_VALIDATOR"""
        validator = RUN_VALIDATOR()

        # Create a mock validator class
        mock_validator_instance = MagicMock()
        shareable = MagicMock()
        mock_validator_instance.execute.return_value = shareable

        mock_validator_class = MagicMock(return_value=mock_validator_instance)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()

        with patch.dict("sys.modules", {"validator": MagicMock(FLIP_VALIDATOR=mock_validator_class)}):
            validator.execute("validate", shareable, fl_ctx, abort_signal)

            # Verify validator was initialized
            assert validator._validator is not None
            mock_validator_class.assert_called_once()
            mock_validator_instance.execute.assert_called_once()

    def test_execute_reuses_existing_validator(self):
        """Test that execute reuses already initialized validator"""
        validator = RUN_VALIDATOR()

        # Pre-initialize the validator
        mock_validator_instance = MagicMock()
        shareable = MagicMock()
        mock_validator_instance.execute.return_value = shareable
        validator._validator = mock_validator_instance

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()

        validator.execute("validate", shareable, fl_ctx, abort_signal)

        # Verify it used the existing validator
        mock_validator_instance.execute.assert_called_once_with("validate", shareable, fl_ctx, abort_signal)

    def test_execute_exception_handling(self):
        """Test that execute handles exceptions properly"""
        validator = RUN_VALIDATOR()
        validator.log_info = MagicMock()
        validator.log_error = MagicMock()

        # Create a mock validator that raises an exception
        mock_validator_instance = MagicMock()
        mock_validator_instance.execute.side_effect = Exception("Test exception")

        mock_validator_class = MagicMock(return_value=mock_validator_instance)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()
        shareable = MagicMock()

        with patch.dict("sys.modules", {"validator": MagicMock(FLIP_VALIDATOR=mock_validator_class)}):
            validator.execute("validate", shareable, fl_ctx, abort_signal)

            # Verify exception was logged
            validator.log_info.assert_called()
            validator.log_error.assert_called()
