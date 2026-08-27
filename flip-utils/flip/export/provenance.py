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

"""Federated-training provenance carried inside an exported bundle.

Provenance is *embedded* in the bundle's ``metadata.json`` rather than shipped alongside it, so a
deployed artefact cannot become separated from the record of the run that produced it. A model
that cannot be traced back to its federated origin fails both governance and clinical safety
review.

Packaging runs separately from training and does not call the Central Hub, so these values are
supplied by the caller — whoever is preparing the model for deployment — rather than discovered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

#: Key under which provenance is written into the bundle's ``metadata.json``.
PROVENANCE_KEY = "flip_provenance"


@dataclass
class Provenance:
    """Record of the federated run a model came from.

    Attributes:
        model_id (str): FLIP model UUID.
        project_id (str): FLIP project the run belonged to.
        participating_trusts (list[str]): Trusts that contributed to training.
        global_rounds (int | None): Number of federated rounds.
        local_rounds (int | None): Local epochs per round.
        final_aggregate_metric (str): Final aggregate metric, e.g. ``"Dice=0.91"``.
        source_checkpoint (str): Filename of the checkpoint exported from.
        fl_backend (str): ``nvflare`` or ``flower``.
        flip_version (str): Version of the ``flip`` package that performed the export.
        architecture (str): Model class name (caller-supplied; e.g. read from the checkpoint's
            ``train_conf`` by the caller before constructing this record).
        extra (dict[str, Any]): Any additional caller-supplied fields.
    """

    model_id: str = ""
    project_id: str = ""
    participating_trusts: list[str] = field(default_factory=list)
    global_rounds: int | None = None
    local_rounds: int | None = None
    final_aggregate_metric: str = ""
    source_checkpoint: str = ""
    fl_backend: str = "nvflare"
    flip_version: str = ""
    architecture: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, exported_at: datetime | None = None) -> dict[str, Any]:
        """Render the provenance for embedding, stamping the export time.

        Args:
            exported_at (datetime | None): Export timestamp. Defaults to now, in UTC.

        Returns:
            dict[str, Any]: The provenance block, with empty fields omitted.
        """
        stamped = exported_at or datetime.now(timezone.utc)
        record: dict[str, Any] = {
            "flip_model_id": self.model_id,
            "flip_project_id": self.project_id,
            "participating_trusts": self.participating_trusts,
            "global_rounds": self.global_rounds,
            "local_rounds": self.local_rounds,
            "final_aggregate_metric": self.final_aggregate_metric,
            "source_checkpoint": self.source_checkpoint,
            "fl_backend": self.fl_backend,
            "flip_version": self.flip_version,
            "architecture": self.architecture,
            "exported_at": stamped.isoformat(),
            # Stated explicitly so it survives into any downstream record. A federated research
            # model is neither CE-marked nor FDA-cleared.
            "not_for_clinical_use": True,
        }
        record.update(self.extra)
        return {key: value for key, value in record.items() if value not in ("", None, [])}

    def merged_into(self, metadata: dict[str, Any], exported_at: datetime | None = None) -> dict[str, Any]:
        """Return ``metadata`` with the provenance block added under :data:`PROVENANCE_KEY`.

        Args:
            metadata (dict[str, Any]): The author-supplied bundle metadata.
            exported_at (datetime | None): Export timestamp. Defaults to now, in UTC.

        Returns:
            dict[str, Any]: A copy of the metadata carrying the provenance block.
        """
        stamped = dict(metadata)
        stamped[PROVENANCE_KEY] = self.as_dict(exported_at)
        return stamped
