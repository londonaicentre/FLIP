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

"""What a job needs at each site, and how it becomes NVFLARE's ``resource_spec`` (FLIP#70).

NVFLARE splits the question in two. The job states what it needs (``meta.json`` ``resource_spec``),
each site's resource manager states what it has (``local/resources.json``, from the kit's
``NUM_AVAILABLE_GPUS``), and the server's scheduler holds a job until every site can provide its
share. FLIP keeps that split: this module only decides *which* statement of need applies, and
writes it in NVFLARE's terms. It never compares a request with a site's capacity, and never lowers
one to fit.

Which statement applies, first match wins:

1. an override the researcher gave when submitting training (``UploadAppRequest.resources``);
2. the job's own ``config.json`` ``RESOURCE_SPEC``, in NVFLARE's names;
3. the fl-api default, ``JOB_RESOURCE_SPEC_NUM_GPUS`` / ``JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB``.
"""

from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from fl_api.utils.schemas import JobResources

# config.json RESOURCE_SPEC key -> JobResources field. NVFLARE's names, so a researcher writes what
# NVFLARE's docs show.
_NVFLARE_TO_CONTRACT = {"num_of_gpus": "num_gpus", "mem_per_gpu_in_GiB": "mem_per_gpu_gib"}


class ResourceSource(StrEnum):
    """Where the request that applies to a job came from."""

    SUBMISSION = "submission"
    CONFIG = "config.json"
    DEFAULT = "default"


def parse_resource_spec(raw: Any) -> JobResources:
    """Read a ``config.json`` ``RESOURCE_SPEC``, written in NVFLARE's names.

    Args:
        raw (Any): the value of ``RESOURCE_SPEC``, e.g. ``{"num_of_gpus": 1, "mem_per_gpu_in_GiB": 7}``.

    Returns:
        JobResources: the request.

    Raises:
        ValueError: if it is not an object of known keys with valid values.
    """
    if not isinstance(raw, dict):
        raise ValueError('RESOURCE_SPEC must be an object such as {"num_of_gpus": 1, "mem_per_gpu_in_GiB": 7}')
    unknown = sorted(set(raw) - set(_NVFLARE_TO_CONTRACT))
    if unknown:
        raise ValueError(f"RESOURCE_SPEC has unknown key(s) {unknown}; allowed: {sorted(_NVFLARE_TO_CONTRACT)}")
    if "num_of_gpus" not in raw:
        raise ValueError("RESOURCE_SPEC must set num_of_gpus")
    try:
        return JobResources(**{_NVFLARE_TO_CONTRACT[key]: value for key, value in raw.items()})
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'value'}: {err['msg']}" for err in e.errors())
        raise ValueError(f"RESOURCE_SPEC is invalid: {problems}") from e


def resolve_job_resources(
    override: JobResources | None, declared: JobResources | None, default: JobResources
) -> tuple[JobResources, ResourceSource]:
    """Pick the request that applies to a job.

    Args:
        override (JobResources | None): what the researcher set at submission, if anything.
        declared (JobResources | None): the job's ``config.json`` ``RESOURCE_SPEC``, if any.
        default (JobResources): the fl-api default.

    Returns:
        tuple[JobResources, ResourceSource]: the request and where it came from.
    """
    if override is not None:
        return override, ResourceSource.SUBMISSION
    if declared is not None:
        return declared, ResourceSource.CONFIG
    return default, ResourceSource.DEFAULT


def nvflare_resource_spec(resources: JobResources, sites: list[str]) -> dict[str, dict[str, int]]:
    """Write a request as ``meta.json`` ``resource_spec``.

    One flat entry per site rather than NVFLARE 2.9's portable ``@default``, which refuses
    ``mem_per_gpu_in_GiB``. A flat entry still counts as portable, so a Docker or Kubernetes job
    launcher would turn ``num_of_gpus`` into a device request. No GPUs writes ``{}``, as before.

    Args:
        resources (JobResources): the request.
        sites (list[str]): the job's sites, by FL client name.

    Returns:
        dict[str, dict[str, int]]: the ``resource_spec``.
    """
    if not resources.num_gpus:
        return {}
    # "num_of_gpus" is GPUResourceManager's num_gpu_key: a requirement without it makes the manager raise.
    entry = {"num_of_gpus": resources.num_gpus, "mem_per_gpu_in_GiB": resources.mem_per_gpu_gib}
    return {site: dict(entry) for site in sites}
