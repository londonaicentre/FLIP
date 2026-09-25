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

"""Unit tests for scripts/generate_values.py's env → Helm-values mapping, for the one
value whose absence the chart refuses to render: flClient.kitHostPath (FLIP#965/#1009).

generate_values.py and sync_k8s_kit.py both turn a kit file into a Helm override, so they
must agree on what a kit without FL_KIT_DIR means — the canonical /opt/flip/fl-kit that
every shipped kit sets and the default `make stage-kit` writes to — rather than one
falling back and the other dropping the key.
"""

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "generate_values.py"
_spec = importlib.util.spec_from_file_location("generate_values", _SCRIPT)
generate_values = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(generate_values)


_KIT = {
    "TRUST_NAME": "Kubernetes Trust",
    "TRUST_NUMBER": "2",
    "CENTRAL_HUB_API_URL": "https://stag.flip.aicentre.co.uk/api",
    "FL_BACKEND": "Flower",
    "FL_KIT_DIR": "/srv/kits/Trust_K8s",
}


def kit_host_path(env):
    overrides, _secrets = generate_values.build_values(env)
    return overrides["flClient"]["kitHostPath"]


def test_kit_host_path_comes_from_fl_kit_dir():
    assert kit_host_path(_KIT) == "/srv/kits/Trust_K8s"


def test_kit_host_path_falls_back_to_the_canonical_path_when_fl_kit_dir_is_absent():
    """The chart's `required` guard would otherwise refuse the render for a kit that
    simply never carried the field — the same default sync_k8s_kit.py renders."""
    kit = {k: v for k, v in _KIT.items() if k != "FL_KIT_DIR"}
    assert kit_host_path(kit) == generate_values.DEFAULT_KIT_HOST_PATH == "/opt/flip/fl-kit"


def test_kit_host_path_falls_back_when_fl_kit_dir_is_blank():
    """A commented-out or cleared value must not surface as an empty path either."""
    assert kit_host_path({**_KIT, "FL_KIT_DIR": "   "}) == "/opt/flip/fl-kit"


def test_fallback_does_not_touch_the_other_mapped_values():
    overrides, _secrets = generate_values.build_values({k: v for k, v in _KIT.items() if k != "FL_KIT_DIR"})
    assert overrides["flBackend"] == "flower"
    assert overrides["trustNumber"] == "2"
    assert overrides["trustApi"]["env"]["CENTRAL_HUB_API_URL"] == _KIT["CENTRAL_HUB_API_URL"]


def test_both_override_generators_share_the_fallback():
    """sync_k8s_kit.render_override is the other writer of flClient.kitHostPath."""
    sync_script = Path(__file__).resolve().parents[2] / "sync_k8s_kit.py"
    sync_spec = importlib.util.spec_from_file_location("sync_k8s_kit", sync_script)
    sync_k8s_kit = importlib.util.module_from_spec(sync_spec)
    sync_spec.loader.exec_module(sync_k8s_kit)
    out = sync_k8s_kit.render_override({"FL_BACKEND": "nvflare"}, "Trust_K8s", "eu-west-2")
    assert f"\nflClient:\n  kitHostPath: {generate_values.DEFAULT_KIT_HOST_PATH}\n" in out


def test_the_kits_docker_tag_becomes_the_charts_release_pin():
    """The kit's Hub-shared DOCKER_TAG is the release the site runs (FLIP#1204); before this it
    never reached Helm, and a Kubernetes site stayed on nine hand-edited `image.tag: stag`."""
    overrides, _secrets = generate_values.build_values({**_KIT, "DOCKER_TAG": "v0.6.0"})
    assert overrides["global"]["image"]["tag"] == "v0.6.0"


def test_a_kit_without_docker_tag_leaves_the_release_pin_alone():
    """Dev kits keep the Hub-shared block commented out; the chart's own per-service tags apply."""
    overrides, _secrets = generate_values.build_values(_KIT)
    assert "global" not in overrides


def test_the_kits_image_opt_outs_become_chart_pins():
    """OMOP_DB_TAG / ORTHANC_TAG / XNAT_TAG hold one image back from DOCKER_TAG on compose
    (`${OMOP_DB_TAG:-${DOCKER_TAG}}`); the chart's `<svc>.image.pin` is their twin."""
    pins = {"OMOP_DB_TAG": "latest", "ORTHANC_TAG": "sha-2bf07b9", "XNAT_TAG": "v0.6.0"}
    kit = {**_KIT, "DOCKER_TAG": "sha-badcff1", **pins}
    overrides, _secrets = generate_values.build_values(kit)
    assert overrides["omopDb"]["image"]["pin"] == "latest"
    assert overrides["orthanc"]["image"]["pin"] == "sha-2bf07b9"
    assert overrides["xnat"]["image"]["pin"] == "v0.6.0"
    unpinned, _secrets = generate_values.build_values({**_KIT, "DOCKER_TAG": "v0.6.0"})
    assert "pin" not in unpinned.get("omopDb", {}).get("image", {})
    assert "orthanc" not in unpinned or "image" not in unpinned["orthanc"]


def test_the_fl_client_follows_docker_fl_tag_only_when_it_names_an_immutable_image():
    """Compose runs the client at DOCKER_FL_TAG outright; the chart does so for a release or a
    CI sha- tag, and leaves a dev kit's locally built `dev` / the floating `stag` alone."""
    for tag in ("v0.6.1", "v0.6.1-rc.1", "sha-03fdb61"):
        overrides, _secrets = generate_values.build_values({**_KIT, "DOCKER_TAG": "v0.6.1", "DOCKER_FL_TAG": tag})
        assert overrides["flClient"]["image"]["pin"] == tag, tag
    for tag in ("dev", "stag", "prod", "latest", ""):
        overrides, _secrets = generate_values.build_values({**_KIT, "DOCKER_TAG": "stag", "DOCKER_FL_TAG": tag})
        assert "image" not in overrides["flClient"], tag


# ── Renamed kit variables ────────────────────────────────────────────────
# The env-var renames in ``generate_values.py`` are announced, not silent.
#
# Dropping a name from ``ENV_VAR_MAP`` is silent by construction: the variable
# stops reaching the generated values and the chart default applies instead. For a
# data-version pin that is a *changed deployed dataset* rather than an error, and
# nothing in the install would report it — which is why ``RENAMED_ENV_VARS``
# exists and why it is checked here rather than left to a reader of the diff.


def test_the_old_pin_is_no_longer_read():
    overrides, _ = generate_values.build_values({"OMOP_DATA_VERSION": "20260729"})

    assert "trustData" not in overrides


def test_the_old_pin_being_set_is_reported(capsys):
    generate_values.build_values({"OMOP_DATA_VERSION": "20260729"})

    warning = capsys.readouterr().err
    assert "OMOP_DATA_VERSION" in warning
    assert "TRUST_DATA_VERSION" in warning
    assert "20260729" in warning


def test_the_new_pin_maps_through(capsys):
    overrides, _ = generate_values.build_values({"TRUST_DATA_VERSION": "20260901"})

    assert overrides["trustData"]["version"] == "20260901"
    assert capsys.readouterr().err == ""


def test_the_new_pin_wins_and_says_so_when_both_are_set(capsys):
    overrides, _ = generate_values.build_values({"OMOP_DATA_VERSION": "20260729", "TRUST_DATA_VERSION": "20260901"})

    assert overrides["trustData"]["version"] == "20260901"
    assert "in effect" in capsys.readouterr().err


def test_every_rename_target_is_a_name_the_script_actually_reads():
    """A rename pointing at a name nothing maps would send operators to a dead variable."""
    unmapped = set(generate_values.RENAMED_ENV_VARS.values()) - set(generate_values.ENV_VAR_MAP)

    assert unmapped == set(), f"RENAMED_ENV_VARS points at names ENV_VAR_MAP does not carry: {sorted(unmapped)}"


def test_no_rename_target_is_itself_retired():
    overlap = set(generate_values.RENAMED_ENV_VARS) & set(generate_values.ENV_VAR_MAP)

    assert overlap == set(), f"these are both retired and mapped: {sorted(overlap)}"
