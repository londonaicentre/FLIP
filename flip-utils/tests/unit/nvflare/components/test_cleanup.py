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

from unittest.mock import MagicMock, patch

import pytest
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import ReturnCode

from flip.constants import FlipTasks
from flip.nvflare.components.cleanup import CleanupImages

PROJECT = "abcdef01-2345-6789-abcd-ef0123456789"
OTHER_PROJECT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def constants(tmp_path):
    with patch("flip.nvflare.components.cleanup.FlipConstants") as mock_constants:
        mock_constants.IMAGES_TRANSPORT = "mount"
        mock_constants.LOCAL_DEV = False
        mock_constants.IMAGES_DIR = str(tmp_path / "images")
        mock_constants.NET_ID = "net-1"
        yield mock_constants


@pytest.fixture
def project_id():
    with patch("flip.nvflare.components.cleanup.get_flip_project_id", return_value=PROJECT) as mock_get:
        yield mock_get


def _study(tmp_path, project, accession="ACC001"):
    study = tmp_path / "images" / "net-1" / project / accession / "a.dcm"
    study.parent.mkdir(parents=True)
    study.write_bytes(b"dicom")
    return study


def _fl_ctx(job_id="test_job"):
    fl_ctx = MagicMock()
    fl_ctx.get_peer_context.return_value = None
    fl_ctx.get_job_id.return_value = job_id
    return fl_ctx


def _cleanup():
    """A CleanupImages with its warnings and errors recorded: NVFLARE's own fire an event, which needs a real fl_ctx."""
    cleanup = CleanupImages()
    cleanup.log_warning = MagicMock()
    cleanup.log_error = MagicMock()
    return cleanup


def _run(task, fl_ctx=None):
    return _cleanup().execute(task, MagicMock(), fl_ctx or _fl_ctx(), MagicMock())


@pytest.mark.parametrize("task", [FlipTasks.INIT_TRAINING, FlipTasks.INIT_TASK])
def test_the_start_of_a_job_keeps_what_its_trainer_already_downloaded(constants, project_id, tmp_path, task):
    """The trainer starts before the first task and may already be downloading (FLIP#1830)."""
    own = _study(tmp_path, PROJECT)
    other = _study(tmp_path, OTHER_PROJECT)
    upload = tmp_path / "images" / "net-1" / "upload"
    upload.mkdir()

    assert _run(task).get_return_code() == ReturnCode.OK

    assert own.read_bytes() == b"dicom"
    assert not other.parent.parent.exists()
    assert not upload.exists()


def test_without_a_project_id_the_start_of_a_job_deletes_every_project(constants, project_id, tmp_path):
    project_id.return_value = None
    study = _study(tmp_path, PROJECT)

    assert _run(FlipTasks.INIT_TRAINING).get_return_code() == ReturnCode.OK

    assert list((tmp_path / "images" / "net-1").iterdir()) == []
    assert not study.exists()


def test_the_end_of_the_run_deletes_every_project(constants, project_id, tmp_path):
    """ABOUT_TO_END_RUN fires when the job completes, fails or is aborted, with no task from the server."""
    _study(tmp_path, PROJECT)
    _study(tmp_path, OTHER_PROJECT)

    _cleanup().handle_event(EventType.ABOUT_TO_END_RUN, _fl_ctx())

    assert list((tmp_path / "images" / "net-1").iterdir()) == []


def test_an_error_at_the_end_of_the_run_is_logged_not_raised(constants, tmp_path):
    _study(tmp_path, PROJECT)
    cleanup = _cleanup()

    with patch("flip.nvflare.components.cleanup.shutil.rmtree", side_effect=OSError("busy")):
        cleanup.handle_event(EventType.ABOUT_TO_END_RUN, _fl_ctx())

    cleanup.log_error.assert_called_once()


@pytest.mark.parametrize("task", [FlipTasks.POST_VALIDATION, FlipTasks.POST_TASK])
def test_the_last_task_deletes_the_job_directory_and_leaves_images_to_the_end_of_the_run(
    constants, tmp_path, monkeypatch, task
):
    job_dir = tmp_path / "workspace" / "test_job"
    job_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "workspace")
    study = _study(tmp_path, PROJECT)

    assert _run(task).get_return_code() == ReturnCode.OK

    assert not job_dir.exists()
    assert study.exists()


def test_dev_mode_deletes_nothing(constants, project_id, tmp_path, monkeypatch):
    constants.LOCAL_DEV = True
    job_dir = tmp_path / "workspace" / "test_job"
    job_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "workspace")
    study = _study(tmp_path, OTHER_PROJECT)

    for task in (FlipTasks.INIT_TRAINING, FlipTasks.POST_VALIDATION):
        assert _run(task).get_return_code() == ReturnCode.OK
    _cleanup().handle_event(EventType.ABOUT_TO_END_RUN, _fl_ctx())

    assert job_dir.exists()
    assert study.exists()


def test_a_missing_net_folder_is_nothing_to_clean(constants, project_id):
    assert _run(FlipTasks.INIT_TRAINING).get_return_code() == ReturnCode.OK


def test_an_unknown_task_is_refused():
    assert _run("UNKNOWN_TASK").get_return_code() == ReturnCode.TASK_UNKNOWN


def test_a_failing_task_reports_its_traceback(constants):
    with patch("os.getcwd", side_effect=Exception("Test exception")):
        result = _run(FlipTasks.POST_VALIDATION)

    assert result.get_return_code() == ReturnCode.EXECUTION_EXCEPTION
    assert result.get_header("exception") is not None


@pytest.mark.parametrize("transport", ["project", "stream"])
def test_a_job_that_does_not_mount_the_net_folder_deletes_no_images(constants, project_id, transport, tmp_path):
    """Its images, if any, are a read-only mount of imaging-api's cache (FLIP#1678), or none at all (FLIP#1049)."""
    constants.IMAGES_TRANSPORT = transport
    study = _study(tmp_path, OTHER_PROJECT)

    for task in (FlipTasks.INIT_TRAINING, FlipTasks.POST_VALIDATION):
        assert _run(task).get_return_code() == ReturnCode.OK
    _cleanup().handle_event(EventType.ABOUT_TO_END_RUN, _fl_ctx())

    assert study.read_bytes() == b"dicom"
