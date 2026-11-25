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

"""NVFLARE's Kubernetes job launcher, building a FLIP project's study entry when its job starts (FLIP#1678).

A FLIP job runs in its project's NVFLARE study, and NVFLARE starts a job pod only for a study with an entry in
``local/study_runtime.yaml``. Those entries were the operator's to add, with ``sync-project-studies`` after every
approval and every upgrade: a project the operator had not synced, or synced before the trust had frozen its cohort,
aborted its job on every site, and an entry synced before an upgrade kept the old job image.

This launcher builds a project's entry at launch instead, the way that command did
(``flip.nvflare.project_studies.project_entry``): the default study's job image and pod template and, where the
template declares the images volume, a read-only mount of the project's own folder, or of its directory of XNAT's
archive once XNAT holds one. Built at launch, it always carries the kit's current image and template. An entry in
the operator's file is not used for a project.

It keeps the trust-side gate the file was: an entry, and with it any mount of studies at rest, only for a project
this trust approved. The hub names a job's study (a researcher cannot pick one: ``meta.json`` ``study`` is refused),
but the trust does not take its word for it: a project gets its entry only once imaging-api names the XNAT project
it created when the trust approved it (``xnat_project_id``). Any other project's job, a tabular one's or one the
trust never approved, and any job while imaging-api cannot answer, runs as the default study, which mounts no
images and streams them through imaging-api, whose download routes serve only the approved cohort.

A job outside a project's study, which only a hub older than FLIP#1678 sends, runs as the kit's default study.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import yaml
from nvflare.app_opt.job_launcher.k8s_launcher import ClientK8sJobLauncher
from nvflare.app_opt.job_launcher.study_runtime import StudyRuntime, load_study_runtime_file, resolve_study_runtime

from flip.nvflare.k8s_job_mode import STUDY_RUNTIME, STUDY_RUNTIME_DEFAULT
from flip.nvflare.project_studies import is_project, project_entry, xnat_project_id


def default_study_file(local_dir: Path) -> Path:
    """The kit's default study: beside the operator's file where the chart names one, else the study file itself."""
    path = local_dir / STUDY_RUNTIME_DEFAULT
    return path if path.is_file() else local_dir / STUDY_RUNTIME


class ProjectK8sJobLauncher(ClientK8sJobLauncher):
    """``ClientK8sJobLauncher`` that gives each FLIP project's study its entry when the project's job starts."""

    def _resolve_study_runtime(self, workspace_root: str, study: str | None) -> StudyRuntime | None:
        local_dir = Path(workspace_root, "local")
        if study is not None and is_project(study):
            return self._project_runtime(local_dir, study)
        runtime = super()._resolve_study_runtime(workspace_root, study)
        if runtime is not None and runtime.container_image:
            return runtime
        return self._default_runtime(local_dir) or runtime

    def _default_runtime(self, local_dir: Path) -> StudyRuntime | None:
        """The kit's default study, parsed beside its pod template; None if the kit has none."""
        default_file = default_study_file(local_dir)
        if not default_file.is_file():
            return None
        runtime_map = load_study_runtime_file(str(default_file), launcher_mode="k8s", logger=self.logger)
        return resolve_study_runtime(runtime_map, "default", str(default_file), logger=self.logger)

    def _project_runtime(self, local_dir: Path, project: str) -> StudyRuntime | None:
        if xnat_project_id(project) is None:
            # Not approved for imaging here, or imaging-api cannot say: mount nothing at rest (fail closed).
            self.logger.info(f"project {project}: no XNAT project at this trust, so its job streams")
            return self._default_runtime(local_dir)
        try:
            document: dict[str, Any] = yaml.safe_load(default_study_file(local_dir).read_text(encoding="utf-8"))
            entry = project_entry(local_dir, document["studies"]["default"], project)
        except (OSError, KeyError, TypeError, yaml.YAMLError) as e:
            raise RuntimeError(f"cannot build the study entry of project {project}: {type(e).__name__}: {e}") from e
        # Through NVFLARE's own parser, so the entry is held to what it would accept from the site's file.
        with tempfile.NamedTemporaryFile("w", suffix=".yaml") as f:
            json.dump({"format_version": 2, "studies": {project: entry}}, f)
            f.flush()
            runtime_map = load_study_runtime_file(f.name, launcher_mode="k8s", logger=self.logger)
            runtime = resolve_study_runtime(runtime_map, project, f.name, logger=self.logger)
        self.logger.info(f"project {project}: job image {runtime.container_image}, study entry built at launch")
        return runtime
