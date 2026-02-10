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
import tempfile
from unittest.mock import MagicMock, patch

from nvflare.apis.fl_constant import ReturnCode

from flip.components.cleanup import CleanupImages
from flip.constants import FlipTasks


class TestCleanupImages:
    def test_init(self):
        """Test that CleanupImages initializes correctly"""
        cleanup = CleanupImages()
        assert cleanup is not None

    @patch("flip.components.cleanup.FlipConstants")
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

    @patch("flip.components.cleanup.FlipConstants")
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
