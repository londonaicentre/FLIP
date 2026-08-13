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

"""
XNAT helpers for the FLIP *data enrichment* stage.

Data enrichment is the platform stage where a model developer adds whatever an app needs on top of
the imaging data FLIP pulled into each Trust's XNAT — most commonly segmentation labels for a
supervised app. This subpackage uploads those files into the right XNAT scan resource so that
:meth:`flip.FLIPBase.get_by_accession_number` brings them down to the FL client alongside the image.

**Where this runs.** On the model developer's workstation inside the Trust network (or as an XNAT
Container Service job), authenticated as their own XNAT account. It is deliberately *not* part of
the FL training path: FL client containers hold no XNAT credentials and reach imaging data only
through the imaging-api proxy.

Example usage:

.. code-block:: python

    from flip.xnat import XnatClient, read_manifest, upload_enrichment_files

    client = XnatClient.from_env()
    project = client.resolve_project_by_flip_project_id("<flip-project-uuid>")
    summary = upload_enrichment_files(
        client,
        project,
        read_manifest("manifest.csv"),
    )
    print(summary.render())
"""

from flip.xnat.client import XnatClient, XnatScan
from flip.xnat.enrichment import (
    EnrichmentItem,
    EnrichmentSummary,
    read_manifest,
    upload_enrichment_files,
)

__all__ = [
    "EnrichmentItem",
    "EnrichmentSummary",
    "XnatClient",
    "XnatScan",
    "read_manifest",
    "upload_enrichment_files",
]
