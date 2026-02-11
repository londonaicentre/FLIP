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

import json
import os
import tempfile
from unittest.mock import MagicMock, Mock

from nvflare.apis.dxo import DXO, DataKind
from nvflare.apis.event_type import EventType
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType

from flip.components.validation_json_generator import ValidationJsonGenerator


class TestValidationJsonGenerator:
    def setup_method(self):
        """Setup test fixtures"""
        self.generator = ValidationJsonGenerator()
        self.fl_ctx = MagicMock()
        self.fl_ctx.get_peer_context.return_value = None

    def test_init_default_values(self):
        """Test initialization with default values"""
        generator = ValidationJsonGenerator()
        assert generator is not None
        assert generator._results_dir == AppConstants.CROSS_VAL_DIR
        assert generator._json_file_name == "cross_val_results.json"

    def test_init_custom_values(self):
        """Test initialization with custom values"""
        generator = ValidationJsonGenerator(results_dir="custom_val_dir", json_file_name="custom_val.json")
        assert generator._results_dir == "custom_val_dir"
        assert generator._json_file_name == "custom_val.json"

    def test_handle_start_run_clears_results(self):
        """Test that START_RUN event clears results"""
        self.generator._val_results = {"client1": {"model1": {"accuracy": 0.9}}}

        self.generator.handle_evaluation_events(EventType.START_RUN, self.fl_ctx)

        assert len(self.generator._val_results) == 0

    def test_handle_validation_result_received_success(self):
        """Test successful handling of VALIDATION_RESULT_RECEIVED event"""
        model_owner = "site1"
        data_client = "site2"
        metrics_data = {"accuracy": 0.95, "loss": 0.05}

        # Create DXO with METRICS data
        dxo = DXO(data_kind=DataKind.METRICS, data=metrics_data)
        shareable = dxo.to_shareable()

        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: model_owner,
            AppConstants.DATA_CLIENT: data_client,
            AppConstants.VALIDATION_RESULT: shareable,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert data_client in self.generator._val_results
        assert model_owner in self.generator._val_results[data_client]
        assert self.generator._val_results[data_client][model_owner] == metrics_data

    def test_handle_validation_result_received_no_model_owner(self):
        """Test handling when model_owner is missing"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: None,
            AppConstants.DATA_CLIENT: "client1",
            AppConstants.VALIDATION_RESULT: MagicMock(),
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        # Should log error, results should remain empty
        assert len(self.generator._val_results) == 0

    def test_handle_validation_result_received_no_data_client(self):
        """Test handling when data_client is missing"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: "model1",
            AppConstants.DATA_CLIENT: None,
            AppConstants.VALIDATION_RESULT: MagicMock(),
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._val_results) == 0

    def test_handle_validation_result_received_wrong_data_kind(self):
        """Test handling when DXO has wrong data kind"""
        model_owner = "site1"
        data_client = "site2"

        # Create DXO with WEIGHTS data kind instead of METRICS
        dxo = DXO(data_kind=DataKind.WEIGHTS, data={"weight": [1, 2, 3]})
        shareable = dxo.to_shareable()

        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: model_owner,
            AppConstants.DATA_CLIENT: data_client,
            AppConstants.VALIDATION_RESULT: shareable,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        # Should not add to results due to wrong data kind
        assert data_client not in self.generator._val_results

    def test_handle_validation_result_received_no_result(self):
        """Test handling when validation result is None"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: "model1",
            AppConstants.DATA_CLIENT: "client1",
            AppConstants.VALIDATION_RESULT: None,
        }.get(key, default)

        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._val_results) == 0

    def test_handle_validation_result_received_exception(self):
        """Test handling when exception occurs processing result"""
        self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
            AppConstants.MODEL_OWNER: "model1",
            AppConstants.DATA_CLIENT: "client1",
            AppConstants.VALIDATION_RESULT: "invalid_shareable",
        }.get(key, default)

        # Should handle exception gracefully
        self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert len(self.generator._val_results) == 0

    def test_handle_end_run_creates_json_file(self):
        """Test that END_RUN event creates JSON file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)

            # Setup mock engine and workspace
            mock_workspace = Mock()
            mock_workspace.get_run_dir.return_value = run_dir
            mock_engine = Mock()
            mock_engine.get_workspace.return_value = mock_workspace
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"

            # Add some test results (cross-validation matrix)
            self.generator._val_results = {
                "site1": {"site1_model": {"accuracy": 0.95}, "site2_model": {"accuracy": 0.88}},
                "site2": {"site1_model": {"accuracy": 0.90}, "site2_model": {"accuracy": 0.93}},
            }

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            # Check that JSON file was created
            val_dir = os.path.join(run_dir, self.generator._results_dir)
            json_file = os.path.join(val_dir, self.generator._json_file_name)

            assert os.path.exists(json_file)

            # Verify contents
            with open(json_file, "r") as f:
                saved_results = json.load(f)

            assert saved_results == self.generator._val_results

    def test_handle_end_run_creates_directory_if_not_exists(self):
        """Test that END_RUN creates results directory if it doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = os.path.join(tmpdir, "run_1")
            os.makedirs(run_dir)

            mock_workspace = Mock()
            mock_workspace.get_run_dir.return_value = run_dir
            mock_engine = Mock()
            mock_engine.get_workspace.return_value = mock_workspace
            self.fl_ctx.get_engine.return_value = mock_engine
            self.fl_ctx.get_job_id.return_value = "job_123"

            self.generator._val_results = {"site1": {"model1": {"accuracy": 0.9}}}

            val_dir = os.path.join(run_dir, self.generator._results_dir)
            assert not os.path.exists(val_dir)

            self.generator.handle_evaluation_events(EventType.END_RUN, self.fl_ctx)

            assert os.path.exists(val_dir)

    def test_handle_cross_validation_matrix(self):
        """Test handling a full cross-validation matrix"""
        sites = ["site1", "site2", "site3"]

        for data_client in sites:
            for model_owner in sites:
                # Simulate different accuracy based on whether it's the same site
                accuracy = 0.95 if data_client == model_owner else 0.85
                metrics_data = {"accuracy": accuracy}
                dxo = DXO(data_kind=DataKind.METRICS, data=metrics_data)
                shareable = dxo.to_shareable()

                self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
                    AppConstants.MODEL_OWNER: model_owner,
                    AppConstants.DATA_CLIENT: data_client,
                    AppConstants.VALIDATION_RESULT: shareable,
                }.get(key, default)

                self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        # Should have results for all sites
        assert len(self.generator._val_results) == 3
        for site in sites:
            assert site in self.generator._val_results
            # Each site should have validated all models
            assert len(self.generator._val_results[site]) == 3

    def test_handle_same_client_multiple_models(self):
        """Test a single client validating multiple models"""
        data_client = "site1"
        models = ["model_a", "model_b", "model_c"]

        for model_owner in models:
            metrics_data = {"accuracy": 0.9 + (0.01 * models.index(model_owner))}
            dxo = DXO(data_kind=DataKind.METRICS, data=metrics_data)
            shareable = dxo.to_shareable()

            self.fl_ctx.get_prop.side_effect = lambda key, default=None: {
                AppConstants.MODEL_OWNER: model_owner,
                AppConstants.DATA_CLIENT: data_client,
                AppConstants.VALIDATION_RESULT: shareable,
            }.get(key, default)

            self.generator.handle_evaluation_events(AppEventType.VALIDATION_RESULT_RECEIVED, self.fl_ctx)

        assert data_client in self.generator._val_results
        assert len(self.generator._val_results[data_client]) == 3
        for model in models:
            assert model in self.generator._val_results[data_client]
