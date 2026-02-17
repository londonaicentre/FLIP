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

from unittest.mock import MagicMock, patch

from nvflare.app_common.app_constant import AppConstants

from flip.nvflare.executors.trainer import RUN_TRAINER


class TestRunTrainer:
    def test_init_default_values(self):
        """Test initialization with default values"""
        trainer = RUN_TRAINER()
        assert trainer._train_task_name == AppConstants.TASK_TRAIN
        assert trainer._submit_model_task_name == AppConstants.TASK_SUBMIT_MODEL
        assert trainer._exclude_vars is None
        assert trainer._project_id == ""
        assert trainer._query == ""
        assert trainer._flip_trainer is None
        assert trainer._epochs is None

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        trainer = RUN_TRAINER(
            train_task_name="custom_train",
            submit_model_task_name="custom_submit",
            exclude_vars=["var1", "var2"],
            project_id="proj_123",
            query="SELECT * FROM data",
        )
        assert trainer._train_task_name == "custom_train"
        assert trainer._submit_model_task_name == "custom_submit"
        assert trainer._exclude_vars == ["var1", "var2"]
        assert trainer._project_id == "proj_123"
        assert trainer._query == "SELECT * FROM data"

    def test_execute_imports_trainer_dynamically(self):
        """Test that execute dynamically imports the FLIP_TRAINER"""
        trainer = RUN_TRAINER()

        # Create a mock trainer class
        mock_trainer_instance = MagicMock()
        mock_trainer_instance.get_num_epochs.return_value = 5
        shareable = MagicMock()
        mock_trainer_instance.execute.return_value = shareable

        mock_trainer_class = MagicMock(return_value=mock_trainer_instance)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()

        with patch.dict("sys.modules", {"trainer": MagicMock(FLIP_TRAINER=mock_trainer_class)}):
            trainer.execute("train", shareable, fl_ctx, abort_signal)

            # Verify trainer was initialized
            assert trainer._flip_trainer is not None
            assert trainer._epochs == 5
            mock_trainer_class.assert_called_once()
            mock_trainer_instance.execute.assert_called_once()

    def test_execute_reuses_existing_trainer(self):
        """Test that execute reuses already initialized trainer"""
        trainer = RUN_TRAINER()

        # Pre-initialize the trainer
        mock_trainer_instance = MagicMock()
        mock_trainer_instance.get_num_epochs.return_value = 3
        shareable = MagicMock()
        mock_trainer_instance.execute.return_value = shareable
        trainer._flip_trainer = mock_trainer_instance
        trainer._epochs = 3

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()

        trainer.execute("train", shareable, fl_ctx, abort_signal)

        # Verify it used the existing trainer
        mock_trainer_instance.execute.assert_called_once_with("train", shareable, fl_ctx, abort_signal)

    def test_execute_exception_handling(self):
        """Test that execute handles exceptions properly"""
        trainer = RUN_TRAINER()
        trainer.log_info = MagicMock()
        trainer.log_error = MagicMock()

        # Create a mock trainer that raises an exception
        mock_trainer_instance = MagicMock()
        mock_trainer_instance.get_num_epochs.return_value = 5
        mock_trainer_instance.execute.side_effect = Exception("Test exception")

        mock_trainer_class = MagicMock(return_value=mock_trainer_instance)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        abort_signal = MagicMock()
        shareable = MagicMock()

        with patch.dict("sys.modules", {"trainer": MagicMock(FLIP_TRAINER=mock_trainer_class)}):
            trainer.execute("train", shareable, fl_ctx, abort_signal)

            # Verify exception was logged
            trainer.log_info.assert_called()
            trainer.log_error.assert_called()
