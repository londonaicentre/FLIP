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


def _assert_no_duplicate_task_chains(config: dict) -> None:
    """Each task may appear in at most ONE task_data_filters chain — NVFLARE's FedJsonConfigurator
    raises ConfigError ("multiple data filter chains defined for task ...") at job parse time
    otherwise, failing the job on every client/server before any filter runs."""
    tasks = [task for block in config.get("task_data_filters", []) for task in block.get("tasks", [])]
    assert len(tasks) == len(set(tasks)), f"task covered by more than one task_data_filters chain: {tasks}"


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

    def test_configure_client_injects_keep_only_vars_filter(self, mock_isfile, mock_write_config, mock_read_config):
        """When aggregate_only_regex is set, a KeepOnlyVars filter is prepended to the train
        task_result_filters (before any existing filter), shrinking the per-round update (FLIP#684)."""
        mock_read_config.return_value = {
            "task_result_filters": [
                {
                    "tasks": ["train"],
                    "filters": [{"id": "percentile_privacy", "path": "flip.nvflare.components.PercentilePrivacy"}],
                }
            ]
        }

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        train_block = next(b for b in modified_config["task_result_filters"] if "train" in b["tasks"])
        filters = train_block["filters"]
        # KeepOnlyVars first (runs before PercentilePrivacy), with the include regex.
        assert filters[0]["path"] == "flip.nvflare.components.KeepOnlyVars"
        assert filters[0]["args"]["include_vars"] == "omni_heads"
        assert filters[1]["id"] == "percentile_privacy"

    def test_configure_client_keep_only_vars_creates_train_block_when_absent(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """A client config with no train task_result_filters block gets one created carrying
        KeepOnlyVars (apps without a PercentilePrivacy filter still get the head-only shrink)."""
        mock_read_config.return_value = {}

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        train_block = next(b for b in modified_config["task_result_filters"] if "train" in b["tasks"])
        assert train_block["filters"][0]["path"] == "flip.nvflare.components.KeepOnlyVars"
        assert train_block["filters"][0]["args"]["include_vars"] == "omni_heads"

    def test_configure_client_no_regex_leaves_filters_untouched(self, mock_isfile, mock_write_config, mock_read_config):
        """No aggregate_only_regex → no KeepOnlyVars injected (unchanged for every normal job)."""
        mock_read_config.return_value = {"task_result_filters": [{"tasks": ["train"], "filters": []}]}

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
        )

        modified_config = mock_write_config.call_args[0][0]
        train_block = next(b for b in modified_config["task_result_filters"] if "train" in b["tasks"])
        assert all(f.get("path") != "flip.nvflare.components.KeepOnlyVars" for f in train_block["filters"])

    def test_configure_client_injects_reconstruct_full_model_filter(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """With aggregate_only_regex set, a ReconstructFullModelForEval filter is prepended as its own
        chain over BOTH ``train`` and ``validate`` — the client half of the head-only broadcast for
        training AND cross-site validation (one instance shares the round-0 backbone cache across both
        tasks; #730)."""
        mock_read_config.return_value = {"task_result_filters": [{"tasks": ["train"], "filters": []}]}

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        reconstruct_block = modified_config["task_data_filters"][0]
        assert reconstruct_block["tasks"] == ["train", "validate"]
        assert reconstruct_block["filters"][0]["path"] == "flip.nvflare.components.ReconstructFullModelForEval"
        _assert_no_duplicate_task_chains(modified_config)

    def test_configure_client_reconstruct_filter_folds_existing_train_chain(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """An existing train task_data_filters chain is FOLDED into the injected ``["train",
        "validate"]`` chain — NVFLARE rejects a task covered by two chains, so it cannot be left
        alongside. ReconstructFullModelForEval stays first so the full model is rebuilt before any
        other incoming data filter runs; unrelated chains are preserved untouched."""
        mock_read_config.return_value = {
            "task_data_filters": [
                {"tasks": ["train"], "filters": [{"id": "existing", "path": "some.Filter"}]},
                {"tasks": ["other_task"], "filters": [{"id": "unrelated", "path": "some.OtherFilter"}]},
            ]
        }

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        merged_block = modified_config["task_data_filters"][0]
        assert merged_block["tasks"] == ["train", "validate"]
        assert merged_block["filters"][0]["path"] == "flip.nvflare.components.ReconstructFullModelForEval"
        assert merged_block["filters"][1]["id"] == "existing"
        # The unrelated chain is preserved unchanged, after the injected chain.
        assert modified_config["task_data_filters"][1]["filters"][0]["id"] == "unrelated"
        _assert_no_duplicate_task_chains(modified_config)

    def test_configure_client_reconstruct_filter_supersedes_recipe_baked_one(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """A ReconstructFullModel data filter already baked into the app config (e.g. by a
        ``FlipFedAvgRecipe(aggregate_only_regex=...)`` export) is dropped as superseded: the
        deploy-time chain must hold the ONE instance whose round-0 cache is shared across train and
        validate — keeping both would reconstruct twice via a second, cache-less instance."""
        mock_read_config.return_value = {
            "task_data_filters": [
                {
                    "tasks": ["train"],
                    "filters": [
                        {"id": "reconstruct_full_model", "path": "flip.nvflare.components.ReconstructFullModel"}
                    ],
                }
            ]
        }

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        assert modified_config["task_data_filters"] == [
            {
                "tasks": ["train", "validate"],
                "filters": [
                    {
                        "id": "reconstruct_full_model",
                        "path": "flip.nvflare.components.ReconstructFullModelForEval",
                        "args": {},
                    }
                ],
            }
        ]

    def test_configure_client_reconstruct_filter_rejects_unmergeable_chain(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """A chain covering train/validate together with OTHER tasks cannot be folded without changing
        which tasks its filters run on → clear configure-time error, instead of NVFLARE's parse-time
        duplicate-chain ConfigError on every client."""
        mock_read_config.return_value = {
            "task_data_filters": [
                {"tasks": ["train", "custom_task"], "filters": [{"id": "existing", "path": "some.Filter"}]}
            ]
        }

        with pytest.raises(ValueError, match=r"chain over \['train', 'custom_task'\]"):
            configure_client(
                job_dir=MOCK_JOB_APP_DIR,
                app_name=MOCK_APP_NAME,
                project_id=MOCK_PROJECT_ID,
                cohort_query=MOCK_COHORT_QUERY,
                aggregate_only_regex="omni_heads",
            )

    def test_configure_client_no_regex_leaves_data_filters_untouched(
        self, mock_isfile, mock_write_config, mock_read_config
    ):
        """No aggregate_only_regex → no reconstruct filter injected (normal full-model broadcast) —
        this is the ``evaluation``/multimodel path, which must never head-only-trim."""
        mock_read_config.return_value = {"task_result_filters": [{"tasks": ["train"], "filters": []}]}

        configure_client(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            project_id=MOCK_PROJECT_ID,
            cohort_query=MOCK_COHORT_QUERY,
        )

        modified_config = mock_write_config.call_args[0][0]
        assert all(
            f.get("path") != "flip.nvflare.components.ReconstructFullModelForEval"
            for b in modified_config.get("task_data_filters", [])
            for f in b.get("filters", [])
        )

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

    def _minimal_server_config(self):
        return {
            "workflows": [{"args": {"participating_clients": []}}],
            "components": [{"id": "aggregator", "name": "agg", "args": {"aggregation_weights": {}}}],
        }

    def test_configure_server_overrides_literal_num_rounds(self, mock_isfile, mock_read_config, mock_write_config):
        """Recipe-generated templates carry a LITERAL num_rounds (FedJob serialisation), not the
        executor templates' "{global_rounds}" placeholder — configure_server must override it or
        every deployed client_api job runs the template default (3 rounds) regardless of the user's
        GLOBAL_ROUNDS (observed live: a GLOBAL_ROUNDS=20 job ran 3 rounds)."""
        config = self._minimal_server_config()
        config["workflows"][0]["args"]["num_rounds"] = 3  # literal, as FedJob serialises it
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=20,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        (modified_config, _), _ = mock_write_config.call_args
        assert modified_config["workflows"][0]["args"]["num_rounds"] == 20

    def test_configure_server_leaves_placeholder_num_rounds_untouched(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """Executor templates use "num_rounds": "{global_rounds}" resolved by NVFLARE from the
        top-level global_rounds key — the string placeholder must NOT be clobbered."""
        config = self._minimal_server_config()
        config["workflows"][0]["args"]["num_rounds"] = "{global_rounds}"
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=20,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        (modified_config, _), _ = mock_write_config.call_args
        assert modified_config["workflows"][0]["args"]["num_rounds"] == "{global_rounds}"
        assert modified_config["global_rounds"] == 20

    def test_configure_server_overrides_literal_min_clients(self, mock_isfile, mock_read_config, mock_write_config):
        """Recipe-generated templates also carry a LITERAL min_clients (default 1) — with 2 trusts
        that lets ScatterAndGather close every round on the fastest trust's update alone, silently
        dropping the slower trust's contributions (observed live: a 2-trust 20-round job aggregated
        20/20 updates from one trust, 0 from the other). Must be overridden to the trust count;
        the executor templates' "{min_clients}" string placeholder must survive untouched."""
        config = self._minimal_server_config()
        config["workflows"][0]["args"]["min_clients"] = 1  # literal, as FedJob serialises it
        config["workflows"].append({"args": {"min_clients": "{min_clients}"}})  # executor-style
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=20,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        (modified_config, _), _ = mock_write_config.call_args
        assert modified_config["workflows"][0]["args"]["min_clients"] == len(MOCK_APP_CLIENTS)
        assert modified_config["workflows"][1]["args"]["min_clients"] == "{min_clients}"
        assert modified_config["min_clients"] == len(MOCK_APP_CLIENTS)

    def test_configure_server_diffusion_recipe_overrides_min_clients_only(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """The diffusion_model template's ScatterAndGatherLDM workflows carry literal
        min_clients (must be overridden to the trust count, as for the other recipe templates) but
        num_rounds_ae/num_rounds_dm instead of num_rounds — those must pass through untouched:
        the controller re-reads GLOBAL_ROUNDS_AE/GLOBAL_ROUNDS_DM from the app config.json at
        start, so the deployed per-stage round counts come from the user config, not from here."""
        config = self._minimal_server_config()
        config["workflows"][0]["args"].update(
            {"min_clients": 1, "num_rounds_ae": 1, "num_rounds_dm": 1, "train_task_name": "train_ae"}
        )
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=20,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        (modified_config, _), _ = mock_write_config.call_args
        sag_args = modified_config["workflows"][0]["args"]
        assert sag_args["min_clients"] == len(MOCK_APP_CLIENTS)
        assert sag_args["num_rounds_ae"] == 1
        assert sag_args["num_rounds_dm"] == 1

    def test_configure_server_injects_trim_broadcast_filter(self, mock_isfile, mock_read_config, mock_write_config):
        """With aggregate_only_regex set, a TrimBroadcastVars filter is appended to the train
        task_data_filters — the server half of the head-only broadcast (after round 0 it broadcasts
        only the trainable params instead of the full ~759 MiB model)."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        train_block = next(b for b in modified_config["task_data_filters"] if "train" in b["tasks"])
        trim_filter = train_block["filters"][-1]
        assert trim_filter["path"] == "flip.nvflare.components.TrimBroadcastVars"
        assert trim_filter["args"]["include_vars"] == "omni_heads"

    def test_configure_server_trim_filter_appended_to_existing_train_block(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """An existing train task_data_filters block keeps its filters; TrimBroadcastVars is
        appended so it runs after any other outgoing data filter."""
        config = self._minimal_server_config()
        config["task_data_filters"] = [{"tasks": ["train"], "filters": [{"id": "existing", "path": "some.Filter"}]}]
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        train_block = next(b for b in modified_config["task_data_filters"] if "train" in b["tasks"])
        assert train_block["filters"][0]["id"] == "existing"
        assert train_block["filters"][-1]["path"] == "flip.nvflare.components.TrimBroadcastVars"
        assert train_block["filters"][-1]["args"]["include_vars"] == "omni_heads"

    def test_configure_server_injects_trim_eval_broadcast_filter(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """With aggregate_only_regex set, a TrimEvalBroadcastVars filter is added to a ``validate``
        task_data_filters chain — the server half of head-only cross-site validation (#730). It is a
        separate chain from the ``train`` TrimBroadcastVars one."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        validate_block = next(b for b in modified_config["task_data_filters"] if b["tasks"] == ["validate"])
        eval_filter = validate_block["filters"][-1]
        assert eval_filter["path"] == "flip.nvflare.components.TrimEvalBroadcastVars"
        assert eval_filter["args"]["include_vars"] == "omni_heads"
        # The train chain is still present and distinct.
        train_block = next(b for b in modified_config["task_data_filters"] if "train" in b["tasks"])
        assert train_block["filters"][-1]["path"] == "flip.nvflare.components.TrimBroadcastVars"
        _assert_no_duplicate_task_chains(modified_config)

    def test_configure_server_trim_eval_appended_to_existing_validate_block(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """An existing dedicated ``["validate"]`` chain gains TrimEvalBroadcastVars (appended, so it
        runs after any other outgoing data filter) instead of a second validate-covering chain, which
        NVFLARE would reject at parse time."""
        config = self._minimal_server_config()
        config["task_data_filters"] = [{"tasks": ["validate"], "filters": [{"id": "existing", "path": "some.Filter"}]}]
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            aggregate_only_regex="omni_heads",
        )

        modified_config = mock_write_config.call_args[0][0]
        validate_block = next(b for b in modified_config["task_data_filters"] if b["tasks"] == ["validate"])
        assert validate_block["filters"][0]["id"] == "existing"
        assert validate_block["filters"][-1]["path"] == "flip.nvflare.components.TrimEvalBroadcastVars"
        _assert_no_duplicate_task_chains(modified_config)

    def test_configure_server_trim_eval_rejects_multi_task_validate_chain(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """TrimEvalBroadcastVars trims unconditionally (no round gate), so it must not be appended
        into a chain that also covers other tasks — it would e.g. trim the round-0 train broadcast
        and destroy the client's backbone cache → clear configure-time error."""
        config = self._minimal_server_config()
        config["task_data_filters"] = [{"tasks": ["validate", "other_task"], "filters": []}]
        mock_read_config.return_value = config

        with pytest.raises(ValueError, match=r"chain over \['validate', 'other_task'\]"):
            configure_server(
                job_dir=MOCK_JOB_APP_DIR,
                app_name=MOCK_APP_NAME,
                global_rounds=3,
                trusts=MOCK_APP_CLIENTS,
                ignore_result_error=True,
                aggregator="agg",
                aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
                aggregate_only_regex="omni_heads",
            )

    def test_configure_server_no_regex_leaves_data_filters_untouched(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """No aggregate_only_regex → neither TrimBroadcastVars nor TrimEvalBroadcastVars injected
        (normal full-model broadcast) — this is the ``evaluation``/multimodel path, which must never
        head-only-trim the validate broadcast (clients don't hold the arbitrary server checkpoints)."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        modified_config = mock_write_config.call_args[0][0]
        injected_paths = {
            f.get("path")
            for b in modified_config.get("task_data_filters", [])
            for f in b.get("filters", [])
        }
        assert "flip.nvflare.components.TrimBroadcastVars" not in injected_paths
        assert "flip.nvflare.components.TrimEvalBroadcastVars" not in injected_paths

    _SELECTOR_PATH = "nvflare.app_common.widgets.intime_model_selector.IntimeModelSelector"

    def test_configure_server_injects_intime_model_selector(self, mock_isfile, mock_read_config, mock_write_config):
        """With best_model_metric set, a stock IntimeModelSelector component keyed on that metric is
        appended so the best global model is saved alongside the final one (FLIP#673). This is the
        production mirror of FlipFedAvgRecipe(best_model_metric=...) — the fl-server wires the selector
        at job-assembly from the app's config.json, the same way AGGREGATE_ONLY_REGEX wires filters."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            best_model_metric="VAL_DICE",
        )

        modified_config = mock_write_config.call_args[0][0]
        selector = next(c for c in modified_config["components"] if c.get("path") == self._SELECTOR_PATH)
        assert selector["args"]["key_metric"] == "VAL_DICE"
        assert selector["args"]["negate_key_metric"] is False

    def test_configure_server_intime_selector_negates_for_minimize(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """Loss-like metrics (lower is better) inject with negate_key_metric=True."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            best_model_metric="VAL_LOSS",
            best_model_metric_minimize=True,
        )

        modified_config = mock_write_config.call_args[0][0]
        selector = next(c for c in modified_config["components"] if c.get("path") == self._SELECTOR_PATH)
        assert selector["args"]["negate_key_metric"] is True

    def test_configure_server_no_best_metric_leaves_components_untouched(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """No best_model_metric → no selector injected, so no best checkpoint is ever saved
        (unchanged behaviour for every job that doesn't opt in)."""
        mock_read_config.return_value = self._minimal_server_config()

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        modified_config = mock_write_config.call_args[0][0]
        assert not any(c.get("path") == self._SELECTOR_PATH for c in modified_config["components"])

    def test_configure_server_intime_selector_idempotent(self, mock_isfile, mock_read_config, mock_write_config):
        """A template that already carries an IntimeModelSelector (e.g. a recipe-exported job built
        with best_model_metric) must not get a second one — two selectors would both drive
        best-model saves off different accumulators."""
        config = self._minimal_server_config()
        config["components"].append(
            {"id": "model_selector", "path": self._SELECTOR_PATH, "args": {"key_metric": "VAL_DICE"}}
        )
        mock_read_config.return_value = config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=3,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=True,
            aggregator="agg",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
            best_model_metric="VAL_DICE",
        )

        modified_config = mock_write_config.call_args[0][0]
        selectors = [c for c in modified_config["components"] if c.get("path") == self._SELECTOR_PATH]
        assert len(selectors) == 1

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

    def test_configure_server_evaluation_recipe_config(
        self, mock_isfile, mock_read_config, mock_write_config
    ):
        """The recipe-generated ``evaluation`` server config has no aggregator component and
        workflows without ``participating_clients``/``ignore_result_error`` (GlobalModelEval discovers
        clients at runtime). The generic assembly must still set model_id/global_rounds/min_clients and
        leave those args untouched — i.e. a new recipe job type needs no prepare_config changes."""
        mock_server_config = {
            "format_version": 2,
            "workflows": [
                {"id": "controller", "path": "...InitEvaluation", "args": {"model_id": None}},
                {
                    "id": "controller1",
                    "path": "...GlobalModelEval",
                    "args": {"validation_timeout": 12000, "model_locator_id": "model_locator"},
                },
                {"id": "controller2", "path": "...BroadcastTask", "args": {"task_name": "post_validation"}},
            ],
            "task_result_filters": [
                {"tasks": ["validate", "post_validation"], "filters": [{"path": "...ClientExceptionReporter"}]}
            ],
            "components": [
                {"id": "persistor", "path": "...PTFileModelPersistor", "args": {}},
                {"id": "model_locator", "path": "...EvaluationModelLocator", "args": {}},
                {"id": "json_generator", "path": "...EvaluationJsonGenerator", "args": {}},
            ],
        }
        mock_read_config.return_value = mock_server_config

        configure_server(
            job_dir=MOCK_JOB_APP_DIR,
            app_name=MOCK_APP_NAME,
            global_rounds=1,
            trusts=MOCK_APP_CLIENTS,
            ignore_result_error=False,
            aggregator="new_aggregator",
            aggregation_weights=MOCK_AGGREGATION_WEIGHTS,
        )

        args, _ = mock_write_config.call_args
        modified = args[0]
        assert modified["model_id"] == MOCK_APP_NAME
        assert modified["min_clients"] == 2
        # No participating_clients/ignore_result_error keys are injected (not present in the recipe args),
        # and no component is treated as an aggregator.
        assert "participating_clients" not in modified["workflows"][1]["args"]
        assert all("aggregation_weights" not in c.get("args", {}) for c in modified["components"])


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
        assert meta_config["custom_props"] == {"model_id": MOCK_APP_NAME}

    def test_configure_meta_with_gpus(self, mock_write_config, mock_get_settings):
        # Settings mock
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 2
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        # Must be "num_of_gpus" (NVFLARE GPUResourceManager's num_gpu_key) — "num_gpus" makes the
        # manager raise and the job fail to schedule.
        expected_resource_spec_per_client = {"num_of_gpus": 2, "mem_per_gpu_in_GiB": 16}

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
        assert meta_config["custom_props"] == {"model_id": MOCK_APP_NAME}

    def test_configure_meta_publishes_model_id_in_custom_props(self, mock_write_config, mock_get_settings):
        # custom_props.model_id is the lazy-resolution channel for recipe-built job types
        # (e.g. standard) whose component configs carry no model_id. app_name is the model_id.
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_NUM_GPUS = 0
        mock_get_settings.return_value.JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB = 16

        configure_meta(MOCK_JOB_APP_DIR, MOCK_APP_NAME, MOCK_APP_CLIENTS)

        meta_config = mock_write_config.call_args.args[0]
        assert meta_config["custom_props"]["model_id"] == MOCK_APP_NAME


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
