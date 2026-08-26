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
from flip.nvflare.components.cleanup import CleanupJobDir


def _fl_ctx(job_id: str) -> MagicMock:
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    fl_ctx.get_job_id.return_value = job_id
    fl_ctx.get_identity_name.return_value = "test_client"
    return fl_ctx


class TestCleanupJobDir:
    def test_init(self):
        assert CleanupJobDir() is not None

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_post_validation_deletes_job_dir(self, mock_constants):
        mock_constants.LOCAL_DEV = False

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir)

            with patch("os.getcwd", return_value=tmpdir):
                result = CleanupJobDir().execute(FlipTasks.POST_VALIDATION, MagicMock(), _fl_ctx(job_id), MagicMock())

            assert result.get_return_code() == ReturnCode.OK
            assert not os.path.exists(job_dir)

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_post_validation_dev_mode_keeps_job_dir(self, mock_constants):
        mock_constants.LOCAL_DEV = True

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            job_dir = os.path.join(tmpdir, job_id)
            os.makedirs(job_dir)

            with patch("os.getcwd", return_value=tmpdir):
                result = CleanupJobDir().execute(FlipTasks.POST_VALIDATION, MagicMock(), _fl_ctx(job_id), MagicMock())

            assert result.get_return_code() == ReturnCode.OK
            assert os.path.exists(job_dir)

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_post_validation_with_no_job_dir_is_ok(self, mock_constants):
        mock_constants.LOCAL_DEV = False

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.getcwd", return_value=tmpdir):
                result = CleanupJobDir().execute(FlipTasks.POST_VALIDATION, MagicMock(), _fl_ctx("absent"), MagicMock())

            assert result.get_return_code() == ReturnCode.OK

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_never_touches_imaging_data(self, mock_constants):
        """Imaging retention is owned trust-side by imaging-api's TTL sweeper (FLIP#1050):
        the executor must not delete anything outside the job workspace."""
        mock_constants.LOCAL_DEV = False

        with tempfile.TemporaryDirectory() as tmpdir:
            images_dir = os.path.join(tmpdir, "images", "net-1", "proj", "ACC1")
            os.makedirs(images_dir)
            mock_constants.IMAGES_DIR = os.path.join(tmpdir, "images")
            mock_constants.NET_ID = "net-1"

            with patch("os.getcwd", return_value=tmpdir):
                CleanupJobDir().execute(FlipTasks.POST_VALIDATION, MagicMock(), _fl_ctx("job1"), MagicMock())

            assert os.path.isdir(images_dir)

    def test_unknown_task_returns_task_unknown(self):
        result = CleanupJobDir().execute("some_other_task", MagicMock(), _fl_ctx("job1"), MagicMock())

        assert result.get_return_code() == ReturnCode.TASK_UNKNOWN

    @patch("flip.nvflare.components.cleanup.FlipConstants")
    def test_exception_is_reported_not_raised(self, mock_constants):
        mock_constants.LOCAL_DEV = False

        with tempfile.TemporaryDirectory() as tmpdir:
            job_id = "test_job_123"
            os.makedirs(os.path.join(tmpdir, job_id))

            cleanup = CleanupJobDir()
            # log_error fires an NVFLARE log event that type-checks the FLContext,
            # which a MagicMock fails — stub the log methods like the controller tests do.
            cleanup.log_info = MagicMock()
            cleanup.log_error = MagicMock()

            with patch("os.getcwd", return_value=tmpdir):
                with patch("shutil.rmtree", side_effect=OSError("disk error")):
                    result = cleanup.execute(FlipTasks.POST_VALIDATION, MagicMock(), _fl_ctx(job_id), MagicMock())

            assert result.get_return_code() == ReturnCode.EXECUTION_EXCEPTION
            assert result.get_header("exception") is not None
