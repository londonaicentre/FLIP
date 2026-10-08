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

"""Secret field ownership under Helm 4's server-side apply (FLIP#1366).

`patch-kit-secrets` writes the kit's per-trust keys into the chart's Secret with field
manager ``kubectl-patch``. Helm 4 applies the release server-side, so an upgrade that would
*change* one of those keys is refused:

    Apply failed with 3 conflicts: conflict with "kubectl-patch" using v1: .data.trust-api-key

The refusal only fires when the live value and the chart's rendered value disagree, which is
why the fix is to keep them in step (``align_values_secrets``) rather than to force the
apply. These tests pin that choice, including the security property that makes forcing
unacceptable: the chart renders the three keys from ``values-secrets.yaml``, and
``templates/secrets.yaml`` omits an empty slot, so a forced apply from a stale or
half-filled file would revert live trust credentials on a routine redeploy.
"""

import importlib.util
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

CHART_DIR = Path(__file__).resolve().parents[1]
_SCRIPT = CHART_DIR / "sync_k8s_kit.py"
_spec = importlib.util.spec_from_file_location("sync_k8s_kit", _SCRIPT)
assert _spec
assert _spec.loader
sync_k8s_kit = importlib.util.module_from_spec(_spec)
sys.modules["sync_k8s_kit"] = sync_k8s_kit
_spec.loader.exec_module(sync_k8s_kit)

KIT_OWNED_KEYS = ("aes-key-base64", "trust-api-key", "trust-internal-service-key")

GENERATED = """\
secrets:
  create: True
  data:
    # openssl rand -base64 32
    aes-key-base64: "stale-aes"
    trust-api-key: "stale-api"
    trust-internal-service-key: "stale-internal"
    trust-internal-service-key-header: X-Trust-Internal-Service-Key
    xnat-admin-password: untouched-xnat
    orthanc-registered-users: "{\\"admin\\": \\"untouched-orthanc\\"}"
"""

PATCHED = {
    "aes-key-base64": "live-aes",
    "trust-api-key": "live-api",
    "trust-internal-service-key": "live-internal",
    "trust-internal-service-key-header": "X-Trust-Internal-Service-Key",
}


def _values_file(tmp_path: Path, body: str = GENERATED) -> Path:
    path = tmp_path / "values-secrets.yaml"
    path.write_text(body)
    path.chmod(0o600)
    return path


def _slots(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        if ":" in line and line.startswith("    "):
            key, _, value = line.strip().partition(":")
            out[key] = value.strip().strip('"')
    return out


def test_the_patched_values_reach_the_chart_so_helm_has_nothing_to_change(tmp_path):
    """The whole fix: after a patch, helm's apply is a no-op on these fields."""
    path = _values_file(tmp_path)
    aligned = sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert sorted(aligned) == sorted(KIT_OWNED_KEYS)  # the header already matched
    slots = _slots(path)
    for key in KIT_OWNED_KEYS:
        assert slots[key] == PATCHED[key], f"{key} would still differ — the upgrade conflicts"


def test_slots_the_kit_does_not_own_are_left_exactly_as_they_were(tmp_path):
    """A kit sync must never disturb the hand-filled XNAT / Orthanc credentials."""
    path = _values_file(tmp_path)
    sync_k8s_kit.align_values_secrets(path, PATCHED)

    slots = _slots(path)
    assert slots["xnat-admin-password"] == "untouched-xnat"  # pragma: allowlist secret
    assert "untouched-orthanc" in slots["orthanc-registered-users"]
    assert "# openssl rand -base64 32" in path.read_text()


def test_the_file_mode_stays_0600(tmp_path):
    path = _values_file(tmp_path)
    sync_k8s_kit.align_values_secrets(path, PATCHED)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_missing_slot_is_added_rather_than_left_for_the_chart_to_omit(tmp_path):
    """templates/secrets.yaml omits an empty slot, and the pod then dies on a missing key."""
    path = _values_file(tmp_path, 'secrets:\n  create: True\n  data:\n    xnat-admin-password: "keep"\n')
    aligned = sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert set(KIT_OWNED_KEYS) <= set(aligned)
    slots = _slots(path)
    assert slots["trust-api-key"] == "live-api"
    assert slots["xnat-admin-password"] == "keep"  # pragma: allowlist secret


def test_already_aligned_values_are_a_no_op(tmp_path):
    path = _values_file(tmp_path)
    sync_k8s_kit.align_values_secrets(path, PATCHED)
    before = path.read_text()
    assert sync_k8s_kit.align_values_secrets(path, PATCHED) == []
    assert path.read_text() == before


def test_no_values_secrets_file_is_not_an_error(tmp_path):
    """Without it the chart runs secrets.create=false and manages no Secret of its own."""
    assert sync_k8s_kit.align_values_secrets(tmp_path / "values-secrets.yaml", PATCHED) == []


def test_a_value_carrying_a_quote_or_a_backslash_round_trips_as_yaml(tmp_path):
    """Both writers of this file must quote identically, so the value survives the YAML parser.

    `generate_values.py` renders the whole file with `yaml_quote`; `align_values_secrets`
    patches single lines. Hand-rolled `key: "<value>"` quoting produced invalid YAML — or
    worse, a silently truncated credential — for a value containing `"` or `\\`.
    """
    awkward = 'a"b\\c"d'
    path = _values_file(tmp_path)
    sync_k8s_kit.align_values_secrets(path, {**PATCHED, "trust-api-key": awkward})

    parsed = yaml.safe_load(path.read_text())
    assert parsed["secrets"]["data"]["trust-api-key"] == awkward
    # The slots the kit also owns are still intact after the awkward neighbour.
    assert parsed["secrets"]["data"]["aes-key-base64"] == "live-aes"
    assert parsed["secrets"]["data"]["xnat-admin-password"] == "untouched-xnat"  # pragma: allowlist secret


def test_the_same_key_name_outside_secrets_data_is_left_alone(tmp_path):
    """Matching is scoped to the `secrets:` -> `data:` block, not the first line in the file.

    A first-match regex would rewrite a same-named key in another section — writing a live
    credential into a slot the chart never reads, and leaving the real slot stale, so the
    SSA conflict this whole change exists to remove would still fire.
    """
    body = (
        "someOtherComponent:\n"
        "  env:\n"
        '    trust-api-key: "not-the-secret"\n'
        "secrets:\n"
        "  create: True\n"
        "  data:\n"
        '    aes-key-base64: "stale-aes"\n'
        '    trust-api-key: "stale-api"\n'
        '    trust-internal-service-key: "stale-internal"\n'
        "trailing:\n"
        '  trust-api-key: "also-not-the-secret"\n'
    )
    path = _values_file(tmp_path, body)
    sync_k8s_kit.align_values_secrets(path, PATCHED)

    parsed = yaml.safe_load(path.read_text())
    assert parsed["secrets"]["data"]["trust-api-key"] == "live-api"
    assert parsed["someOtherComponent"]["env"]["trust-api-key"] == "not-the-secret"
    assert parsed["trailing"]["trust-api-key"] == "also-not-the-secret"


def test_an_inserted_slot_takes_the_indent_the_block_already_uses(tmp_path):
    """The insert used to assume a two-space `data:` with four-space entries."""
    body = "secrets:\n    create: True\n    data:\n        xnat-admin-password: keep\n"
    path = _values_file(tmp_path, body)
    sync_k8s_kit.align_values_secrets(path, PATCHED)

    parsed = yaml.safe_load(path.read_text())
    assert parsed["secrets"]["data"]["trust-api-key"] == "live-api"
    assert parsed["secrets"]["data"]["xnat-admin-password"] == "keep"  # pragma: allowlist secret
    assert "        trust-api-key: live-api" in path.read_text().splitlines()


def test_a_file_with_no_secrets_data_block_is_left_untouched(tmp_path):
    """Better to realign nothing than to guess where the chart reads its Secret from."""
    body = 'someOtherComponent:\n  trust-api-key: "not-the-secret"\n'
    path = _values_file(tmp_path, body)

    assert sync_k8s_kit.align_values_secrets(path, PATCHED) == []
    assert path.read_text() == body


def test_the_secret_is_merge_patched_never_server_side_applied(monkeypatch):
    """A partial server-side apply prunes every key the manager owns and did not list —
    it would empty the XNAT/OMOP/Orthanc slots out of the same Secret."""
    calls: list[list[str]] = []

    class _Result:
        returncode = 0
        stdout = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return _Result()

    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", fake_run)
    sync_k8s_kit.patch_k8s_secret("trust-release-flip-trust-secrets", "flip-trust", PATCHED, "trust-release")

    flat = [" ".join(call) for call in calls]
    assert any("patch secret" in c and "--type merge" in c for c in flat), flat
    assert not any("--server-side" in c or "--force-conflicts" in c for c in flat), flat


@pytest.mark.skipif(shutil.which("make") is None, reason="make is not installed")
def test_deploy_does_not_force_server_side_apply_conflicts():
    """Forcing would overwrite live per-trust keys with whatever values-secrets.yaml holds."""
    out = subprocess.run(
        ["make", "-n", "-C", str(CHART_DIR), "deploy", "PROD=stag"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
    upgrade = next(line for line in out.splitlines() if "helm upgrade --install" in line)
    assert "--force-conflicts" not in out, upgrade
    assert "--take-ownership" not in out, upgrade


def test_the_chart_omits_a_secret_key_whose_slot_is_empty():
    """The property that makes a forced apply dangerous, asserted on the template itself."""
    template = (CHART_DIR / "templates" / "secrets.yaml").read_text()
    for key in KIT_OWNED_KEYS:
        assert f'{{{{- if index .Values.secrets.data "{key}" }}}}' in template, key


# ── Hand-edited shapes (FLIP#1366 review) ────────────────────────────────────────────
# `generate_values.py` never writes these, but it tells operators to fill missing slots by
# hand, so they are expected. Each one used to leave the file with the key twice: YAML keeps
# the LAST copy, so the operator was told "Realigned" while the chart still rendered the old
# value and the next `helm upgrade` died on the same SSA conflict. Every case is therefore
# asserted through `yaml.safe_load` — what the chart actually reads — plus an occurrence
# count, because a file can parse to the right value and still carry a duplicate.


def _data(path: Path) -> dict:
    return yaml.safe_load(path.read_text())["secrets"]["data"]


def _occurrences(path: Path, key: str) -> int:
    """How many times the key owns a line, comments excluded."""
    return sum(
        1
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#") and re.match(rf"^\s*{re.escape(key)}:(\s.*)?$", line)
    )


def test_an_empty_slot_is_filled_in_place_not_duplicated(tmp_path):
    """`trust-api-key:` with nothing after the colon is the shape an operator leaves behind.

    The old key pattern required whitespace after the colon, so an empty slot looked absent:
    a second copy went in above it and `yaml.safe_load` read the empty one — `None`.
    """
    path = _values_file(tmp_path, "secrets:\n  create: True\n  data:\n    trust-api-key:\n")
    aligned = sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert "trust-api-key" in aligned
    assert _occurrences(path, "trust-api-key") == 1, path.read_text()
    assert _data(path)["trust-api-key"] == "live-api"


def test_a_comment_inside_the_block_does_not_hide_the_slot_below_it(tmp_path):
    """A note at column 0 used to end the block, hiding the real slot and leaving it stale."""
    body = (
        "secrets:\n"
        "  create: True\n"
        "  data:\n"
        "# filled in by hand after registration\n"
        '    trust-api-key: "stale"\n'
        "    # and one at the data indent\n"
        '    aes-key-base64: "stale-aes"\n'
    )
    path = _values_file(tmp_path, body)
    sync_k8s_kit.align_values_secrets(path, PATCHED)

    for key in ("trust-api-key", "aes-key-base64"):
        assert _occurrences(path, key) == 1, path.read_text()
    assert _data(path)["trust-api-key"] == "live-api"
    assert _data(path)["aes-key-base64"] == "live-aes"
    assert "# filled in by hand after registration" in path.read_text()


def test_a_trailing_comment_on_the_header_still_finds_the_block(tmp_path):
    """`secrets:  # generated` made the whole block unfindable — a silent no-op realign."""
    body = (
        "secrets:  # generated by generate_values.py\n"
        "  create: True\n"
        "  data:   # per-trust keys below\n"
        '    trust-api-key: "stale"\n'
    )
    path = _values_file(tmp_path, body)
    aligned = sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert "trust-api-key" in aligned
    assert _occurrences(path, "trust-api-key") == 1, path.read_text()
    assert _data(path)["trust-api-key"] == "live-api"


def test_a_key_left_in_the_block_twice_is_refused_and_the_file_untouched(tmp_path):
    """Last-one-wins means a duplicate cannot be reported any other way than refusing."""
    body = 'secrets:\n  create: True\n  data:\n    trust-api-key: "one"\n    trust-api-key: "two"\n'
    path = _values_file(tmp_path, body)

    with pytest.raises(sync_k8s_kit.ValuesSecretsError) as excinfo:
        sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert str(path) in str(excinfo.value), excinfo.value
    assert path.read_text() == body, "the file must be left exactly as it was"


def test_a_block_scalar_slot_is_refused_rather_than_orphaned(tmp_path):
    """Rewriting the `key: |` line alone leaves its continuation parsing as a credential."""
    body = "secrets:\n  create: True\n  data:\n    trust-api-key: |\n      some-multiline\n"
    path = _values_file(tmp_path, body)

    with pytest.raises(sync_k8s_kit.ValuesSecretsError) as excinfo:
        sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert str(path) in str(excinfo.value), excinfo.value
    assert path.read_text() == body


def test_a_file_without_the_block_warns_that_the_next_upgrade_will_conflict(tmp_path, capsys):
    """Returning [] silently told the operator nothing had to be realigned — it did."""
    path = _values_file(tmp_path, 'someOtherComponent:\n  trust-api-key: "not-the-secret"\n')

    assert sync_k8s_kit.align_values_secrets(path, PATCHED) == []

    out = capsys.readouterr().out
    assert str(path) in out, out
    assert "helm upgrade" in out, out


def test_the_file_is_replaced_atomically_never_truncated_in_place(tmp_path, monkeypatch):
    """This runs AFTER the cluster patch: a crash mid-write must not empty the file.

    `path.write_text` truncates first, so an interruption left a zero-byte credentials file —
    and `templates/secrets.yaml` omits an empty slot, so the next deploy would roll out pods
    with no trust credentials at all. Asserted by failing the write and checking the original
    survives intact, and by pinning the `os.replace` the atomicity rests on.
    """
    path = _values_file(tmp_path)
    before = path.read_text()
    replaced: list[tuple] = []

    real_replace = sync_k8s_kit.os.replace

    def boom(src, dst):
        replaced.append((src, dst))
        raise OSError("interrupted")

    monkeypatch.setattr(sync_k8s_kit.os, "replace", boom)
    with pytest.raises(OSError, match="interrupted"):
        sync_k8s_kit.align_values_secrets(path, PATCHED)

    assert replaced, "the write did not go through os.replace — it is not atomic"
    assert path.read_text() == before, "the original was truncated before the new content landed"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "values-secrets.yaml"]
    assert leftovers == [], f"a temp file was left behind: {leftovers}"

    # And the successful path really does rename into place, preserving the mode.
    monkeypatch.setattr(sync_k8s_kit.os, "replace", real_replace)
    sync_k8s_kit.align_values_secrets(path, PATCHED)
    assert _data(path)["trust-api-key"] == "live-api"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_main_realigns_the_values_file_when_it_patches_the_secret(tmp_path, monkeypatch, capsys):
    """Nothing exercised the call from `main()`: deleting it left all the tests green.

    kubectl is monkeypatched so the namespace and the Secret both "exist" — the path on which
    `main` patches the cluster and must then realign the generated values file.
    """
    kit = tmp_path / "trust" / ".env.ABC.development"
    kit.parent.mkdir(parents=True)
    kit.write_text(
        "TRUST_API_KEY=live-api\n"
        "TRUST_INTERNAL_SERVICE_KEY=live-internal\n"
        "TRUST_INTERNAL_SERVICE_KEY_HEADER=X-Trust-Internal-Service-Key\n"
        "AES_KEY_BASE64=live-aes\n"
        "CENTRAL_HUB_API_URL=http://hub.example.com/api\n"
    )
    out_dir = tmp_path / "chart"
    out_dir.mkdir()
    values = out_dir / sync_k8s_kit.VALUES_SECRETS_NAME
    values.write_text(GENERATED)
    values.chmod(0o600)

    class _Ok:
        returncode = 0
        stdout = ""

    monkeypatch.setattr(sync_k8s_kit, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sync_k8s_kit.subprocess, "run", lambda *a, **k: _Ok())

    sync_k8s_kit.main(
        code="ABC",
        env="development",
        namespace="flip-trust",
        secret_name="trust-release-flip-trust-secrets",
        output_dir=out_dir,
        aws_region="eu-west-2",
        apply_secret=True,
        write_override=False,
    )

    data = _data(values)
    for key in KIT_OWNED_KEYS:
        assert data[key] == PATCHED[key], f"main() did not realign {key}"
        assert _occurrences(values, key) == 1
    assert "Realigned" in capsys.readouterr().out
