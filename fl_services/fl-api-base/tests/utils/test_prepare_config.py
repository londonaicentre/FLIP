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

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from fl_api.utils.constants import (
    GLOBAL_ROUNDS,
    LOCAL_ROUNDS,
)
from fl_api.utils.prepare_config import (
    configure_client,
    configure_config,
    configure_environment,
    configure_meta,
    configure_server,
)

MOCK_CONFIG_PATH = Path("/path/to/config.json")
MOCK_JOB_APP_DIR = Path("/job/dir/test_app")
PATH_CONFIG_FED_SERVER = MOCK_JOB_APP_DIR / "config" / "config_fed_server.json"
PATH_CONFIG_FED_CLIENT = MOCK_JOB_APP_DIR / "config" / "config_fed_client.json"
MOCK_APP_NAME = "test_app"
MOCK_PROJECT_ID = "project123"
MOCK_COHORT_QUERY = "SELECT * FROM table"
MOCK_APP_CLIENTS = ["app-trust1", "app-trust2"]
MOCK_AGGREGATION_WEIGHTS = {"trust1": 0.5, "trust2": 0.5}


@pytest.fixture
def mock_get_settings():
    with patch("fl_api.utils.prepare_config.get_settings") as mock_settings:
        yield mock_settings


@pytest.fixture
def mock_isfile():
    with patch("fl_api.utils.prepare_config.Path.is_file") as mock_isfile:
        # Default to True, can be overridden in specific tests
        mock_isfile.return_value = True
        yield mock_isfile


@pytest.fixture
def mock_read_config():
    with patch("fl_api.utils.prepare_config.read_config") as mock_read:
        yield mock_read


@pytest.fixture
def mock_write_config():
    with patch("fl_api.utils.prepare_config.write_config") as mock_write:
        yield mock_write


class TestConfigureConfig:
    def test_valid_config(self, mock_isfile, mock_read_config, mock_write_config):
        # Setup
        mock_read_config.return_value = {
            "LOCAL_ROUNDS": 5,
            "GLOBAL_ROUNDS": 3,
            "OTHER_PARAM": "value",
        }

        # Execute
        configure_config(MOCK_JOB_APP_DIR)

        # No exception raised means validation passed
        # Should mock_write_config be called? No, because the config is already valid, so it should not be modified.
        mock_write_config.assert_not_called()

    def test_valid_multi_stage_config(self, mock_isfile, mock_read_config, mock_write_config):
        # Setup
        mock_read_config.return_value = {
            "LOCAL_ROUNDS": 5,
            "LOCAL_ROUNDS_GAN": 10,
            "GLOBAL_ROUNDS": 3,
            "GLOBAL_ROUNDS_GAN": 2,
            "OTHER_PARAM": "value",
        }

        # Execute
        configure_config(MOCK_JOB_APP_DIR)

        # No exception raised means validation passed
        # Should mock_write_config be called? No, because the config is already valid, so it should not be modified.
        mock_write_config.assert_not_called()

    def test_missing_local_rounds(self, mock_isfile, mock_read_config, mock_write_config):
        # Setup
        mock_read_config.return_value = {"SOME_OTHER_KEY": "value"}

        # Execute
        configure_config(MOCK_JOB_APP_DIR)

        # Check
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        modified_config = args[0]
        assert modified_config[LOCAL_ROUNDS] == 1

        # Should mock_write_config be called? Yes, because the config is missing LOCAL_ROUNDS, so it should be added.
        mock_write_config.assert_called_once()

    def test_missing_global_rounds(self, mock_isfile, mock_read_config, mock_write_config):
        # Setup
        mock_read_config.return_value = {"LOCAL_ROUNDS": 5, "SOME_OTHER_KEY": "value"}

        # Execute
        configure_config(MOCK_JOB_APP_DIR)

        # Check
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        modified_config = args[0]
        assert modified_config[GLOBAL_ROUNDS] == 1

        # Should mock_write_config be called? Yes, because the config is missing GLOBAL_ROUNDS, so it should be added.
        mock_write_config.assert_called_once()

    def test_missing_matching_global_rounds(self, mock_isfile, mock_read_config):
        # Setup
        mock_read_config.return_value = {
            "LOCAL_ROUNDS": 5,
            "GLOBAL_ROUNDS": 3,
            "LOCAL_ROUNDS_GAN": 10,
            "OTHER_PARAM": "value",
        }

        # Execute and Assert
        with pytest.raises(
            ValueError,
            match="config.json has encountered LOCAL_ROUNDS for stage_keyword='GAN' but not the equivalent "
            "GLOBAL_ROUNDS.",
        ):
            configure_config(MOCK_JOB_APP_DIR)

    # Add test for what happens in a multi-stage config if the stage keys are okay but the global keys are not
    # e.g. LOCAL_ROUNDS not present, but LOCAL_ROUNDS_GAN is present, and GLOBAL_ROUNDS_GAN is present.
    def test_missing_global_rounds_in_multi_stage_config(self, mock_isfile, mock_read_config):
        # Setup
        mock_read_config.return_value = {
            "LOCAL_ROUNDS_GAN": 10,
            "GLOBAL_ROUNDS_GAN": 2,
            "OTHER_PARAM": "value",
        }

        # Execute and Assert
        with pytest.raises(
            ValueError,
            # re.escape is used to escape the parentheses in the error message, which are part of the string and not
            # regex groups.
            match=re.escape(
                "config.json has encountered 1 local rounds key (LOCAL_ROUNDS_GAN). "
                "When only 1 local rounds key is present, it must be called LOCAL_ROUNDS. "
                "Please change the name of the local rounds key to LOCAL_ROUNDS."
            ),
        ):
            configure_config(MOCK_JOB_APP_DIR)


class TestConfigureClient:
    def test_configure_client_success(self, mock_isfile, mock_write_config, mock_read_config):
        # Setup

        mock_client_config = {"existing_key": "existing_value"}
        mock_read_config.return_value = mock_client_config

        # Execute
        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
        )

        # Assert
        mock_isfile.assert_called_once()
        mock_read_config.assert_called_once()

        # Check that the config was modified correctly
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        modified_config = args[0]
        assert modified_config["project_id"] == MOCK_PROJECT_ID
        assert modified_config["query"] == MOCK_COHORT_QUERY
        assert modified_config["existing_key"] == "existing_value"

    def test_configure_client_file_not_found(self, mock_isfile):
        # Setup
        mock_isfile.return_value = False

        # Execute and Assert
        with pytest.raises(
            FileNotFoundError,
            match="No config_fed_client.json found in app 'test_app'",
        ):
            configure_client(
                job_dir=MOCK_JOB_APP_DIR,
                app_name=MOCK_APP_NAME,
                project_id=MOCK_PROJECT_ID,
                cohort_query=MOCK_COHORT_QUERY,
            )


class TestConfigureServer:
    def test_configure_server_success(self, mock_isfile, mock_read_config, mock_write_config):
        # Setup

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
        mock_read_config.return_value = mock_server_config

        # Execute
        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="new_aggregator",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        # Assert
        mock_read_config.assert_called_once()
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        modified_config = args[0]
        assert modified_config["model_id"] == MOCK_APP_NAME
        assert modified_config["global_rounds"] == 3
        assert modified_config["min_clients"] == 2
        assert modified_config["workflows"][2]["args"]["participating_clients"] == MOCK_APP_CLIENTS
        assert modified_config["workflows"][1]["args"]["ignore_result_error"] is True
        assert modified_config["components"][2]["name"] == "new_aggregator"
        assert modified_config["components"][2]["args"]["aggregation_weights"] == MOCK_AGGREGATION_WEIGHTS

    def test_configure_server_file_not_found(self, mock_isfile):
        # Setup
        mock_isfile.return_value = False

        # Execute and Assert
        with pytest.raises(
            FileNotFoundError,
            match="No config_fed_server.json found in app 'test_app'",
        ):
            configure_server(
                job_dir=MOCK_JOB_APP_DIR,
                app_name=MOCK_APP_NAME,
                global_rounds=3,
                trusts=MOCK_APP_CLIENTS,
                ignore_result_error=True,
                aggregator="new_aggregator",
                aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            )


class TestConfigureMeta:
    def test_configure_meta_without_gpus(self, mock_write_config, mock_get_settings):
        # Settings mock
        # If num_gpus is not > 0, resource_spec should be set to an empty dict
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 0
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        # Execute
        configure_meta(MOCK_JOB_APP_DIR, MOCK_APP_NAME, MOCK_APP_CLIENTS)

        # Assert
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        meta_config = args[0]
        assert meta_config["name"] == MOCK_APP_NAME
        assert meta_config["resource_spec"] == {}
        assert meta_config["deploy_map"] == {"app": ["server"] + MOCK_APP_CLIENTS}
        assert meta_config["min_clients"] == 2
        assert meta_config["mandatory_clients"] == MOCK_APP_CLIENTS

    def test_configure_meta_with_gpus(self, mock_write_config, mock_get_settings):
        # Settings mock
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 2
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        expected_resource_spec_per_client = {"num_gpus": 2, "mem_per_gpu_in_GiB": 16}

        # Execute
        configure_meta(MOCK_JOB_APP_DIR, MOCK_APP_NAME, MOCK_APP_CLIENTS)

        # Assert
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        meta_config = args[0]
        assert meta_config["name"] == MOCK_APP_NAME
        assert meta_config["resource_spec"] == {
            client: expected_resource_spec_per_client for client in MOCK_APP_CLIENTS
        }
        assert meta_config["deploy_map"] == {"app": ["server"] + MOCK_APP_CLIENTS}
        assert meta_config["min_clients"] == 2
        assert meta_config["mandatory_clients"] == MOCK_APP_CLIENTS


class TestConfigureEnvironment:
    def test_configure_environment(self, mock_write_config):
        # Execute
        configure_environment(MOCK_JOB_APP_DIR)

        # Assert
        mock_write_config.assert_called_once()
        args, _ = mock_write_config.call_args
        env_config = args[0]

        from nvflare.app_common.app_constant import EnvironmentKey

        assert env_config[EnvironmentKey.CHECKPOINT_DIR] == "model"
