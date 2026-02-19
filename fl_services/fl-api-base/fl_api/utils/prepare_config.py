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
import os
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
from fl_api.utils.logger import logger


def configure_config(job_dir: str, global_rounds_override: int = 1, local_rounds_override: int = 1):
    """Looks into the config.json file, which should be in the custom folder of the application.

    Args:
        job_dir (str): path to the job directory.
        global_rounds_override (int): number of global rounds to set if not present [default=1]
        local_rounds_override (int): number of local rounds to set if not present [default=1]

    Raises:
        ValueError: if the config.json file does not have the LOCAL_ROUNDS and GLOBAL_ROUNDS keys, or
        sub-versions of these.

    Returns:
        None
    """
    config_json = os.path.join(job_dir, "custom", CONFIG)

    # If no config.json is uploaded, this is no issue.
    if not os.path.isfile(config_json):
        logger.warning(f"No {CONFIG} file found in {job_dir}/custom. Skipping configuration.")
        return

    # Load the config.json file.
    with open(config_json, "r") as f:
        config = json.load(f)

    # Two sets: generative model or normal model.
    # This should be made more generalisable in the future, with perhaps passing to this function what type of training
    # this is and hence, what is the name of the local or global round.

    found_local_round = [i for i in config.keys() if LOCAL_ROUNDS in i]

    save_changes = False
    if len(found_local_round) == 0:
        config[LOCAL_ROUNDS] = local_rounds_override
        save_changes = True
        logger.debug(
            f"{CONFIG} has to have a {LOCAL_ROUNDS} number and a {GLOBAL_ROUNDS} number. Overriding {LOCAL_ROUNDS}."
        )

    if LOCAL_ROUNDS in found_local_round and len(found_local_round) == 1:
        if GLOBAL_ROUNDS not in config.keys():
            config[GLOBAL_ROUNDS] = global_rounds_override
            save_changes = True
            logger.debug(
                f"{CONFIG} has encountered {LOCAL_ROUNDS} but not {GLOBAL_ROUNDS}. Overriding {GLOBAL_ROUNDS}."
            )

    elif len(found_local_round) > 1:
        # We look for multi stage local and global rounds
        for local_key in found_local_round:
            if local_key == LOCAL_ROUNDS:
                continue
            stage_keyword = local_key.split(f"{LOCAL_ROUNDS}_")[1]
            if f"{GLOBAL_ROUNDS}_{stage_keyword}" not in config.keys():
                raise ValueError(
                    f"{CONFIG} has encountered {LOCAL_ROUNDS} for stage {stage_keyword} but not the equivalent "
                    f"{GLOBAL_ROUNDS}."
                )

    if save_changes:
        with open(config_json, "w") as f:
            json.dump(config, f, indent=4)

        logger.info(f"Updated {CONFIG} in {job_dir} with {LOCAL_ROUNDS} and {GLOBAL_ROUNDS}.")


def configure_client(job_dir: str, app_name: str, project_id: str, cohort_query: str):
    """
    Populates config_fed_client.json, necessary to modulate the client controllers in nvflare jobs.

    Args:
        job_dir (str): job directory, where the config and custom folders will be
        app_name (str): name of the job (corresponds to model_id)
        project_id (str): unique project_id identifier
        cohort_query (str): cohort query identifying the project (SQL query used to obtain the data)

    Raises:
        FileNotFoundError: if config_fed_client.json is not there, FileNotFound error arises.

    Returns:
        None
    """
    config_file = os.path.join(job_dir, "config", CONFIG_FED_CLIENT)

    if not os.path.isfile(config_file):
        err_msg = f"No {CONFIG_FED_CLIENT} found in app '{app_name}'"
        raise FileNotFoundError(err_msg)

    with open(config_file) as outfile:
        client_config = json.load(outfile)

        client_config["project_id"] = project_id
        client_config["query"] = cohort_query

    logger.debug(f"Client config to be written: {client_config}")

    with open(config_file, "w") as outfile:
        json.dump(client_config, outfile, indent=2)

    logger.info(f"Successfully updated {CONFIG_FED_CLIENT} for app '{app_name}'")


def configure_server(
    job_dir: str,
    app_name: str,
    global_rounds: int,
    trusts: List[str],
    ignore_result_error: bool,
    aggregator: str,
    aggregation_weights: dict,
):
    """Configures the server config file. Making sure the app name, global rounds,
    trusts, and other variables are set correctly.

    Args:
        job_dir (str): directory where the job is stored (includes the application name)
        app_name (str): application name
        global_rounds (int): number of global rounds
        trusts (List[str]): list of trusts that will be part of the job
        ignore_result_error (bool): whether to ignore result errors
        aggregator (str): name of the aggregator to be used
        aggregation_weights (dict): aggregation weights to be used in the job (per trust)

    Raises:
        FileNotFoundError: if the config file does not exist.

    Returns:
        None
    """
    config_file = os.path.join(job_dir, "config", CONFIG_FED_SERVER)

    if not os.path.isfile(config_file):
        err_msg = f"No {CONFIG_FED_SERVER} found in app '{app_name}'"
        raise FileNotFoundError(err_msg)

    with open(config_file) as outfile:
        server_config = json.load(outfile)

        server_config["model_id"] = app_name
        server_config["global_rounds"] = global_rounds
        server_config["min_clients"] = len(trusts)

        for workflow in server_config["workflows"]:
            if "args" in workflow and "participating_clients" in workflow["args"]:
                workflow["args"]["participating_clients"] = trusts
            if "args" in workflow and "ignore_result_error" in workflow["args"]:
                workflow["args"]["ignore_result_error"] = ignore_result_error

        for component in server_config["components"]:
            if (
                "name" in component.keys()
                and "aggregator" in component["name"]
                or "id" in component.keys()
                and "aggregator" in component["id"]
            ):
                component["name"] = aggregator
                component["args"]["aggregation_weights"] = aggregation_weights

    with open(config_file, "w") as outfile:
        json.dump(server_config, outfile, indent=2)

    logger.info(f"Successfully updated {CONFIG_FED_SERVER} for app '{app_name}'")


def configure_meta(job_dir: str, app_name: str, trusts: List[str]):
    """Writes the meta.json file, which is part of the NVFLARE application.

    Args:
        job_dir (str): job directory
        app_name (str): name of this specific application, under which the config and custom folders will be saved.
        trusts (List[str]): list of trusts that are part of this training (site names)
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

    meta_path = os.path.join(job_dir, META)

    with open(meta_path, "w") as outfile:
        json.dump(meta_config, outfile, indent=2)

    logger.info(f"Successfully wrote {META} to {meta_path}")


def configure_environment(job_dir: str):
    """Inside the config folder, you can have an optional environment.json which defines the EnvironmentKey variables.
    In this case, we define the CHECKPOINT_DIR as "model"

    Args:
        job_dir (str): job directory (including name of the federated learning app).
    """

    env_config = {EnvironmentKey.CHECKPOINT_DIR: "model"}
    logger.debug(f"Environment config to be written: {env_config}")

    env_path = os.path.join(job_dir, "config", ENVIRONMENT)

    with open(env_path, "w") as outfile:
        json.dump(env_config, outfile, indent=2)

    logger.info(f"Successfully wrote {ENVIRONMENT} to {env_path}")
