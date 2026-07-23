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


def _inject_keep_only_vars_filter(config: dict, include_regex: str) -> None:
    """Prepend a KeepOnlyVars filter to the ``train`` task_result_filters (client-side).

    Shrinks the per-round client update to only the parameters matching ``include_regex`` (the
    trainable ones), so a frozen-backbone fine-tune sends just its head instead of the full model
    (FLIP#684). Prepended so it runs BEFORE any existing result filter (e.g. PercentilePrivacy),
    which should see only the retained head — otherwise the percentile cutoff is skewed by the
    ~0 diffs of the frozen backbone. Mutates ``config`` in place.
    """
    keep_filter = {
        "id": "keep_only_trainable_vars",
        "path": "flip.nvflare.components.KeepOnlyVars",
        "args": {"include_vars": include_regex},
    }
    filters_blocks = config.setdefault("task_result_filters", [])
    for block in filters_blocks:
        if "train" in block.get("tasks", []):
            block.setdefault("filters", []).insert(0, keep_filter)
            return
    # No existing train filter block — add one.
    filters_blocks.append({"tasks": ["train"], "filters": [keep_filter]})


def _inject_reconstruct_full_model_filter(config: dict) -> None:
    """Prepend a ReconstructFullModelForEval filter covering BOTH ``train`` and ``validate`` (client).

    The client-side half of the head-only broadcast: it rebuilds the full global model from the
    trimmed (head-only) broadcast the server sends after round 0 (training, see
    ``_inject_trim_broadcast_filter`` / TrimBroadcastVars) and for cross-site validation (see
    ``_inject_trim_eval_broadcast_filter`` / TrimEvalBroadcastVars), so the client trainer and
    validator keep receiving a full state dict and need no change.

    Wired as a SINGLE filter chain over ``["train", "validate"]`` so ONE component instance handles
    both tasks: the frozen backbone the client reconstructs against is the round-0 broadcast cached
    during training, and NVFLARE builds a fresh filter instance per chain occurrence — so that cache
    is only visible during validation when the same instance serves both tasks. Prepended so it runs
    before any other incoming task_data_filter. Injected alongside the KeepOnlyVars result filter —
    together the matched client-side ends of the frozen-backbone round-trip. Mutates ``config`` in
    place.

    NVFLARE rejects a job in which the same task appears in more than one task_data_filters chain
    (``FedJsonConfigurator._build_filter_table`` raises "multiple data filter chains defined for
    task ..."), so any pre-existing chain covering ``train`` or ``validate`` is FOLDED INTO the
    injected chain rather than left alongside it: its filters keep their relative order after the
    reconstruct filter (which must see the broadcast first), and any ``ReconstructFullModel*`` filter
    already wired (e.g. by a ``FlipFedAvgRecipe(aggregate_only_regex=...)`` export) is dropped as
    superseded — the deploy-time instance must be the ONE holding the round-0 cache across both
    tasks. A pre-existing chain that also covers OTHER tasks cannot be folded without silently
    changing which tasks its filters run on, so that raises instead.

    Raises:
        ValueError: if an existing task_data_filters chain covers ``train`` or ``validate`` together
            with other tasks — unmergeable, and NVFLARE would reject the overlap at job parse time.
    """
    reconstruct_filter = {
        "id": "reconstruct_full_model",
        "path": "flip.nvflare.components.ReconstructFullModelForEval",
        "args": {},
    }
    covered_tasks = {"train", "validate"}
    filters_blocks = config.setdefault("task_data_filters", [])
    to_merge = [block for block in filters_blocks if covered_tasks & set(block.get("tasks", []))]
    unmergeable = [block for block in to_merge if set(block.get("tasks", [])) - covered_tasks]
    if unmergeable:
        raise ValueError(
            "AGGREGATE_ONLY_REGEX requires a single client task_data_filters chain over ['train', 'validate'], "
            f"but the app config has a chain over {unmergeable[0].get('tasks')} — folding it in would apply its "
            "filters to train/validate only, and NVFLARE rejects a task covered by two chains. Restrict the "
            "chain to train/validate tasks or unset AGGREGATE_ONLY_REGEX."
        )
    merged_filters = [reconstruct_filter]
    for block in to_merge:
        for existing in block.get("filters", []):
            if str(existing.get("path", "")).startswith("flip.nvflare.components.ReconstructFullModel"):
                # Already wired (e.g. recipe-baked); superseded by the instance injected above — keeping
                # both would double-reconstruct and NVFLARE would build a second, cache-less instance.
                logger.info(f"Dropping pre-existing {existing.get('path')} filter superseded by injected chain")
                continue
            merged_filters.append(existing)
        if set(block["tasks"]) != covered_tasks and len(block.get("filters", [])) > 0:
            logger.warning(
                f"Folding task_data_filters chain over {block['tasks']} into the injected "
                "['train', 'validate'] chain; its filters now run on both tasks"
            )
    remaining_blocks = [block for block in filters_blocks if not any(block is merged for merged in to_merge)]
    config["task_data_filters"] = [{"tasks": ["train", "validate"], "filters": merged_filters}, *remaining_blocks]


def _inject_trim_broadcast_filter(config: dict, include_regex: str) -> None:
    """Append a TrimBroadcastVars filter to the ``train`` task_data_filters (server-side).

    The server-side half of the head-only broadcast: after round 0 it trims the outgoing global-model
    broadcast down to only the trainable params matching ``include_regex``, so the frozen backbone
    (~759 MiB) ships once at round 0 instead of every round. Clients rebuild the full model via
    ReconstructFullModel. Mutates ``config`` in place.
    """
    trim_filter = {
        "id": "trim_broadcast_to_trainable",
        "path": "flip.nvflare.components.TrimBroadcastVars",
        "args": {"include_vars": include_regex},
    }
    filters_blocks = config.setdefault("task_data_filters", [])
    for block in filters_blocks:
        if "train" in block.get("tasks", []):
            block.setdefault("filters", []).append(trim_filter)
            return
    filters_blocks.append({"tasks": ["train"], "filters": [trim_filter]})


def _inject_trim_eval_broadcast_filter(config: dict, include_regex: str) -> None:
    """Append a TrimEvalBroadcastVars filter to the ``validate`` task_data_filters (server-side).

    The server-side half of head-only cross-site validation: it trims the ``validate`` broadcast
    (the full aggregated global model that ``GlobalModelEval`` sends to each client for scoring) down
    to only the trainable params matching ``include_regex``, so the frozen ~759 MiB backbone is not
    re-shipped for evaluation — the client rebuilds the full model via ReconstructFullModelForEval
    from the backbone it cached at training round 0. Kept as its own ``["validate"]`` chain (distinct
    from the ``["train"]`` TrimBroadcastVars chain); both filters are stateless, so a separate
    server-side instance per task is fine. Mutates ``config`` in place.

    Detection is by membership (any chain covering ``validate``), not exact match — NVFLARE rejects a
    task covered by two chains, so appending a second validate-covering chain would fail the job at
    parse time. But unlike the round-gated TrimBroadcastVars (which no-ops on tasks without a round
    header), this filter trims UNCONDITIONALLY, so it must not be appended into a chain that also
    covers other tasks (it would e.g. trim the round-0 ``train`` broadcast and destroy the client's
    backbone cache) — that raises instead.

    Raises:
        ValueError: if an existing task_data_filters chain covers ``validate`` together with other
            tasks — the unconditional trim cannot be scoped to ``validate`` within a shared chain.
    """
    trim_filter = {
        "id": "trim_eval_broadcast_to_trainable",
        "path": "flip.nvflare.components.TrimEvalBroadcastVars",
        "args": {"include_vars": include_regex},
    }
    filters_blocks = config.setdefault("task_data_filters", [])
    for block in filters_blocks:
        tasks = block.get("tasks", [])
        if "validate" not in tasks:
            continue
        if tasks != ["validate"]:
            raise ValueError(
                "AGGREGATE_ONLY_REGEX requires a dedicated ['validate'] server task_data_filters chain, but the "
                f"app config has a chain over {tasks} — TrimEvalBroadcastVars trims unconditionally, so it cannot "
                "share a chain with other tasks, and NVFLARE rejects a task covered by two chains. Split validate "
                "into its own chain or unset AGGREGATE_ONLY_REGEX."
            )
        block.setdefault("filters", []).append(trim_filter)
        return
    filters_blocks.append({"tasks": ["validate"], "filters": [trim_filter]})


def _inject_intime_model_selector(config: dict, key_metric: str, minimize: bool) -> None:
    """Append a stock ``IntimeModelSelector`` component keyed on ``key_metric`` (server-side, FLIP#673).

    The production mirror of ``FlipFedAvgRecipe(best_model_metric=...)``: the selector averages the
    client-reported validation metric each round and fires ``GLOBAL_BEST_MODEL_AVAILABLE`` on
    improvement, which the persistor answers by saving ``best_FL_global_model.pt`` alongside the final
    model. The FLIP ``ScatterAndGather`` controller (a thin subclass of stock) already fires the round
    events the selector listens on, so only the component needs adding. The client trainer must report
    ``key_metric`` on its returned ``FLModel`` (evaluated on the received global model) for selection
    to fire; without it the selector stays dormant and no best model is saved.

    Idempotent: a template that already carries an ``IntimeModelSelector`` (e.g. a recipe export built
    with ``best_model_metric``) is left untouched — two selectors would drive best-model saves off
    separate accumulators. Mutates ``config`` in place.
    """
    selector_path = "nvflare.app_common.widgets.intime_model_selector.IntimeModelSelector"
    components = config.setdefault("components", [])
    if any(component.get("path") == selector_path for component in components):
        logger.info("IntimeModelSelector already present in server config; not injecting a second one")
        return
    components.append(
        {
            "id": "model_selector",
            "path": selector_path,
            "args": {"key_metric": key_metric, "negate_key_metric": minimize},
        }
    )


def configure_client(
    job_dir: Path,
    app_name: str,
    project_id: str,
    cohort_query: str,
    aggregate_only_regex: str | None = None,
) -> Path:
    """
    Populates config_fed_client.json, necessary to modulate the client controllers in NVFLARE jobs, with the project_id
    and cohort_query.

    Args:
        job_dir (Path): job directory, where the config and custom folders will be
        app_name (str): name of the job (corresponds to model_id)
        project_id (str): unique project_id identifier
        cohort_query (str): cohort query identifying the project (SQL query used to obtain the data)
        aggregate_only_regex (str | None): when set, inject a KeepOnlyVars ``train`` result filter so
            only matching (trainable) params are sent per round, plus a ReconstructFullModelForEval
            data filter over ``train``+``validate`` that rebuilds the full model client-side for both
            training and cross-site validation (frozen-backbone head-only, FLIP#684 / #730).

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

    if aggregate_only_regex:
        _inject_keep_only_vars_filter(config, aggregate_only_regex)
        _inject_reconstruct_full_model_filter(config)
        logger.info(
            f"Injected KeepOnlyVars result filter + ReconstructFullModel data filter "
            f"(include_vars={aggregate_only_regex!r}) for app '{app_name}'"
        )

    logger.debug(f"Client config to be written: {config}")

    write_config(config, config_file)

    logger.info(f"Successfully updated {CONFIG_FED_CLIENT} for app '{app_name}'")
    return config_file


def configure_server(
    job_dir: Path,
    app_name: str,
    global_rounds: int,
    trusts: list[str],
    ignore_result_error: bool,
    aggregator: str,
    aggregation_weights: dict,
    aggregate_only_regex: str | None = None,
    best_model_metric: str | None = None,
    best_model_metric_minimize: bool = False,
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
        aggregate_only_regex (str | None): when set, inject a TrimBroadcastVars ``train`` data filter so
            only the trainable params (matching the regex) are broadcast per round after round 0, plus a
            TrimEvalBroadcastVars ``validate`` data filter so post-training cross-site validation also
            broadcasts only the head (frozen-backbone head-only downstream — the server-side mirror of
            the client KeepOnlyVars / ReconstructFullModelForEval; FLIP#684 / #730).
        best_model_metric (str | None): when set, inject a stock IntimeModelSelector keyed on this
            validation metric so the best global model is saved alongside the final one (FLIP#673).
        best_model_metric_minimize (bool): when True, negate the selector's key metric for loss-like
            metrics where lower is better. Defaults to False (higher is better).

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
        # Recipe-generated templates (standard_client_api) carry LITERAL num_rounds/min_clients
        # baked in by FedJob serialisation instead of the executor templates' "{global_rounds}" /
        # "{min_clients}" placeholders, so the top-level keys set above never reach the workflow.
        # Override them directly or every deployed client_api job silently runs the template
        # defaults: 3 rounds no matter what GLOBAL_ROUNDS asks for, and min_clients=1 — which lets
        # ScatterAndGather close every round on the first (fastest) trust's update, silently
        # dropping all slower trusts' contributions (observed live: a 2-trust 20-round job
        # aggregated 20/20 updates from one trust and 0 from the other).
        if "args" in workflow and "num_rounds" in workflow["args"] and not isinstance(
            workflow["args"]["num_rounds"], str
        ):
            workflow["args"]["num_rounds"] = global_rounds
        if "args" in workflow and "min_clients" in workflow["args"] and not isinstance(
            workflow["args"]["min_clients"], str
        ):
            workflow["args"]["min_clients"] = len(trusts)

    for component in config["components"]:
        if ("name" in component and "aggregator" in component["name"]) or (
            "id" in component and "aggregator" in component["id"]
        ):
            component["name"] = aggregator  # override the aggregator if specified in the config, otherwise use default
            component["args"]["aggregation_weights"] = aggregation_weights  # override the aggregation weights

    if aggregate_only_regex:
        _inject_trim_broadcast_filter(config, aggregate_only_regex)
        _inject_trim_eval_broadcast_filter(config, aggregate_only_regex)
        logger.info(
            f"Injected TrimBroadcastVars (train) + TrimEvalBroadcastVars (validate) data filters "
            f"(include_vars={aggregate_only_regex!r}) for app '{app_name}'"
        )

    if best_model_metric:
        _inject_intime_model_selector(config, best_model_metric, best_model_metric_minimize)
        logger.info(
            f"Injected IntimeModelSelector (key_metric={best_model_metric!r}, "
            f"negate_key_metric={best_model_metric_minimize}) for app '{app_name}'"
        )

    write_config(config, config_file)

    logger.info(f"Successfully updated {CONFIG_FED_SERVER} for app '{app_name}'")
    return config_file


def configure_meta(job_dir: Path, app_name: str, trusts: list[str]) -> Path:
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
        # NVFLARE's GPUResourceManager reads the requirement via num_gpu_key="num_of_gpus"
        # (app_common/resource_managers/gpu_resource_manager.py) and RAISES if it's absent — so
        # the key must be "num_of_gpus", not "num_gpus", or the job fails to schedule.
        resource_spec = {
            trust: {"num_of_gpus": num_gpus, "mem_per_gpu_in_GiB": mem_per_gpu_in_gib} for trust in trusts
        }
    else:
        resource_spec = {}

    # Create the meta.json file.
    #
    # ``custom_props`` is NVFLARE's officially-sanctioned channel for job-scoped metadata
    # (``JobMetaKey.CUSTOM_PROPS``; surfaced to components via ``FLContextKey.JOB_META``). We
    # publish the FLIP ``model_id`` (== ``app_name``) here so recipe-built job types whose
    # component configs carry no ``model_id`` (e.g. ``standard_client_api``, built before the UUID is
    # known) can resolve it lazily at runtime via ``flip.nvflare.runtime.get_flip_model_id``.
    # Legacy job types still receive ``model_id`` through their component args and ignore this
    # key, so populating it unconditionally is safe and keeps both paths consistent.
    meta_config = {
        "name": app_name,
        "resource_spec": resource_spec,
        "deploy_map": {"app": ["server"] + trusts},
        "min_clients": len(trusts),
        "mandatory_clients": trusts,
        "custom_props": {"model_id": app_name},
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

    def is_valid(value: object) -> bool:
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

    regex = config.get("AGGREGATE_ONLY_REGEX")
    if regex:
        if not isinstance(regex, str):
            raise ValueError("AGGREGATE_ONLY_REGEX must be a string regex")
        try:
            re.compile(regex)
        except re.error as exc:
            raise ValueError(f"AGGREGATE_ONLY_REGEX is not a valid regex: {exc}") from exc
        validated.AGGREGATE_ONLY_REGEX = regex

    best_model_metric = config.get("BEST_MODEL_METRIC")
    if best_model_metric is not None:
        if not isinstance(best_model_metric, str) or not best_model_metric.strip():
            raise ValueError("BEST_MODEL_METRIC must be a non-empty string metric label")
        validated.BEST_MODEL_METRIC = best_model_metric

        minimize = config.get("BEST_MODEL_METRIC_MINIMIZE", False)
        # Guard bool before int: in Python ``isinstance(True, int)`` is True, but we want to reject
        # a stray 1/0 here so the config stays explicit.
        if not isinstance(minimize, bool):
            raise ValueError("BEST_MODEL_METRIC_MINIMIZE must be a boolean")
        validated.BEST_MODEL_METRIC_MINIMIZE = minimize
    elif "BEST_MODEL_METRIC_MINIMIZE" in config:
        # BEST_MODEL_METRIC_MINIMIZE only has meaning alongside a selector metric — silently
        # accepting it without BEST_MODEL_METRIC would let a user believe it took effect.
        raise ValueError("BEST_MODEL_METRIC_MINIMIZE requires BEST_MODEL_METRIC to also be set")

    return validated
