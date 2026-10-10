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

"""What a job needs at each site, and how it becomes NVFLARE's resource_spec (FLIP#70).

The job states its need; the site's GPUResourceManager states what it has; NVFLARE's scheduler
decides. Nothing here ever lowers a request to fit a site.
"""

from unittest.mock import MagicMock

import pytest
from nvflare.app_common.resource_managers.gpu_resource_manager import GPUResourceManager
from nvflare.private.fed.server.job_meta_validator import JobMetaValidator
from nvflare.utils.job_launcher_utils import get_resource_manager_spec, validate_portable_resource_conflicts
from pydantic import ValidationError

from fl_api.utils.job_resources import (
    ResourceSource,
    default_job_resources,
    nvflare_resource_spec,
    parse_resource_spec,
    resolve_job_resources,
)
from fl_api.utils.schemas import MAX_GPUS_PER_SITE, MAX_MEM_PER_GPU_GIB, JobResources, UploadAppRequest

TRUSTS = ["Trust_1", "Trust_3"]


# ── the request shape ───────────────────────────────────────────────────────────────


def test_a_gpu_request_holds_a_count_and_a_per_gpu_memory():
    resources = JobResources(num_gpus=1, mem_per_gpu_gib=7)

    assert (resources.num_gpus, resources.mem_per_gpu_gib) == (1, 7)


def test_memory_defaults_to_no_minimum():
    assert JobResources(num_gpus=2).mem_per_gpu_gib == 0


@pytest.mark.parametrize("bad", [True, 1.5, "1", -1, MAX_GPUS_PER_SITE + 1])
def test_a_gpu_count_must_be_a_plain_integer_within_the_sanity_cap(bad):
    with pytest.raises(ValidationError):
        JobResources(num_gpus=bad)


@pytest.mark.parametrize("bad", [True, 7.5, "7", -1, MAX_MEM_PER_GPU_GIB + 1])
def test_per_gpu_memory_must_be_a_plain_integer_within_the_sanity_cap(bad):
    with pytest.raises(ValidationError):
        JobResources(num_gpus=1, mem_per_gpu_gib=bad)


def test_memory_without_gpus_is_refused_rather_than_ignored():
    with pytest.raises(ValidationError, match="mem_per_gpu_gib"):
        JobResources(num_gpus=0, mem_per_gpu_gib=7)


def test_an_unknown_field_is_refused_so_a_typo_never_drops_the_request():
    with pytest.raises(ValidationError):
        JobResources(num_gpus=1, num_cpus=4)


def test_the_upload_request_carries_an_optional_override():
    body = {"project_id": "p", "cohort_query": "q", "trusts": TRUSTS, "bundle_urls": []}

    assert UploadAppRequest(**body).resources is None
    assert UploadAppRequest(**body, resources={"num_gpus": 1}).resources == JobResources(num_gpus=1)


# ── config.json RESOURCE_SPEC, in NVFLARE's own names ───────────────────────────────


def test_config_json_uses_nvflares_resource_spec_names():
    assert parse_resource_spec({"num_of_gpus": 1, "mem_per_gpu_in_GiB": 7}) == JobResources(
        num_gpus=1, mem_per_gpu_gib=7
    )


@pytest.mark.parametrize(
    "raw",
    [
        "1 GPU",
        {"num_gpus": 1},  # the hub's name, not NVFLARE's
        {"num_of_gpus": 1, "num_of_cpus": 2},
        {"num_of_gpus": True},
        {"mem_per_gpu_in_GiB": 7},
        {"num_of_gpus": 0, "mem_per_gpu_in_GiB": 7},
    ],
)
def test_a_malformed_resource_spec_is_an_error_naming_the_key(raw):
    with pytest.raises(ValueError, match="RESOURCE_SPEC"):
        parse_resource_spec(raw)


# ── which request wins ──────────────────────────────────────────────────────────────


def test_with_nothing_declared_the_fl_api_default_applies():
    resources, source = resolve_job_resources(override=None, declared=None, default=lambda: JobResources(num_gpus=0))

    assert resources == JobResources(num_gpus=0)
    assert source is ResourceSource.DEFAULT


def test_the_jobs_config_json_beats_the_default():
    declared = JobResources(num_gpus=1, mem_per_gpu_gib=7)

    resources, source = resolve_job_resources(
        override=None, declared=declared, default=lambda: JobResources(num_gpus=0)
    )

    assert resources == declared
    assert source is ResourceSource.CONFIG


def test_an_override_at_submission_beats_the_jobs_config_json():
    override = JobResources(num_gpus=2, mem_per_gpu_gib=16)

    resources, source = resolve_job_resources(
        override=override, declared=JobResources(num_gpus=1), default=JobResources(num_gpus=0)
    )

    assert resources == override
    assert source is ResourceSource.SUBMISSION


def test_an_override_of_zero_gpus_is_honoured_not_mistaken_for_no_override():
    resources, source = resolve_job_resources(
        override=JobResources(num_gpus=0), declared=JobResources(num_gpus=1), default=JobResources(num_gpus=1)
    )

    assert resources.num_gpus == 0
    assert source is ResourceSource.SUBMISSION


def test_the_default_is_never_read_when_the_job_or_the_run_sets_a_request():
    """A misconfigured default must not break a job that does not use it."""

    def broken_default():
        raise AssertionError("the default was read")

    assert resolve_job_resources(JobResources(num_gpus=1), None, broken_default)[0].num_gpus == 1
    assert resolve_job_resources(None, JobResources(num_gpus=2), broken_default)[0].num_gpus == 2


# ── the fl-api default ──────────────────────────────────────────────────────────────


def _settings(num_gpus, mem):
    return MagicMock(JOB_RESOURCE_SPEC_NUM_GPUS=num_gpus, JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB=mem)


def test_the_default_comes_from_the_fl_api_settings(monkeypatch):
    monkeypatch.setattr("fl_api.utils.job_resources.get_settings", lambda: _settings(1, 7))

    assert default_job_resources() == JobResources(num_gpus=1, mem_per_gpu_gib=7)


@pytest.mark.parametrize(("num_gpus", "mem"), [(0, 7), (MAX_GPUS_PER_SITE + 1, 0), (1, MAX_MEM_PER_GPU_GIB + 1)])
def test_a_default_the_request_contract_refuses_names_the_settings(monkeypatch, num_gpus, mem):
    """Deleting only JOB_RESOURCE_SPEC_NUM_GPUS leaves memory without a GPU, for one."""
    monkeypatch.setattr("fl_api.utils.job_resources.get_settings", lambda: _settings(num_gpus, mem))

    with pytest.raises(ValueError, match="JOB_RESOURCE_SPEC_NUM_GPUS"):
        default_job_resources()


# ── meta.json resource_spec ─────────────────────────────────────────────────────────


def test_no_gpus_writes_an_empty_resource_spec_as_before():
    assert nvflare_resource_spec(JobResources(num_gpus=0), TRUSTS) == {}


def test_gpus_write_one_flat_entry_per_site_in_nvflares_keys():
    """Explicit per-site entries, not "@default": NVFLARE 2.9's portable @default refuses mem_per_gpu_in_GiB."""
    spec = nvflare_resource_spec(JobResources(num_gpus=1, mem_per_gpu_gib=7), TRUSTS)

    assert spec == {site: {"num_of_gpus": 1, "mem_per_gpu_in_GiB": 7} for site in TRUSTS}


# ── NVFLARE agrees ──────────────────────────────────────────────────────────────────


def _meta(resources: JobResources) -> dict:
    return {
        "name": "m",
        "resource_spec": nvflare_resource_spec(resources, TRUSTS),
        "deploy_map": {"app": ["server", *TRUSTS]},
        "min_clients": len(TRUSTS),
        "mandatory_clients": TRUSTS,
    }


@pytest.mark.parametrize("resources", [JobResources(num_gpus=0), JobResources(num_gpus=2, mem_per_gpu_gib=16)])
def test_nvflares_job_validator_accepts_the_resource_spec(resources):
    """The server runs these on every submitted job and refuses it on a ValueError."""
    meta = _meta(resources)

    JobMetaValidator._validate_resource("job", meta)
    validate_portable_resource_conflicts(meta)


@pytest.mark.parametrize(
    ("site_gpus", "site_mem", "resources", "admitted"),
    [
        (0, 0, JobResources(num_gpus=0), True),
        (0, 0, JobResources(num_gpus=1, mem_per_gpu_gib=7), False),
        (2, 16, JobResources(num_gpus=1, mem_per_gpu_gib=7), True),
        (2, 16, JobResources(num_gpus=1, mem_per_gpu_gib=24), False),
        (2, 16, JobResources(num_gpus=3), False),
    ],
)
def test_a_sites_gpu_manager_admits_exactly_what_it_can_provide(site_gpus, site_mem, resources, admitted):
    """What the scheduler sends each site, judged by the manager our kits configure from NUM_AVAILABLE_GPUS."""
    manager = GPUResourceManager(num_of_gpus=site_gpus, mem_per_gpu_in_GiB=site_mem, ignore_host=True)
    requirement = get_resource_manager_spec(_meta(resources), "Trust_1")

    assert manager._check_required_resource_available(requirement) is admitted
