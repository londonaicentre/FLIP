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

"""What a job needs at each site, from its own ``meta.json`` (FLIP#70).

FLARE splits the question in two. The job states what it needs (``meta.json`` ``resource_spec``),
each site's resource manager states what it has (``local/resources.json``, from the kit's
``NUM_AVAILABLE_GPUS``), and the server's scheduler holds a job until every site can provide its
share. FLIP keeps that split: the researcher's ``meta.json`` is the job's statement, as it would be
for ``nvflare job submit``. This module checks it, settles the GPU count and writes the result. It
never compares a request with a site's capacity, and never lowers one to fit.

What a researcher's ``meta.json`` may hold:

- ``resource_spec``: only FLARE's portable ``@default`` block (``num_of_gpus``, ``num_of_cpus``,
  ``memory``), which applies at every site. A researcher cannot know the trusts' site names, so a
  per-site block is refused.
- ``custom_props``: kept, except ``model_id``. The app's ``config.json`` carries FLIP's model id
  now, so a placeholder left by a recipe export is dropped rather than shadowing it.
- ``name``, ``deploy_map``, ``min_clients``, ``mandatory_clients``: replaced from the run. A recipe
  export always writes them.
- Anything else, such as ``launcher_spec``, ``study`` or ``scope``, is refused. Those choose how a
  site runs the job, which is the site's decision.

The GPU count, first match wins:

1. an override the researcher gave when submitting training (``UploadAppRequest.resources``);
2. the job's ``meta.json`` ``resource_spec["@default"]["num_of_gpus"]``;
3. the fl-api default, ``JOB_RESOURCE_SPEC_NUM_GPUS``.

CPUs and memory come only from the job. Only a container launcher (Docker, Kubernetes) reads them,
and the override and the default are about GPUs.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from fl_api.config import get_settings
from fl_api.utils.refusals import ResearcherRefusal
from fl_api.utils.schemas import MAX_GPUS_PER_SITE, JobResources

PORTABLE_DEFAULT_KEY = "@default"
# Written by FLIP from the run, whatever the researcher's file says.
FLIP_OWNED_META_KEYS = frozenset({"name", "deploy_map", "min_clients", "mandatory_clients"})
# Taken from the researcher's file.
RESEARCHER_META_KEYS = frozenset({"resource_spec", "custom_props"})
# The custom_props key FLIP published its model id under before it moved to config.json.
LEGACY_MODEL_ID_PROP = "model_id"


class ResourceSource(StrEnum):
    """Where the GPU count that applies to a job came from."""

    SUBMISSION = "submission"
    META = "meta.json"
    DEFAULT = "default"


class PortableResources(BaseModel):
    """FLARE's portable ``resource_spec["@default"]`` block, with its validity rules and FLIP's GPU cap."""

    model_config = ConfigDict(extra="forbid")

    num_of_gpus: StrictInt | None = Field(default=None, ge=0, le=MAX_GPUS_PER_SITE)
    num_of_cpus: StrictInt | None = Field(default=None, ge=1)
    # FLARE's own pattern (nvflare.utils.job_launcher_utils), e.g. "16Gi".
    memory: StrictStr | None = Field(default=None, pattern=r"^[1-9][0-9]*(Mi|Gi|Ti)$")


@dataclass(frozen=True)
class JobMeta:
    """The parts of a researcher's ``meta.json`` that FLIP keeps."""

    resources: PortableResources = field(default_factory=PortableResources)
    custom_props: dict[str, Any] = field(default_factory=dict)

    @property
    def declared_gpus(self) -> int | None:
        """The job's GPU count, or None when it states none (an empty ``resource_spec`` is not zero)."""
        return self.resources.num_of_gpus


def parse_job_meta(raw: Any) -> JobMeta:
    """Check a researcher's ``meta.json`` and keep the parts FLIP takes from it.

    Args:
        raw (Any): the parsed ``meta.json``.

    Returns:
        JobMeta: the job's portable resources and its ``custom_props``, without FLIP's legacy ``model_id``.

    Raises:
        ResearcherRefusal: naming the key, if the file holds something FLIP refuses (see the module docstring).
    """
    if not isinstance(raw, dict):
        raise ResearcherRefusal("meta.json must be a JSON object")
    refused = sorted(set(raw) - FLIP_OWNED_META_KEYS - RESEARCHER_META_KEYS)
    if refused:
        raise ResearcherRefusal(
            f"meta.json has key(s) FLIP does not accept: {refused}. A job's meta.json may declare resource_spec "
            f"and custom_props; FLIP writes {sorted(FLIP_OWNED_META_KEYS)} from the run, and how a trust runs the "
            "job is the trust's choice."
        )

    # None is how FLARE spells "nothing" too; an empty list or string is a mistake, not nothing.
    resource_spec = {} if raw.get("resource_spec") is None else raw["resource_spec"]
    if not isinstance(resource_spec, dict):
        raise ResearcherRefusal('meta.json resource_spec must be an object such as {"@default": {"num_of_gpus": 1}}')
    per_site = sorted(set(resource_spec) - {PORTABLE_DEFAULT_KEY})
    if per_site:
        raise ResearcherRefusal(
            f"meta.json resource_spec has per-site block(s) {per_site}. Declare what the job needs at every trust "
            f'under "{PORTABLE_DEFAULT_KEY}" instead: a job cannot know the trusts\' site names.'
        )
    default_block = {} if resource_spec.get(PORTABLE_DEFAULT_KEY) is None else resource_spec[PORTABLE_DEFAULT_KEY]
    if not isinstance(default_block, dict):
        raise ResearcherRefusal(f'meta.json resource_spec["{PORTABLE_DEFAULT_KEY}"] must be an object')
    try:
        resources = PortableResources(**default_block)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, err['loc'])) or 'value'}: {err['msg']}" for err in e.errors())
        raise ResearcherRefusal(f'meta.json resource_spec["{PORTABLE_DEFAULT_KEY}"] is invalid: {problems}') from e

    custom_props = {} if raw.get("custom_props") is None else raw["custom_props"]
    if not isinstance(custom_props, dict):
        raise ResearcherRefusal("meta.json custom_props must be an object")
    custom_props = {key: value for key, value in custom_props.items() if key != LEGACY_MODEL_ID_PROP}

    return JobMeta(resources=resources, custom_props=custom_props)


def default_job_resources() -> JobResources:
    """The GPU count for a job that declares none: ``JOB_RESOURCE_SPEC_NUM_GPUS``.

    The fl-api checks it at start-up (``create_fl_session``), so a value the request contract refuses
    stops the service instead of failing every upload that falls back to it.

    Returns:
        JobResources: the default request.

    Raises:
        ValueError: if the setting is above the sanity cap.
    """
    num_gpus = get_settings().JOB_RESOURCE_SPEC_NUM_GPUS
    try:
        return JobResources(num_gpus=num_gpus)
    except ValidationError as e:
        raise ValueError(
            f"JOB_RESOURCE_SPEC_NUM_GPUS={num_gpus} is not a valid default GPU count: {e.errors()[0]['msg']}"
        ) from e


def resolve_job_resources(
    override: JobResources | None, declared_gpus: int | None, default: Callable[[], JobResources]
) -> tuple[JobResources, ResourceSource]:
    """Pick the GPU count that applies to a job.

    Args:
        override (JobResources | None): what the researcher set at submission, if anything.
        declared_gpus (int | None): the job's ``meta.json`` GPU count, if it states one.
        default (Callable[[], JobResources]): the fl-api default, read only when neither of the others is set.

    Returns:
        tuple[JobResources, ResourceSource]: the request and where it came from.
    """
    if override is not None:
        return override, ResourceSource.SUBMISSION
    if declared_gpus is not None:
        return JobResources(num_gpus=declared_gpus), ResourceSource.META
    return default(), ResourceSource.DEFAULT


def nvflare_resource_spec(resources: JobResources, declared: PortableResources) -> dict[str, dict[str, int | str]]:
    """Write the job's ``meta.json`` ``resource_spec``: one portable ``@default`` block every site reads.

    FLARE gives each site's resource manager the ``num_of_gpus`` (``num_of_gpus`` is
    GPUResourceManager's ``num_gpu_key``), and a Docker or Kubernetes launcher turns all three fields
    into the job's container request. Zero GPUs leaves the count out; a job that asks for nothing
    gets ``{}``, as before.

    Args:
        resources (JobResources): the GPU count that won.
        declared (PortableResources): the job's own block, for its CPUs and memory.

    Returns:
        dict[str, dict[str, int | str]]: the ``resource_spec``.
    """
    block = declared.model_dump(exclude_none=True) | {"num_of_gpus": resources.num_gpus}
    if not resources.num_gpus:
        del block["num_of_gpus"]
    return {PORTABLE_DEFAULT_KEY: block} if block else {}
