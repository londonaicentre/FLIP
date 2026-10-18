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
import math
import numbers
import os.path
from collections.abc import Mapping
from typing import Any

from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.event_type import EventType
from nvflare.apis.fl_constant import ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_common.app_event_type import AppEventType
from nvflare.app_common.utils.file_utils import resolve_path_under_root
from nvflare.app_common.widgets.validation_json_generator import to_serializable

from flip.constants import FlipMetaKey, PTConstants
from flip.nvflare.components.validation_json_generator import ValidationJsonGenerator


class EvaluationJsonGenerator(ValidationJsonGenerator):
    """Evaluation results generator, recording both the metrics and the failed validate tasks.

    Deduped against FLIP's :class:`ValidationJsonGenerator`: it inherits the manual-dispatch
    mechanism (the no-op ``handle_event`` — ServerEventHandler drives ``handle_evaluation_events``)
    and the numpy-float JSON encoding / path-safe output. It differs in the results directory / file
    name, in tracking per-task failures, and in how results are keyed.

    **Results shape.** Each entry is keyed by data site, then by model name. Which controller drives
    the run decides where the model name comes from:

    - ``GlobalModelEval`` (the ``evaluation`` job type) sends one ``validate`` task per
      (model, client) and sets ``AppConstants.MODEL_OWNER``, so results nest under it. Keying on
      the client alone would let each model's metrics overwrite the previous model's (FLIP#754).
    - The retired legacy ``ModelEval`` controller (deleted with the Executor syntax) sent every
      model in one task and set no ``MODEL_OWNER``; the evaluator returned its own model-keyed
      dict, stored as-is — the no-``MODEL_OWNER`` branch stays for that stored-results shape.

    **Failures.** The eval controller fires ``VALIDATION_RESULT_RECEIVED`` *before* it inspects the
    result's return code, so a failed or aborted task arrives here carrying no DXO. Each such task is
    recorded in ``_eval_failures`` and written to ``PTConstants.EvalFailuresFilename``, and
    :meth:`all_tasks_failed` lets ServerEventHandler fail the run rather than report success on an
    empty results file (FLIP#754).

    **Hub metrics.** :meth:`hub_metrics` flattens each reply's ``metrics`` into per-client metric labels,
    which ServerEventHandler forwards to the Central Hub at END_RUN (FLIP#1526). Results the evaluator
    puts in the reply's ``meta[FlipMetaKey.SUPPLEMENTARY_RESULTS]`` are merged into the results file only.
    """

    def __init__(
        self,
        results_dir: str = PTConstants.EvalDir,
        json_file_name: str = PTConstants.EvalResultsFilename,
        failures_file_name: str = PTConstants.EvalFailuresFilename,
    ) -> None:
        """Initialise the evaluation results generator.

        Args:
            results_dir (str): Name of the results directory. Defaults to ``PTConstants.EvalDir``.
            json_file_name (str): Name of the json file. Defaults to ``PTConstants.EvalResultsFilename``.
            failures_file_name (str): Name of the failed-task json file. Defaults to
                ``PTConstants.EvalFailuresFilename``.
        """
        super().__init__(results_dir=results_dir, json_file_name=json_file_name)
        # Per-client store (not the base's nested self._val_results).
        self._eval_results: dict = {}
        self._eval_failures: list[dict] = []
        self._failures_file_name = failures_file_name
        # Each client's replies keyed by model owner (None without one), for hub_metrics.
        self._replies: dict[str, dict[str | None, Any]] = {}

    def all_tasks_failed(self) -> bool:
        """Whether every validate task this run produced a failure and none produced metrics.

        Returns:
            bool: True when at least one task failed and none succeeded. False for a clean run, for a
                partial failure (there are still results worth uploading), and for a job that ran no
                validate tasks at all — a training job's END_RUN must not be reported as a failure.
        """
        return bool(self._eval_failures) and not self._eval_results

    def _record_failure(self, fl_ctx: FLContext, model_owner: str | None, data_client: str, return_code: str) -> None:
        """Record one failed validate task so it survives into the results bundle."""
        self._eval_failures.append({"model": model_owner, "client": data_client, "return_code": return_code})
        self.log_error(
            fl_ctx,
            f"Evaluation task failed (model={model_owner}, client={data_client}, return_code={return_code}). "
            f"Recorded in {self._failures_file_name}.",
            fire_event=False,
        )

    def _record_result(self, model_owner: str | None, data_client: str, metrics: Any, supplementary: Any) -> None:
        """Store one client's reply, nesting under the model name when the controller supplies one.

        The results file gets ``metrics`` with ``supplementary`` merged in; the hub gets ``metrics`` only.
        """
        results = _merge(metrics, supplementary)
        if model_owner:
            self._eval_results.setdefault(data_client, {})[model_owner] = results
        else:
            self._eval_results[data_client] = results
        self._replies.setdefault(data_client, {})[model_owner] = metrics

    def hub_metrics(self) -> dict[str, dict[str, float]]:
        """Flatten each client's evaluation metrics (not its supplementary results) into the hub's labels.

        Nested keys join with ``/``; the label is upper-cased, as on Flower. When the run evaluated more
        than one model, each label is prefixed with the model name (the ``config.json`` key, without the
        ``SRV_`` prefix ``GlobalModelEval`` adds), the same at every client even where a task failed. A
        reply that already keys its metrics by that model name, as the Ark+ multimodel evaluator does, is
        taken as it is. Where labels repeat, the later model's value wins, so cumulative replies are sent once.

        Only finite numbers are kept: an undefined metric (NaN) is omitted rather than sent as 0, and
        lists, which is where per-sample values would live, are never sent.

        Returns:
            dict[str, dict[str, float]]: ``{client: {label: value}}``.
        """
        owners = {owner for replies in self._replies.values() for owner in replies}
        owners |= {failure["model"] for failure in self._eval_failures}
        multi_model = len(owners - {None}) > 1
        hub: dict[str, dict[str, float]] = {}
        for client, replies in self._replies.items():
            labels: dict[str, float] = {}
            dropped: set[str] = set()
            for owner, reply in replies.items():
                name = owner.removeprefix("SRV_") if owner else None
                self_keyed = isinstance(reply, Mapping) and isinstance(reply.get(name), Mapping)
                prefix = (name,) if name and multi_model and not self_keyed else ()
                _flatten_metrics(reply, prefix, labels, dropped)
            if dropped:
                self.logger.warning(f"Not sent to the hub for {client} (not a finite number): {sorted(dropped)}")
            if labels:
                hub[client] = labels
        return hub

    def _handle_validation_result(self, fl_ctx: FLContext) -> None:
        """Sort one received validate result into either the metrics store or the failure list."""
        data_client = fl_ctx.get_prop(AppConstants.DATA_CLIENT, None)
        model_owner = fl_ctx.get_prop(AppConstants.MODEL_OWNER, None)
        eval_results = fl_ctx.get_prop(AppConstants.VALIDATION_RESULT, None)

        if not data_client:
            self.log_error(fl_ctx, "data_client unknown. Evaluation result will not be saved to json", fire_event=False)
            return

        if not eval_results:
            self.log_error(fl_ctx, "Evaluation result not found.", fire_event=False)
            return

        try:
            # The return code must be read before the DXO conversion: an aborted task carries no DXO,
            # so from_shareable() raises on it, and that exception used to be the only trace the
            # failure ever left (FLIP#754).
            return_code = eval_results.get_return_code()
            if return_code != ReturnCode.OK:
                self._record_failure(fl_ctx, model_owner, data_client, return_code)
                return

            dxo = from_shareable(eval_results)
            if dxo.data_kind != DataKind.METRICS:
                self.log_error(
                    fl_ctx, f"Expected dxo of kind METRICS but got {dxo.data_kind} instead.", fire_event=False
                )
                self._record_failure(fl_ctx, model_owner, data_client, ReturnCode.EXECUTION_RESULT_ERROR)
                return
        except Exception:
            self.log_exception(fl_ctx, "Exception in handling evaluation result.", fire_event=False)
            self._record_failure(fl_ctx, model_owner, data_client, ReturnCode.EXECUTION_RESULT_ERROR)
            return

        self._record_result(model_owner, data_client, dxo.data, dxo.get_meta_prop(FlipMetaKey.SUPPLEMENTARY_RESULTS))

    def _write_json(self, fl_ctx: FLContext, run_dir: str, file_name: str, payload: Any, description: str) -> None:
        """Write one json artefact into the run's evaluation results directory."""
        res_file_path = resolve_path_under_root(run_dir, os.path.join(self._results_dir, file_name), description)
        eval_res_dir = os.path.dirname(res_file_path)
        if not os.path.exists(eval_res_dir):
            self.log_info(fl_ctx, f"Creating evaluation results directory at {eval_res_dir}")
            os.makedirs(eval_res_dir)
        with open(res_file_path, "w") as f:
            json.dump(payload, f, indent=4, default=to_serializable)

    def handle_evaluation_events(self, event_type: str, fl_ctx: FLContext) -> None:
        """FLIP integration point — ServerEventHandler dispatches events here, not via handle_event."""
        if event_type == EventType.START_RUN:
            self._eval_results.clear()
            self._eval_failures.clear()
            self._replies.clear()
        elif event_type == AppEventType.VALIDATION_RESULT_RECEIVED:
            self._handle_validation_result(fl_ctx)
        elif event_type == EventType.END_RUN:
            run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_job_id())
            self._write_json(fl_ctx, run_dir, self._json_file_name, self._eval_results, "evaluation results path")
            # Always written, even when empty, so its absence never has to be interpreted.
            self._write_json(fl_ctx, run_dir, self._failures_file_name, self._eval_failures, "evaluation failures path")


def _flatten_metrics(value: Any, path: tuple[str, ...], out: dict[str, float], dropped: set[str]) -> None:
    """Collect every finite numeric leaf of ``value`` into ``out`` under its ``/``-joined, upper-cased path.

    The label of any other leaf goes into ``dropped``.
    """
    if isinstance(value, Mapping):
        for key, child in value.items():
            _flatten_metrics(child, (*path, str(key)), out, dropped)
    elif not path:
        return
    elif isinstance(value, numbers.Real) and not isinstance(value, bool) and math.isfinite(value):
        out["/".join(path).upper()] = float(value)
    else:
        dropped.add("/".join(path).upper())


def _merge(metrics: Any, supplementary: Any) -> Any:
    """Merge ``supplementary`` into ``metrics`` key by key, keeping ``metrics``'s order; ``metrics`` wins a clash."""
    if not (isinstance(metrics, Mapping) and isinstance(supplementary, Mapping)):
        return metrics
    merged = {key: _merge(value, supplementary.get(key)) for key, value in metrics.items()}
    return {**merged, **{key: value for key, value in supplementary.items() if key not in merged}}
