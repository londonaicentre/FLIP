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

from pathlib import Path
from typing import List

from nvflare.app_common.app_constant import EnvironmentKey

from fl_api.config import get_settings
from fl_api.utils.constants import (
    CONFIG,
    CONFIG_FED_CLIENT,
    CONFIG_FED_SERVER,
    ENVIRONMENT,
    GLOBAL_ROUNDS,
    LOCAL_ROUNDS,
    META,
)
from fl_api.utils.io_utils import read_config, write_config
from fl_api.utils.logger import logger
from fl_api.utils.schemas import AggregationWeights, FLAggregators, IOverridableConfig, TrainingRound


# TODO Validation of config.json could be used to avoid some of the logic implemented here.
def configure_config(
    job_dir: Path,
    global_rounds_override: int = get_settings().JOB_CONFIG_DEFAULT_GLOBAL_ROUNDS,
    local_rounds_override: int = get_settings().JOB_CONFIG_DEFAULT_LOCAL_ROUNDS,
) -> Path:
    """
    Configures the config.json file for the job.

    Looks into the config.json file, which should be in the custom folder of the application.

    Args:
        job_dir (Path): path to the job directory.
        global_rounds_override (int): number of global rounds to set if not present
        local_rounds_override (int): number of local rounds to set if not present

    Returns:
        Path: path to the config file that was updated.

    Raises:
        ValueError: if the config.json file does not have the LOCAL_ROUNDS and GLOBAL_ROUNDS keys, or
        sub-versions of these.
    """
    config_json = job_dir / "custom" / CONFIG

    # Load the config.json file.
    user_config = read_config(config_json)

    # Two sets: generative model or normal model.
    # This should be made more generalisable in the future, with perhaps passing to this function what type of training
    # this is and hence, what is the name of the local or global round.

    # Find any keys that START WITH the local rounds keyword, to check if there are any local rounds specified in the
    # config, e.g. LOCAL_ROUNDS, LOCAL_ROUNDS_STAGE1, LOCAL_ROUNDS_STAGE2, etc.
    found_local_round = [i for i in user_config.keys() if i.startswith(LOCAL_ROUNDS)]

    # Create a copy of the user-provided config to update with any missing keys, and then save it back to the same path
    # if any changes are made.
    updated_config = user_config.copy()

    # If no local rounds are found, we override the config with the default local rounds. If there are no global rounds,
    # we override the config with the default global rounds.
    # FIXME What if this is multi-stage and there are no local rounds? Could lead to bugs.
    if len(found_local_round) == 0:
        logger.warning(f"No {LOCAL_ROUNDS} found in config. Overriding with default value = {local_rounds_override}.")
        updated_config[LOCAL_ROUNDS] = local_rounds_override
        logger.debug(
            f"{CONFIG} must have a {LOCAL_ROUNDS} number and a {GLOBAL_ROUNDS} number. Overriding {LOCAL_ROUNDS} with "
            f"default value = {local_rounds_override}."
        )

        if GLOBAL_ROUNDS not in user_config.keys():
            updated_config[GLOBAL_ROUNDS] = global_rounds_override
            logger.debug(
                f"{CONFIG} has encountered no {LOCAL_ROUNDS} and no {GLOBAL_ROUNDS}. Overriding {GLOBAL_ROUNDS} with "
                f"default value = {global_rounds_override}."
            )

    # Overall LOCAL_ROUNDS and GLOBAL_ROUNDS keys
    # If there is exactly 1 local rounds key, we check if there is a global rounds key. If not, we override the config
    # with the default global rounds.
    if len(found_local_round) == 1:
        # This needs to be called LOCAL_ROUNDS, otherwise we can error
        if found_local_round[0] != LOCAL_ROUNDS:
            raise ValueError(
                f"{CONFIG} has encountered 1 local rounds key ({found_local_round[0]}). When only 1 local rounds key "
                f"is present, it must be called {LOCAL_ROUNDS}. Please change the name of the local rounds key to "
                f"{LOCAL_ROUNDS}."
            )

        # FIXME this key could be e.g. LOCAL_ROUNDS_STAGE1, in which case we should check for GLOBAL_ROUNDS_STAGE1, but
        # for now we just check for GLOBAL_ROUNDS?
        # The above should probably be 'if' and not 'elif'
        if GLOBAL_ROUNDS not in user_config.keys():
            updated_config[GLOBAL_ROUNDS] = global_rounds_override
            logger.debug(
                f"{CONFIG} has encountered {LOCAL_ROUNDS} but not {GLOBAL_ROUNDS}. Overriding {GLOBAL_ROUNDS}. with "
                f"default value = {global_rounds_override}."
            )

    # Multi-stage
    # If there are more than 1 local rounds keys, we check that for each of them there is a corresponding global rounds
    # key. If not, we raise an error as we don't know how to override the config in this case, since we don't know which
    # global rounds key corresponds to which local rounds key.
    if len(found_local_round) > 1:
        for local_key in found_local_round:
            if local_key == LOCAL_ROUNDS:
                # Skip as we have already checked this case above
                continue
            # If there are multiple local rounds keys, there must be a global rounds key that corresponds to each of
            # them, with the same sub-key. For example, if there is a local rounds key called "local_rounds_stage1",
            # there must be a global rounds key called "global_rounds_stage1". If this is not the case, we raise an
            # error as we don't know how to override the config in this case, since we don't know which global rounds
            # key corresponds to which local rounds key.
            stage_keyword = local_key.split(f"{LOCAL_ROUNDS}_")[1]
            if f"{GLOBAL_ROUNDS}_{stage_keyword}" not in user_config.keys():
                raise ValueError(
                    f"{CONFIG} has encountered {LOCAL_ROUNDS} for {stage_keyword=} but not the "
                    f"equivalent {GLOBAL_ROUNDS}. You must provide a global rounds key that corresponds to each "
                    f"local rounds key, with the same sub-key."
                )

    # Compare the user-provided config with the updated config, and if there are any differences, save the updated
    # config back to the same path.
    if user_config != updated_config:
        write_config(updated_config, config_json)
        logger.info(f"Updated file {config_json} with default values.")

    return config_json


def configure_client(job_dir: Path, app_name: str, project_id: str, cohort_query: str) -> Path:
    """
    Populates config_fed_client.json, necessary to modulate the client controllers in NVFLARE jobs, with the project_id
    and cohort_query.

    Args:
        job_dir (Path): job directory, where the config and custom folders will be
        app_name (str): name of the job (corresponds to model_id)
        project_id (str): unique project_id identifier
        cohort_query (str): cohort query identifying the project (SQL query used to obtain the data)

    Returns:
        Path: path to the client config file that was updated.

    Raises:
        FileNotFoundError: if config_fed_client.json is not there, FileNotFound error arises.
    """
    config_file = job_dir / "config" / CONFIG_FED_CLIENT

    if not config_file.is_file():
        err_msg = f"No {CONFIG_FED_CLIENT} found in app '{app_name}'"
        raise FileNotFoundError(err_msg)

    config = read_config(config_file)

    # The client config must have the project_id and cohort_query to be able to run the job.
    config["project_id"] = project_id
    config["query"] = cohort_query

    logger.debug(f"Client config to be written: {config}")

    write_config(config, config_file)

    logger.info(f"Successfully updated {CONFIG_FED_CLIENT} for app '{app_name}'")
    return config_file


def configure_server(
    job_dir: Path,
    app_name: str,
    global_rounds: int,
    trusts: List[str],
    ignore_result_error: bool,
    aggregator: str,
    aggregation_weights: dict,
) -> Path:
    """
    Configures the server config file. Making sure the app name, global rounds, and other variables are set correctly.

    Args:
        job_dir (Path): directory where the job is stored (includes the application name)
        app_name (str): application name
        global_rounds (int): number of global rounds
        trusts (List[str]): list of trusts that will be part of the job
        ignore_result_error (bool): whether to ignore result errors
        aggregator (str): name of the aggregator to be used
        aggregation_weights (dict): aggregation weights to be used in the job (per trust)

    Returns:
        Path: path to the server config file that was updated.

    Raises:
        FileNotFoundError: if the config file does not exist.

    .. code-block:: json

        {
            "model_id": "...",
            "global_rounds": 10,
            "min_clients": 2,
            "workflows": [
                {
                    "id": "scatter_and_gather",
                    "args": {
                        "participating_clients": [...],
                        "ignore_result_error": false
                    }
                }
            ],
            "components": [
                {
                    "id": "aggregator",
                    "name": "FedAvg",
                    "args": {
                        "aggregation_weights": {...}
                    }
                }
            ]
        }

    """
    config_file = job_dir / "config" / CONFIG_FED_SERVER

    if not config_file.is_file():
        err_msg = f"No {CONFIG_FED_SERVER} found in app '{app_name}'"
        raise FileNotFoundError(err_msg)

    config = read_config(config_file)

    # Add server configuration variables that are needed to run the job
    config["model_id"] = app_name
    config["global_rounds"] = global_rounds
    config["min_clients"] = len(trusts)

    for workflow in config["workflows"]:
        if "args" in workflow and "participating_clients" in workflow["args"]:
            workflow["args"]["participating_clients"] = trusts
        if "args" in workflow and "ignore_result_error" in workflow["args"]:
            workflow["args"]["ignore_result_error"] = ignore_result_error

    for component in config["components"]:
        if ("name" in component and "aggregator" in component["name"]) or (
            "id" in component and "aggregator" in component["id"]
        ):
            component["name"] = aggregator  # override the aggregator if specified in the config, otherwise use default
            component["args"]["aggregation_weights"] = aggregation_weights  # override the aggregation weights

    write_config(config, config_file)

    logger.info(f"Successfully updated {CONFIG_FED_SERVER} for app '{app_name}'")
    return config_file


def configure_meta(job_dir: Path, app_name: str, trusts: List[str]) -> Path:
    """
    Creates a meta.json file, which is part of the NVFLARE application.

    Args:
        job_dir (Path): job directory
        app_name (str): name of this specific application, under which the config and custom folders will be saved.
        trusts (List[str]): list of trusts that are part of this training (site names)

    Returns:
        Path: path to the meta file that was created.
    """
    # Resources required to perform this job at each site
    # See https://nvflare.readthedocs.io/en/2.4/real_world_fl/job.html#job
    # TODO Currently this is set from the global config, but we should allow per-job overrides in the future.
    # See https://github.com/londonaicentre/FLIP/issues/70
    num_gpus = get_settings().JOB_RESOURCE_SPEC_NUM_GPUS
    mem_per_gpu_in_gib = get_settings().JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB
    print(f"Job configured to use {num_gpus=} with {mem_per_gpu_in_gib=}.")

    # Resource spec should be omitted by default so that 0 gpu jobs get picked up.
    # Resource spec is only needed in envs with configured gpus
    # e.g.
    # {
    #     "resource_spec": {
    #         "Trust_1": { "num_of_gpus": 1, "mem_per_gpu_in_GiB": 1 },
    #         "Trust_2": { "num_of_gpus": 1, "mem_per_gpu_in_GiB": 1 }
    #     }
    # }
    if num_gpus > 0:
        resource_spec = {trust: {"num_gpus": num_gpus, "mem_per_gpu_in_GiB": mem_per_gpu_in_gib} for trust in trusts}
    else:
        resource_spec = {}

    # Create the meta.json file
    meta_config = {
        "name": app_name,
        "resource_spec": resource_spec,
        "deploy_map": {"app": ["server"] + trusts},
        "min_clients": len(trusts),
        "mandatory_clients": trusts,
    }
    logger.debug(f"Meta config to be written: {meta_config}")

    meta_path = job_dir / META

    write_config(meta_config, meta_path)

    logger.info(f"Successfully wrote {META} to {meta_path}")
    return meta_path


def configure_environment(job_dir: Path) -> Path:
    """
    Configures the environment.json file, which is part of the NVFLARE application. This file is used to set environment
    variables for the job.

    Inside the config folder, you can have an optional environment.json which defines the EnvironmentKey variables.
    In this case, we define the CHECKPOINT_DIR as "model".

    Args:
        job_dir (Path): job directory (including name of the federated learning app).

    Returns:
        Path: path to the environment file that was created.
    """
    env_config = {EnvironmentKey.CHECKPOINT_DIR: "model"}
    logger.debug(f"Environment config to be written: {env_config}")

    env_path = job_dir / "config" / ENVIRONMENT

    write_config(env_config, env_path)

    logger.info(f"Successfully wrote {ENVIRONMENT} to {env_path}")
    return env_path


def validate_config(config: dict) -> IOverridableConfig:
    """
    Validate the provided configuration dictionary.

    Args:
        config (IOverridableConfig): The configuration dictionary to validate.

    Returns:
        IOverridableConfig: The validated configuration dictionary.

    Raises:
        ValueError: If any of the checks fail, a ValueError is raised with an appropriate message.
    """
    validated = IOverridableConfig()

    def is_valid(value):
        return isinstance(value, (int, float)) and TrainingRound.MIN <= value <= TrainingRound.MAX

    if not isinstance(config, dict):
        raise ValueError("Provided config is not a valid dictionary")

    if is_valid(config.get("LOCAL_ROUNDS")):
        validated.LOCAL_ROUNDS = config["LOCAL_ROUNDS"]

    if is_valid(config.get("GLOBAL_ROUNDS")):
        validated.GLOBAL_ROUNDS = config["GLOBAL_ROUNDS"]

    if isinstance(config.get("IGNORE_RESULT_ERROR"), bool):
        validated.IGNORE_RESULT_ERROR = config["IGNORE_RESULT_ERROR"]

    agg = config.get("AGGREGATOR")
    if agg:
        if agg in FLAggregators:
            validated.AGGREGATOR = agg
        else:
            raise ValueError(f"Unknown aggregator: {agg}")

    weights = config.get("AGGREGATION_WEIGHTS")
    if weights:
        if not isinstance(weights, dict):
            raise ValueError("AGGREGATION_WEIGHTS must be a dictionary")

        for key, val in weights.items():
            logger.info(f"Validating aggregation weight: {key} -> {val}")
            if not (
                isinstance(val, (int, float))
                and AggregationWeights.MinimumAggregationWeight <= val <= AggregationWeights.MaximumAggregationWeight
            ):
                raise ValueError(f"Invalid weight: {val}")

        validated.AGGREGATION_WEIGHTS = weights

    return validated
