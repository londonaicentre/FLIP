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
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from nvflare.app_opt.pt.file_model_persistor import PTFileModelPersistor

from flip.constants import PTConstants
from flip.exceptions import ResultsUploadError
from flip.nvflare.components.persist_and_cleanup import PersistToS3AndCleanup


class TestPersistToS3AndCleanup:
    """Tests for PersistToS3AndCleanup component"""

    def test_init_with_valid_model_id(self):
        """Test initialization with valid model UUID"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        assert component._model_id_fallback == model_id
        assert component._model_id is None
        assert component.persistor_id == "persistor"
        assert component.flip == flip
        assert component.model_persistor is None
        assert component.model_inventory == {}

    def test_resolves_model_id_from_fallback_arg(self):
        """_resolve_model_id returns the constructor fallback when no job-metadata prop is set."""
        model_id = "abcdef01-2345-6789-abcd-ef0123456789"
        component = PersistToS3AndCleanup(model_id=model_id)
        fl_ctx = MagicMock()
        fl_ctx.get_prop.return_value = None
        assert component._resolve_model_id(fl_ctx) == model_id

    def test_init_with_custom_persistor_id(self):
        """Test initialization with custom persistor_id"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, persistor_id="custom_persistor", flip=flip)

        assert component.persistor_id == "custom_persistor"

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_no_engine(self, mock_constants):
        """Test execute when engine is not found"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value = None

        component.execute(fl_ctx)

        component.system_panic.assert_called_once()
        assert "Engine not found" in str(component.system_panic.call_args)

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_invalid_persistor_component(self, mock_constants):
        """Test execute when persistor component is not PTFileModelPersistor"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.system_panic = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None

        # Return a MagicMock instead of PTFileModelPersistor
        fl_ctx.get_engine.return_value.get_component.return_value = MagicMock()

        component.execute(fl_ctx)

        component.system_panic.assert_called_once()
        assert "must be PTFileModelPersistor" in str(component.system_panic.call_args)

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_success_with_model_inventory(self, mock_constants):
        """Test successful execute with model inventory"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        # Mock PTFileModelPersistor
        persistor = Mock(spec=PTFileModelPersistor)
        model_location = MagicMock()
        model_location.location = "/path/to/model"
        persistor.get_model_inventory.return_value = {PTConstants.PTFileModelName: model_location}
        engine.get_component.return_value = persistor

        with patch.object(component, "upload_results_to_s3_bucket"):
            with patch.object(component, "cleanup"):
                with patch.object(component, "fire_event"):
                    component.execute(fl_ctx)

        assert component.model_dir == "/path/to/model"

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_without_model_inventory(self, mock_constants):
        """Test execute when model inventory doesn't have model"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_warning = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        persistor = Mock(spec=PTFileModelPersistor)
        persistor.get_model_inventory.return_value = {}
        engine.get_component.return_value = persistor

        with patch.object(component, "upload_results_to_s3_bucket"):
            with patch.object(component, "cleanup"):
                with patch.object(component, "fire_event"):
                    component.execute(fl_ctx)

        component.log_warning.assert_called_once()
        assert "Unable to retrieve" in str(component.log_warning.call_args)

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.move")
    @patch("shutil.rmtree")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_to_s3_bucket(self, mock_isdir, mock_isfile, mock_rmtree, mock_move, mock_constants):
        """Test upload_results_to_s3_bucket in production mode"""
        mock_constants.LOCAL_DEV = False
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "job-123"
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = "/mock/workspace"

        mock_isfile.return_value = True
        mock_isdir.return_value = True

        component.upload_results_to_s3_bucket(fl_ctx)

        flip.upload_results_to_s3.assert_called_once()

        # Should move global model, trainer.py, and validator.py if they exist
        assert mock_move.call_count == 3
        # Two dirs are pruned before zipping: app_server + cross_site_val/model_shareables.
        assert mock_rmtree.call_count == 2

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.move")
    @patch("shutil.rmtree")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_prunes_cross_site_val_models(
        self, mock_isdir, mock_isfile, mock_rmtree, mock_move, mock_constants
    ):
        """The cross-site-eval model artefacts must be pruned from the results zip.

        CrossSiteModelEval writes each validated model to ``cross_site_val/model_shareables/`` only to
        broadcast it to clients for scoring (e.g. the ~759 MiB Ark+ evaluation checkpoint the user
        already uploaded) — an eval input, not an output. Metrics (``evaluation_results/``) and the raw
        per-site shareables (``cross_site_val/result_shareables/``) are retained.
        """
        mock_constants.LOCAL_DEV = False
        run_dir = "/mock/workspace/run"
        cross_val_models_dir = os.path.join(run_dir, "cross_site_val", "model_shareables")

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id="123e4567-e89b-12d3-a456-426614174000", flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "job-123"
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = run_dir

        mock_isfile.return_value = False  # no global model / trainer / validator to move
        # Only the cross-site-eval model dir "exists" (not app_server), isolating the prune under test.
        mock_isdir.side_effect = lambda p: p == cross_val_models_dir

        component.upload_results_to_s3_bucket(fl_ctx)

        mock_rmtree.assert_called_once_with(cross_val_models_dir)
        flip.upload_results_to_s3.assert_called_once()

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    @patch("shutil.move")
    @patch("shutil.rmtree")
    @patch("os.path.isfile")
    @patch("os.path.isdir")
    def test_upload_results_training_job_has_no_cross_site_val(
        self, mock_isdir, mock_isfile, mock_rmtree, mock_move, mock_constants
    ):
        """Training jobs have no ``cross_site_val`` dir, so the prune is a safe no-op there."""
        mock_constants.LOCAL_DEV = False
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id="123e4567-e89b-12d3-a456-426614174000", flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_job_id.return_value = "job-123"
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = "/mock/workspace/run"

        mock_isfile.return_value = False
        mock_isdir.return_value = False  # neither app_server nor cross_site_val exist

        component.upload_results_to_s3_bucket(fl_ctx)

        mock_rmtree.assert_not_called()
        flip.upload_results_to_s3.assert_called_once()

    @patch("os.path.isdir")
    def test_cleanup(self, mock_isdir):
        """Test cleanup"""
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)

        fl_ctx = MagicMock()
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_root_dir.return_value = "/mock/workspace"

        mock_isdir.return_value = True

        component.cleanup(fl_ctx)

        expected_calls = [
            call(Path("/mock/workspace") / "save" / model_id),
            call(Path("/mock/workspace") / "transfer" / model_id),
        ]

        flip.cleanup.assert_has_calls(expected_calls, any_order=False)
        assert flip.cleanup.call_count == 2

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_reraises_base_exception(self, mock_constants):
        """execute should log and re-raise even a non-Exception BaseException.

        Guards the ``except BaseException`` handler specifically: a ``KeyboardInterrupt``
        (which an ``except Exception`` would not catch) must still be logged and
        propagated unchanged, so an interrupted run is never silently swallowed.
        """
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_exception = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        persistor = Mock(spec=PTFileModelPersistor)
        model_location = MagicMock()
        model_location.location = "/path/to/model"
        persistor.get_model_inventory.return_value = {PTConstants.PTFileModelName: model_location}
        engine.get_component.return_value = persistor

        with (
            patch.object(component, "upload_results_to_s3_bucket", side_effect=KeyboardInterrupt),
            patch.object(component, "cleanup"),
            patch.object(component, "fire_event"),
        ):
            with pytest.raises(KeyboardInterrupt):
                component.execute(fl_ctx)

        component.log_exception.assert_called_once()

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_execute_reraises_original_exception_type(self, mock_constants):
        """execute should re-raise the original exception type rather than masking it.

        Guards the bare ``raise`` in the ``except BaseException`` handler: a
        ResultsUploadError surfacing from the upload step must propagate unchanged so
        the server event handler can map it to RESULTS_UPLOAD_FAILED instead of a
        blanket ERROR. A plain ``raise Exception`` here would lose that distinction.
        """
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"
        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_exception = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        engine = MagicMock()
        fl_ctx.get_engine.return_value = engine

        persistor = Mock(spec=PTFileModelPersistor)
        model_location = MagicMock()
        model_location.location = "/path/to/model"
        persistor.get_model_inventory.return_value = {PTConstants.PTFileModelName: model_location}
        engine.get_component.return_value = persistor

        with (
            patch.object(component, "upload_results_to_s3_bucket", side_effect=ResultsUploadError("S3 upload failed")),
            patch.object(component, "cleanup"),
            patch.object(component, "fire_event"),
        ):
            with pytest.raises(ResultsUploadError, match="S3 upload failed"):
                component.execute(fl_ctx)

        component.log_exception.assert_called_once()

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_upload_results_with_general_exception(self, mock_constants):
        """Test upload_results_to_s3_bucket with general exception"""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_error = MagicMock()
        component.cleanup = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = "/mock/workspace/run"
        fl_ctx.get_job_id.return_value = "job-123"

        flip.upload_results_to_s3.side_effect = Exception("Upload failed")

        # Implementation catches, runs cleanup, then re-raises the original exception
        with pytest.raises(Exception, match="Upload failed"):
            component.upload_results_to_s3_bucket(fl_ctx)

        component.cleanup.assert_called_once()

    @patch("flip.nvflare.components.persist_and_cleanup.FlipConstants")
    def test_upload_results_preserves_results_upload_error(self, mock_constants):
        """upload_results_to_s3_bucket should re-raise ResultsUploadError with its type intact."""
        mock_constants.LOCAL_DEV = True
        model_id = "123e4567-e89b-12d3-a456-426614174000"

        flip = MagicMock()
        component = PersistToS3AndCleanup(model_id=model_id, flip=flip)
        component.log_info = MagicMock()
        component.log_error = MagicMock()
        component.cleanup = MagicMock()

        fl_ctx = MagicMock()
        fl_ctx.get_peer_context.return_value = None
        fl_ctx.get_engine.return_value.get_workspace.return_value.get_run_dir.return_value = "/mock/workspace/run"
        fl_ctx.get_job_id.return_value = "job-123"

        flip.upload_results_to_s3.side_effect = ResultsUploadError("S3 upload failed")

        with pytest.raises(ResultsUploadError, match="S3 upload failed"):
            component.upload_results_to_s3_bucket(fl_ctx)

        component.cleanup.assert_called_once()
