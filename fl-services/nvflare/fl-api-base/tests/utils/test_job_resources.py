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

"""What a job needs at each site, from its own meta.json (FLIP#70).

The job states its need; the site's GPUResourceManager states what it has; FLARE's scheduler
decides. Nothing here ever lowers a request to fit a site.
"""

from unittest.mock import MagicMock

import pytest
from nvflare.app_common.resource_managers.gpu_resource_manager import GPUResourceManager
from nvflare.private.fed.server.job_meta_validator import JobMetaValidator
from nvflare.utils.job_launcher_utils import (
    get_portable_resource_spec,
    get_resource_manager_spec,
    validate_portable_resource_conflicts,
)
from pydantic import ValidationError

from fl_api.utils.job_resources import (
    JobMeta,
    PortableResources,
    ResourceSource,
    default_job_resources,
    nvflare_resource_spec,
    parse_job_meta,
    resolve_job_resources,
)
from fl_api.utils.schemas import MAX_GPUS_PER_SITE, JobResources, UploadAppRequest

TRUSTS = ["Trust_1", "Trust_3"]


# ── the run's override ──────────────────────────────────────────────────────────────


def test_a_run_override_is_a_gpu_count():
    assert JobResources(num_gpus=2).num_gpus == 2


@pytest.mark.parametrize("bad", [True, 1.5, "1", -1, MAX_GPUS_PER_SITE + 1])
def test_a_gpu_count_must_be_a_plain_integer_within_the_sanity_cap(bad):
    with pytest.raises(ValidationError):
        JobResources(num_gpus=bad)


def test_an_unknown_field_is_refused_so_a_typo_never_drops_the_request():
    with pytest.raises(ValidationError):
        JobResources(num_gpus=1, mem_per_gpu_gib=7)


def test_the_upload_request_carries_an_optional_override():
    body = {"project_id": "p", "cohort_query": "q", "trusts": TRUSTS, "bundle_urls": []}

    assert UploadAppRequest(**body).resources is None
    assert UploadAppRequest(**body, resources={"num_gpus": 1}).resources == JobResources(num_gpus=1)


# ── the researcher's meta.json ──────────────────────────────────────────────────────


def test_an_empty_meta_json_declares_nothing():
    meta = parse_job_meta({})

    assert meta == JobMeta()
    assert meta.declared_gpus is None


def test_a_recipe_export_is_accepted_and_flip_takes_over_the_parts_naming_sites():
    """What ``FedJob.export_job`` writes; FLIP replaces name, deploy_map, min_clients and mandatory_clients."""
    exported = {
        "name": "fedavg",
        "resource_spec": {"@default": {"num_of_gpus": 1}},
        "min_clients": 2,
        "deploy_map": {"app_server": ["server"], "app_site-1": ["site-1"]},
        "mandatory_clients": ["site-1"],
    }

    assert parse_job_meta(exported) == JobMeta(resources=PortableResources(num_of_gpus=1))


def test_the_portable_default_block_holds_gpus_cpus_and_memory():
    meta = parse_job_meta({"resource_spec": {"@default": {"num_of_gpus": 2, "num_of_cpus": 8, "memory": "32Gi"}}})

    assert meta.resources == PortableResources(num_of_gpus=2, num_of_cpus=8, memory="32Gi")
    assert meta.declared_gpus == 2


def test_null_is_nothing_as_nvflare_reads_it():
    assert parse_job_meta({"resource_spec": None, "custom_props": None}) == JobMeta()
    assert parse_job_meta({"resource_spec": {"@default": None}}) == JobMeta()


def test_an_empty_resource_spec_declares_no_gpu_count():
    """A FLIP template ships ``"resource_spec": {}``: that leaves the fl-api default in force, not zero GPUs."""
    assert parse_job_meta({"resource_spec": {}}).declared_gpus is None
    assert parse_job_meta({"resource_spec": {"@default": {"num_of_cpus": 4}}}).declared_gpus is None


def test_zero_gpus_declared_is_a_declaration():
    assert parse_job_meta({"resource_spec": {"@default": {"num_of_gpus": 0}}}).declared_gpus == 0


def test_a_per_site_block_is_refused_because_a_researcher_cannot_know_the_site_names():
    with pytest.raises(ValueError, match=r"resource_spec.*Trust_1.*@default"):
        parse_job_meta({"resource_spec": {"Trust_1": {"num_of_gpus": 1}}})


@pytest.mark.parametrize(
    ("block", "named"),
    [
        ({"num_of_gpus": 1, "mem_per_gpu_in_GiB": 7}, "mem_per_gpu_in_GiB"),
        ({"num_of_gpus": True}, "num_of_gpus"),
        ({"num_of_gpus": 1.5}, "num_of_gpus"),
        ({"num_of_gpus": "1"}, "num_of_gpus"),
        ({"num_of_gpus": -1}, "num_of_gpus"),
        ({"num_of_gpus": MAX_GPUS_PER_SITE + 1}, "num_of_gpus"),
        ({"num_of_cpus": 0}, "num_of_cpus"),
        ({"memory": "16GB"}, "memory"),
        ({"memory": 16}, "memory"),
    ],
)
def test_a_default_block_nvflare_or_the_sanity_cap_refuses_is_an_error_naming_the_field(block, named):
    with pytest.raises(ValueError, match=named):
        parse_job_meta({"resource_spec": {"@default": block}})


@pytest.mark.parametrize("key", ["launcher_spec", "study", "scope", "resorce_spec"])
def test_keys_that_choose_how_a_site_runs_the_job_or_unknown_keys_are_refused(key):
    with pytest.raises(ValueError, match=key):
        parse_job_meta({key: {}})


@pytest.mark.parametrize(
    ("raw", "named"),
    [
        ([], "meta.json"),
        ({"resource_spec": []}, "resource_spec"),
        ({"resource_spec": {"@default": 1}}, "@default"),
        ({"custom_props": "x"}, "custom_props"),
        ({"resource_spec": ""}, "resource_spec"),
        ({"resource_spec": {"@default": []}}, "@default"),
        ({"custom_props": []}, "custom_props"),
    ],
)
def test_a_value_of_the_wrong_type_is_refused(raw, named):
    with pytest.raises(ValueError, match=named):
        parse_job_meta(raw)


def test_custom_props_are_kept_but_flip_no_longer_reads_a_model_id_from_them():
    """FLIP's recipes write a placeholder model_id there; the app's config.json carries the real one."""
    meta = parse_job_meta({"custom_props": {"model_id": "00000000-0000-0000-0000-000000000001", "team": "radiology"}})

    assert meta.custom_props == {"team": "radiology"}


# ── which GPU count wins ────────────────────────────────────────────────────────────


def test_with_nothing_declared_the_fl_api_default_applies():
    resources, source = resolve_job_resources(
        override=None, declared_gpus=None, default=lambda: JobResources(num_gpus=0)
    )

    assert resources == JobResources(num_gpus=0)
    assert source is ResourceSource.DEFAULT


def test_the_jobs_meta_json_beats_the_default():
    resources, source = resolve_job_resources(override=None, declared_gpus=1, default=lambda: JobResources(num_gpus=0))

    assert resources == JobResources(num_gpus=1)
    assert source is ResourceSource.META


def test_an_override_at_submission_beats_the_jobs_meta_json():
    resources, source = resolve_job_resources(
        override=JobResources(num_gpus=2), declared_gpus=1, default=lambda: JobResources(num_gpus=0)
    )

    assert resources == JobResources(num_gpus=2)
    assert source is ResourceSource.SUBMISSION


def test_zero_is_honoured_wherever_it_comes_from_not_mistaken_for_nothing():
    default = lambda: JobResources(num_gpus=1)  # noqa: E731

    assert resolve_job_resources(JobResources(num_gpus=0), 1, default) == (
        JobResources(num_gpus=0),
        ResourceSource.SUBMISSION,
    )
    assert resolve_job_resources(None, 0, default) == (JobResources(num_gpus=0), ResourceSource.META)


def test_the_default_is_never_read_when_the_job_or_the_run_sets_a_count():
    """A misconfigured default must not break a job that does not use it."""

    def broken_default():
        raise AssertionError("the default was read")

    assert resolve_job_resources(JobResources(num_gpus=1), None, broken_default)[0].num_gpus == 1
    assert resolve_job_resources(None, 2, broken_default)[0].num_gpus == 2


# ── the fl-api default ──────────────────────────────────────────────────────────────


def test_the_default_comes_from_the_fl_api_settings(monkeypatch):
    monkeypatch.setattr("fl_api.utils.job_resources.get_settings", lambda: MagicMock(JOB_RESOURCE_SPEC_NUM_GPUS=1))

    assert default_job_resources() == JobResources(num_gpus=1)


def test_a_default_above_the_sanity_cap_names_the_setting(monkeypatch):
    monkeypatch.setattr(
        "fl_api.utils.job_resources.get_settings",
        lambda: MagicMock(JOB_RESOURCE_SPEC_NUM_GPUS=MAX_GPUS_PER_SITE + 1),
    )

    with pytest.raises(ValueError, match="JOB_RESOURCE_SPEC_NUM_GPUS"):
        default_job_resources()


# ── meta.json resource_spec ─────────────────────────────────────────────────────────


def test_no_gpus_and_nothing_else_writes_an_empty_resource_spec_as_before():
    assert nvflare_resource_spec(JobResources(num_gpus=0), PortableResources()) == {}


def test_gpus_go_in_the_portable_default_block_every_site_reads():
    assert nvflare_resource_spec(JobResources(num_gpus=1), PortableResources()) == {"@default": {"num_of_gpus": 1}}


def test_the_count_that_won_replaces_the_jobs_and_its_cpus_and_memory_stay():
    declared = PortableResources(num_of_gpus=1, num_of_cpus=8, memory="32Gi")

    assert nvflare_resource_spec(JobResources(num_gpus=2), declared) == {
        "@default": {"num_of_gpus": 2, "num_of_cpus": 8, "memory": "32Gi"}
    }


def test_zero_gpus_leaves_the_count_out_but_keeps_the_jobs_cpus():
    declared = PortableResources(num_of_gpus=1, num_of_cpus=4)

    assert nvflare_resource_spec(JobResources(num_gpus=0), declared) == {"@default": {"num_of_cpus": 4}}


# ── FLARE agrees ──────────────────────────────────────────────────────────────────


def _meta(resources: JobResources, declared: PortableResources | None = None) -> dict:
    return {
        "name": "m",
        "resource_spec": nvflare_resource_spec(resources, declared or PortableResources()),
        "deploy_map": {"app": ["server", *TRUSTS]},
        "min_clients": len(TRUSTS),
        "mandatory_clients": TRUSTS,
    }


@pytest.mark.parametrize(
    ("resources", "declared"),
    [
        (JobResources(num_gpus=0), None),
        (JobResources(num_gpus=2), None),
        (JobResources(num_gpus=1), PortableResources(num_of_cpus=8, memory="32Gi")),
    ],
)
def test_nvflares_job_validator_accepts_the_resource_spec(resources, declared):
    """The server runs these on every submitted job and refuses it on a ValueError."""
    meta = _meta(resources, declared)

    JobMetaValidator._validate_resource("job", meta)
    validate_portable_resource_conflicts(meta)


@pytest.mark.parametrize(
    ("site_gpus", "site_mem", "resources", "admitted"),
    [
        (0, 0, JobResources(num_gpus=0), True),
        (0, 0, JobResources(num_gpus=1), False),
        (2, 16, JobResources(num_gpus=1), True),
        (2, 16, JobResources(num_gpus=3), False),
    ],
)
def test_a_sites_gpu_manager_admits_exactly_what_it_can_provide(site_gpus, site_mem, resources, admitted):
    """What the scheduler sends each site, judged by the manager our kits configure from NUM_AVAILABLE_GPUS.

    The job asks for no memory per GPU, which the manager counts as 0, so a site that declares 16 GiB per GPU
    still admits it.
    """
    manager = GPUResourceManager(num_of_gpus=site_gpus, mem_per_gpu_in_GiB=site_mem, ignore_host=True)
    requirement = get_resource_manager_spec(_meta(resources), "Trust_1")

    assert manager._check_required_resource_available(requirement) is admitted


def test_a_container_launcher_turns_the_default_block_into_each_sites_request():
    """What FLARE's Docker and Kubernetes launchers read for a site, e.g. a GPU job pod (FLIP#1428)."""
    meta = _meta(JobResources(num_gpus=1), PortableResources(num_of_cpus=8, memory="32Gi"))

    assert get_portable_resource_spec(meta, "Trust_1") == {"num_of_gpus": 1, "num_of_cpus": 8, "memory": "32Gi"}
