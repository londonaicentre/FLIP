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

import json
from unittest.mock import mock_open, patch

import pytest

from fl_api.utils.prepare_config import (
    configure_client,
    configure_config,
    configure_environment,
    configure_meta,
    configure_server,
)


@pytest.fixture
def mock_get_settings():
    with patch("fl_api.utils.prepare_config.get_settings") as mock_settings:
        yield mock_settings


class TestConfigureConfig:
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=json.dumps({
            "LOCAL_ROUNDS": 5,
            "GLOBAL_ROUNDS": 3,
            "OTHER_PARAM": "value",
        }),
    )
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_valid_config(self, mock_join, mock_isfile, mock_file):
        # Setup
        mock_join.return_value = "/path/to/config.json"
        mock_isfile.return_value = True

        # Execute
        configure_config("/job/dir/test_app")

        # Assert
        mock_join.assert_called_with("/job/dir/test_app", "custom", "config.json")
        assert mock_join.call_count > 0
        mock_file.assert_called_with("/path/to/config.json", "r")
        assert mock_file.call_count > 0
        # No exception raised means validation passed

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=json.dumps({
            "LOCAL_ROUNDS": 5,
            "LOCAL_ROUNDS_GAN": 10,
            "GLOBAL_ROUNDS": 3,
            "GLOBAL_ROUNDS_GAN": 2,
            "OTHER_PARAM": "value",
        }),
    )
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_valid_multi_stage_config(self, mock_join, mock_isfile, mock_file):
        # Setup
        mock_join.return_value = "/path/to/config.json"
        mock_isfile.return_value = True

        # Execute
        configure_config("/job/dir/test_app")

        # Assert
        mock_join.assert_called_once_with("/job/dir/test_app", "custom", "config.json")
        mock_file.assert_called_once_with("/path/to/config.json", "r")
        # No exception raised means validation passed

    @patch("json.dump")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=json.dumps({"SOME_OTHER_KEY": "value"}),
    )
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_missing_local_rounds(self, mock_join, mock_isfile, mock_file, mock_dump):
        # Setup
        mock_join.return_value = "/path/to/config.json"
        mock_isfile.return_value = True

        # Execute
        configure_config("/job/dir/test_app")

        # Check
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        modified_config = args[0]
        assert modified_config["LOCAL_ROUNDS"] == 1

    @patch("json.dump")
    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=json.dumps({"LOCAL_ROUNDS": 5, "SOME_OTHER_KEY": "value"}),
    )
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_missing_global_rounds(self, mock_join, mock_isfile, mock_file, mock_dump):
        # Setup
        mock_join.return_value = "/path/to/config.json"
        mock_isfile.return_value = True

        # Execute
        configure_config("/job/dir/test_app")

        # Check
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        modified_config = args[0]
        assert modified_config["GLOBAL_ROUNDS"] == 1

    @patch(
        "builtins.open",
        new_callable=mock_open,
        read_data=json.dumps({
            "LOCAL_ROUNDS": 5,
            "GLOBAL_ROUNDS": 3,
            "LOCAL_ROUNDS_GAN": 10,
            "OTHER_PARAM": "value",
        }),
    )
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_missing_matching_global_rounds(self, mock_join, mock_isfile, mock_file):
        # Setup
        mock_join.return_value = "/path/to/config.json"
        mock_isfile.return_value = True

        # Execute and Assert
        with pytest.raises(
            ValueError,
            match="config.json has encountered LOCAL_ROUNDS for stage GAN but not the equivalent GLOBAL_ROUNDS.",
        ):
            configure_config("/job/dir/test_app")


class TestConfigureClient:
    @patch("builtins.open")
    @patch("json.load")
    @patch("json.dump")
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_configure_client_success(self, mock_join, mock_isfile, mock_dump, mock_load, mock_open):
        # Setup
        mock_join.return_value = "/path/to/config_fed_client.json"
        mock_isfile.return_value = True
        mock_client_config = {"existing_key": "existing_value"}
        mock_load.return_value = mock_client_config

        # Execute
        configure_client("/job/dir/test_app", "test_app", "project123", "SELECT * FROM table")

        # Assert
        mock_join.assert_called_once_with("/job/dir/test_app", "config", "config_fed_client.json")
        mock_isfile.assert_called_once_with("/path/to/config_fed_client.json")
        mock_load.assert_called_once()

        # Check that the config was modified correctly
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        modified_config = args[0]
        assert modified_config["project_id"] == "project123"
        assert modified_config["query"] == "SELECT * FROM table"
        assert modified_config["existing_key"] == "existing_value"

    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_configure_client_file_not_found(self, mock_join, mock_isfile):
        # Setup
        mock_join.return_value = "/path/to/config_fed_client.json"
        mock_isfile.return_value = False

        # Execute and Assert
        with pytest.raises(
            FileNotFoundError,
            match="No config_fed_client.json found in app 'test_app'",
        ):
            configure_client("/job/dir", "test_app", "project123", "SELECT * FROM table")


class TestConfigureServer:
    @patch("builtins.open")
    @patch("json.load")
    @patch("json.dump")
    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_configure_server_success(self, mock_join, mock_isfile, mock_dump, mock_load, mock_open):
        # Setup
        mock_join.return_value = "/path/to/config_fed_server.json"
        mock_isfile.return_value = True
        mock_server_config = {
            "existing_key": "existing_value",
            "workflows": [
                {"id": "workflow1"},
                {"args": {"ignore_result_error": False}},
                {"args": {"participating_clients": []}},
            ],
            "components": [
                {"id": "component1"},
                {"id": "component2"},
                {"id": "component3", "name": "old_aggregator", "args": {"aggregation_weights": {}}},
            ],
        }
        mock_load.return_value = mock_server_config

        # Execute
        configure_server(
            "/job/dir/test_app",
            "test_app",
            3,
            ["trust1", "trust2"],
            True,
            "new_aggregator",
            {"trust1": 0.5, "trust2": 0.5},
        )

        # Assert
        mock_join.assert_called_once_with("/job/dir/test_app", "config", "config_fed_server.json")
        mock_isfile.assert_called_once_with("/path/to/config_fed_server.json")
        mock_load.assert_called_once()

        # Check that the config was modified correctly
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        modified_config = args[0]
        assert modified_config["model_id"] == "test_app"
        assert modified_config["global_rounds"] == 3
        assert modified_config["min_clients"] == 2
        assert modified_config["workflows"][2]["args"]["participating_clients"] == [
            "trust1",
            "trust2",
        ]
        assert modified_config["workflows"][1]["args"]["ignore_result_error"] is True
        assert modified_config["components"][2]["name"] == "new_aggregator"
        assert modified_config["components"][2]["args"]["aggregation_weights"] == {
            "trust1": 0.5,
            "trust2": 0.5,
        }

    @patch("os.path.isfile")
    @patch("os.path.join")
    def test_configure_server_file_not_found(self, mock_join, mock_isfile):
        # Setup
        mock_join.return_value = "/path/to/config_fed_server.json"
        mock_isfile.return_value = False

        # Execute and Assert
        with pytest.raises(
            FileNotFoundError,
            match="No config_fed_server.json found in app 'test_app'",
        ):
            configure_server(
                "/job/dir/test_app",
                "test_app",
                3,
                ["trust1", "trust2"],
                True,
                "new_aggregator",
                {"trust1": 0.5, "trust2": 0.5},
            )


class TestConfigureMeta:
    @patch("builtins.open")
    @patch("json.dump")
    @patch("os.path.join")
    def test_configure_meta_without_gpus(self, mock_join, mock_dump, mock_open, mock_get_settings):
        # Setup
        mock_join.return_value = "/path/to/meta.json"

        # Settings mock
        # If num_gpus is not > 0, no resource_spec should be set
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 0
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        # Execute
        configure_meta("/job/dir", "test_app", ["trust1", "trust2"])

        # Assert
        mock_join.assert_called_once_with("/job/dir", "meta.json")
        mock_open.assert_called_once_with("/path/to/meta.json", "w")

        # Check that the config was created correctly
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        meta_config = args[0]
        assert meta_config["name"] == "test_app"
        assert meta_config["resource_spec"] == {}
        assert meta_config["deploy_map"] == {"app": ["server", "trust1", "trust2"]}
        assert meta_config["min_clients"] == 2
        assert meta_config["mandatory_clients"] == ["trust1", "trust2"]

    @patch("builtins.open")
    @patch("json.dump")
    @patch("os.path.join")
    def test_configure_meta_with_gpus(self, mock_join, mock_dump, mock_open, mock_get_settings):
        # Setup
        mock_join.return_value = "/path/to/meta.json"

        # Settings mock
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 2
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        # Execute
        configure_meta("/job/dir", "test_app", ["trust1", "trust2"])

        # Assert
        mock_join.assert_called_once_with("/job/dir", "meta.json")
        mock_open.assert_called_once_with("/path/to/meta.json", "w")

        # Check that the config was created correctly
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        meta_config = args[0]
        assert meta_config["name"] == "test_app"
        assert meta_config["resource_spec"] == {
            "trust1": {"num_gpus": 2, "mem_per_gpu_in_GiB": 16},
            "trust2": {"num_gpus": 2, "mem_per_gpu_in_GiB": 16},
        }
        assert meta_config["deploy_map"] == {"app": ["server", "trust1", "trust2"]}
        assert meta_config["min_clients"] == 2
        assert meta_config["mandatory_clients"] == ["trust1", "trust2"]


class TestConfigureEnvironment:
    @patch("builtins.open")
    @patch("json.dump")
    @patch("os.path.join")
    def test_configure_environment(self, mock_join, mock_dump, mock_open):
        # Setup
        mock_join.return_value = "/path/to/environment.json"

        # Execute
        configure_environment("/job/dir/test_app")

        # Assert
        mock_join.assert_called_once_with("/job/dir/test_app", "config", "environment.json")
        mock_open.assert_called_once_with("/path/to/environment.json", "w")

        # Check that the config was created correctly
        mock_dump.assert_called_once()
        args, _ = mock_dump.call_args
        env_config = args[0]
        from nvflare.app_common.app_constant import EnvironmentKey

        assert env_config[EnvironmentKey.CHECKPOINT_DIR] == "model"
