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

import os
import tempfile
from unittest.mock import MagicMock, patch

from nvflare.apis.fl_constant import ReturnCode

from flip.constants import FlipTasks
from flip.nvflare.components.cleanup import CleanupImages


class TestCleanupImages:
    def test_init(self):
        """Test that CleanupImages initializes correctly"""
        cleanup = CleanupImages()
        assert cleanup is not None

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_execute_post_validation_dev_mode(self, mock_constants):
        """Test POST_VALIDATION task in dev mode"""
        mock_constants.LOCAL_DEV = True

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir, exist_ok=True)

            cleanup = CleanupImages()
            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            fl_ctx.get_job_id.return_value = job_id
            fl_ctx.get_identity_name.return_value = "test_client"
            shareable = MagicMock()
            abort_signal = MagicMock()

            with patch("os.getcwd", return_value=tmpdir):
                cleanup.execute(FlipTasks.POST_VALIDATION, shareable, fl_ctx, abort_signal)

            # In dev mode, directory should NOT be deleted
            assert os.path.exists(job_dir)

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_execute_init_training_dev_mode(self, mock_constants):
        """Test INIT_TRAINING task in dev mode"""
        mock_constants.LOCAL_DEV = True

        cleanup = CleanupImages()
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        shareable = MagicMock()
        abort_signal = MagicMock()

        result = cleanup.execute(FlipTasks.INIT_TRAINING, shareable, fl_ctx, abort_signal)

        assert result.get_return_code() == ReturnCode.OK

    def test_execute_unknown_task(self):
        """Test execution with unknown task"""
        cleanup = CleanupImages()
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        shareable = MagicMock()
        abort_signal = MagicMock()

        result = cleanup.execute("UNKNOWN_TASK", shareable, fl_ctx, abort_signal)

        assert result.get_return_code() == ReturnCode.TASK_UNKNOWN

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    @patch("shutil.rmtree")
    def test_execute_post_validation_production_mode(self, mock_rmtree, mock_constants):
        """Test POST_VALIDATION task in production mode deletes job directory"""
        mock_constants.LOCAL_DEV = False

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir, exist_ok=True)

            cleanup = CleanupImages()
            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            fl_ctx.get_job_id.return_value = job_id
            fl_ctx.get_identity_name.return_value = "test_client"
            shareable = MagicMock()
            abort_signal = MagicMock()

            with patch("os.getcwd", return_value=tmpdir):
                with patch("os.path.isdir", return_value=True):
                    cleanup.execute(FlipTasks.POST_VALIDATION, shareable, fl_ctx, abort_signal)

            # In production mode, rmtree should be called to delete job_dir
            mock_rmtree.assert_called()

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_execute_init_training_production_mode_no_directory(self, mock_constants):
        """Test INIT_TRAINING in production mode when net_directory doesn't exist"""
        mock_constants.LOCAL_DEV = False
        mock_constants.IMAGES_DIR = "/images"
        mock_constants.NET_ID = "net-1"

        cleanup = CleanupImages()
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "test_job"
        fl_ctx.get_identity_name.return_value = "test_client"
        shareable = MagicMock()
        abort_signal = MagicMock()

        with patch("os.path.exists", return_value=False):
            result = cleanup.execute(FlipTasks.INIT_TRAINING, shareable, fl_ctx, abort_signal)

        assert result.get_return_code() == ReturnCode.OK

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    @patch("shutil.rmtree")
    @patch("os.unlink")
    def test_execute_init_training_production_mode_with_files(self, mock_unlink, mock_rmtree, mock_constants):
        """Test INIT_TRAINING in production mode with files and directories to clean"""
        mock_constants.LOCAL_DEV = False
        mock_constants.IMAGES_DIR = "/images"
        mock_constants.NET_ID = "net-1"

        with tempfile.TemporaryDirectory() as tmpdir:
            net_dir = os.path.join(tmpdir, "net-1")
            os.makedirs(net_dir)

            # Create test files and directories
            test_file = os.path.join(net_dir, "test_file.txt")
            test_dir = os.path.join(net_dir, "test_dir")
            with open(test_file, "w") as f:
                f.write("test")
            os.makedirs(test_dir)

            cleanup = CleanupImages()
            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            fl_ctx.get_job_id.return_value = "test_job"
            fl_ctx.get_identity_name.return_value = "test_client"
            shareable = MagicMock()
            abort_signal = MagicMock()

            with patch("os.path.exists", return_value=True):
                with patch("os.listdir", return_value=["test_file.txt", "test_dir"]):
                    with patch("os.path.isfile") as mock_isfile:
                        with patch("os.path.isdir") as mock_isdir:
                            # First call for file, second for directory
                            mock_isfile.side_effect = [True, False]
                            mock_isdir.side_effect = [False, True]

                            with patch("flip.nvflare.components.cleanup.Path") as mock_path:
                                mock_path_instance = MagicMock()
                                mock_path_instance.glob.return_value = []
                                mock_path.return_value = mock_path_instance

                                result = cleanup.execute(FlipTasks.INIT_TRAINING, shareable, fl_ctx, abort_signal)

            assert result.get_return_code() == ReturnCode.OK
            # Verify cleanup functions were called
            assert mock_unlink.called or mock_rmtree.called

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    @patch("shutil.rmtree")
    @patch("os.unlink")
    def test_execute_post_validation_production_mode_with_files(self, mock_unlink, mock_rmtree, mock_constants):
        """Test POST_VALIDATION in production mode with files and directories to clean"""
        mock_constants.LOCAL_DEV = False
        mock_constants.IMAGES_DIR = "/images"
        mock_constants.NET_ID = "net-1"

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir)

            cleanup = CleanupImages()
            fl_ctx = MagicMock()
            fl_ctx.get_peer_context.return_value = None
            fl_ctx.get_job_id.return_value = job_id
            fl_ctx.get_identity_name.return_value = "test_client"
            shareable = MagicMock()
            abort_signal = MagicMock()

            with patch("os.getcwd", return_value=tmpdir):
                with patch("os.path.isdir", return_value=True):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.listdir", return_value=["file1.txt"]):
                            with patch("os.path.isfile", return_value=True):
                                with patch("flip.nvflare.components.cleanup.Path") as mock_path:
                                    mock_path_instance = MagicMock()
                                    mock_path_instance.glob.return_value = []
                                    mock_path.return_value = mock_path_instance

                                    result = cleanup.execute(FlipTasks.POST_VALIDATION, shareable, fl_ctx, abort_signal)

            assert result.get_return_code() == ReturnCode.OK

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_execute_exception_handling(self, mock_constants):
        """Test exception handling in execute method"""
        mock_constants.LOCAL_DEV = False

        cleanup = CleanupImages()
        cleanup.log_info = MagicMock()
        cleanup.log_error = MagicMock()
        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        shareable = MagicMock()
        abort_signal = MagicMock()

        # Force an exception by making get_job_id raise
        with patch("os.getcwd", side_effect=Exception("Test exception")):
            result = cleanup.execute(FlipTasks.POST_VALIDATION, shareable, fl_ctx, abort_signal)

        assert result.get_return_code() == ReturnCode.EXECUTION_EXCEPTION
        assert result.get_header("exception") is not None
