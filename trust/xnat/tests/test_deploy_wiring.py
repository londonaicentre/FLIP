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
"""Tests for the layers that carry configuration *to* configure-xnat.sh (FLIP#993).

test_configure_pacs.py proves the script does the right thing with the environment it is given.
Nothing proved it is given that environment, and every bug found while deploying this change lived
in the gap: the Makefile defaulted the web port to a literal that collided with a second trust, it
exported an empty AE title, and the compose file's ``${VAR:-default}`` cancelled the script's own
fail-loud guard. None of those are visible from inside the script.

These run make and read the deployment files; they never invoke docker.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

XNAT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = XNAT_DIR.parents[1]
SCRIPT = XNAT_DIR / "xnat" / "config" / "configure-xnat.sh"
COMPOSE = XNAT_DIR / "docker-compose-stack.yml"
REAL_PACS_OVERLAY = XNAT_DIR / "docker-compose-stack.real-pacs.yml"
INIT_JOB = REPO_ROOT / "deploy" / "providers" / "kubernetes" / "templates" / "xnat-init-job.yaml"


def make_vars(*names: str, **overrides: str) -> dict[str, str]:
    """Resolves Make variables by evaluating them in a throwaway target.

    ``--eval`` appends the target after the makefiles are read, so the values are the ones a real
    deploy would use. Only variables are expanded — no recipe from the Makefile itself runs, so
    this needs neither docker nor a swarm.

    Args:
        *names: Make variable names to resolve.
        **overrides: Command-line variable assignments (``KIT=GSTT``), as an operator would pass.

    Returns:
        dict[str, str]: Resolved value per requested name.
    """
    # One recipe line per variable: make splits a recipe on newlines, so a single multi-line echo
    # reaches the shell as several unterminated commands.
    probe = "__probe:\n" + "".join(f"\t@echo '{n}=$({n})'\n" for n in names)
    result = subprocess.run(
        ["make", "--eval", probe, "__probe", *(f"{k}={v}" for k, v in overrides.items())],
        cwd=XNAT_DIR,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    resolved = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key in names:
            resolved[key] = value
    assert set(resolved) == set(names), f"unresolved: {set(names) - set(resolved)}"
    return resolved


def configurable_variables() -> set[str]:
    """Names the script reads with a bare-dash default — its operator-configurable knobs.

    ``${VAR-default}`` rather than ``${VAR:-default}`` is the script's marker for a value an
    operator may set and which must never silently fall back when set empty.
    """
    return set(re.findall(r'^([A-Z0-9_]+)="\$\{\1-', SCRIPT.read_text(), flags=re.MULTILINE))


def compose_environment() -> dict[str, str]:
    """The xnat-web service's ``environment:`` list, as {name: raw value}."""
    entries = {}
    for line in COMPOSE.read_text().splitlines():
        match = re.match(r"\s*-\s+([A-Z0-9_]+)=(.*)$", line)
        if match:
            entries[match.group(1)] = match.group(2)
    return entries


def test_every_script_knob_is_wired_through_compose():
    """A knob the compose file does not pass is one a kit file can set with no effect."""
    missing = configurable_variables() - set(compose_environment())
    assert not missing, f"configure-xnat.sh reads {sorted(missing)}, but docker-compose-stack.yml never passes them"


def test_every_script_knob_is_wired_through_the_helm_init_job():
    """Same contract on the Kubernetes path, which runs the same script from the same image.

    The two deployments drifted apart once already: imaging-api was given the DICOM port but not
    the AE title, so k8s trusts ran with a receiver XNAT would not answer to.
    """
    declared = set(re.findall(r"- name: ([A-Z0-9_]+)", INIT_JOB.read_text()))
    missing = configurable_variables() - declared
    assert not missing, f"configure-xnat.sh reads {sorted(missing)}, but xnat-init-job.yaml never sets them"


def test_compose_defaults_do_not_cancel_the_scripts_empty_check():
    """``${VAR:-default}`` substitutes for a set-but-empty variable; ``${VAR-default}`` does not.

    With the colon form an operator who writes ``PACS_HOST=`` in a kit file gets the mocked Orthanc
    silently, which is precisely the fail-loud behaviour the script's guards exist to provide.
    """
    offenders = {
        name: value
        for name, value in compose_environment().items()
        if name in configurable_variables() and ":-" in value
    }
    assert not offenders, f"these cancel the script's guard by defaulting an empty value: {sorted(offenders)}"


def test_published_ports_are_flat_references():
    """``docker stack deploy`` rejects a nested default in a ports entry.

    ``${XNAT_WEB_PORT:-${XNAT_PORT}}:8080`` fails the whole deploy with "Does not match format
    'ports'" — the fallback has to be resolved by the Makefile, not by compose.
    """
    for compose in (COMPOSE, REAL_PACS_OVERLAY):
        in_ports = False
        for line in compose.read_text().splitlines():
            if re.match(r"\s*ports:\s*$", line):
                in_ports = True
                continue
            if not in_ports:
                continue
            # Comments and blank lines sit between `ports:` and its entries; only a key at the same
            # or lower indent ends the block.
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if not re.match(r"\s*-\s", line):
                in_ports = False
                continue
            assert not re.search(r"\$\{[^}]*\$\{", line), (
                f"nested substitution in a ports entry of {compose.name}: {line.strip()}"
            )


def test_web_port_defaults_to_the_dicom_port():
    """They were one variable until this change, so a kit that sets only XNAT_PORT must still work.

    Defaulting to a literal instead broke the second trust on a host: KCH publishing on 8104 rather
    than its own 8106 collides with a running GSTT.
    """
    resolved = make_vars("XNAT_PORT_EFFECTIVE", "XNAT_WEB_PORT_EFFECTIVE", XNAT_PORT="8106")
    assert resolved["XNAT_WEB_PORT_EFFECTIVE"] == "8106"


def test_web_port_can_be_separated_from_the_dicom_port():
    """Publishing the receiver on the host needs the two on different ports."""
    resolved = make_vars("XNAT_PORT_EFFECTIVE", "XNAT_WEB_PORT_EFFECTIVE", XNAT_PORT="8104", XNAT_WEB_PORT="8080")
    assert (resolved["XNAT_PORT_EFFECTIVE"], resolved["XNAT_WEB_PORT_EFFECTIVE"]) == ("8104", "8080")


def test_ae_title_defaults_when_unset():
    """An unset AE title must reach the script as XNAT, not as an empty string."""
    assert make_vars("XNAT_AETITLE_EFFECTIVE")["XNAT_AETITLE_EFFECTIVE"] == "XNAT"


def test_ae_title_set_empty_stays_empty():
    """The empty value has to survive make so the script's guard is the thing that reports it.

    Substituting the default here would hand the script a configured-looking value and move the
    failure to whenever the PACS first tries to C-STORE back — a wrong AE title is not detectable
    by any earlier step.
    """
    assert make_vars("XNAT_AETITLE_EFFECTIVE", XNAT_AETITLE="")["XNAT_AETITLE_EFFECTIVE"] == ""


def run_xnat_reset(tmp_path, **overrides: str) -> subprocess.CompletedProcess:
    """Runs the real xnat-reset recipe with its destructive half neutered.

    ``make -n`` is no use here: the port guards are shell ``if``s inside the recipe, so a dry run
    prints them without ever deciding anything. So the recipe runs for real, but against a data
    directory under tmp_path and with ``sudo`` stubbed to a no-op — a guard that stops working
    then creates a directory in a temp dir instead of deleting a trust's XNAT archive.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_sudo = bin_dir / "sudo"
    fake_sudo.write_text("#!/bin/sh\nexit 0\n")
    fake_sudo.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        [
            "make",
            "xnat-reset",
            "KIT=GSTT",
            f"XNAT_DATA_DIR={tmp_path / 'data'}",
            *(f"{k}={v}" for k, v in overrides.items()),
        ],
        cwd=XNAT_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("port_var", ["XNAT_PORT", "XNAT_WEB_PORT"])
def test_non_numeric_ports_are_refused_before_deploy(tmp_path, port_var):
    """A non-numeric port reaches docker as an unparseable ports entry, after the data wipe."""
    result = run_xnat_reset(tmp_path, **{port_var: "80a4"})
    assert result.returncode != 0, f"a non-numeric {port_var} was accepted"
    assert port_var in result.stdout + result.stderr


def test_valid_ports_are_accepted(tmp_path):
    """The negative cases above are only meaningful if the guard lets a good configuration through."""
    result = run_xnat_reset(tmp_path, XNAT_PORT="8104", XNAT_WEB_PORT="8080")
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_pacs_refuses_to_publish_both_services_on_one_port(tmp_path):
    """REAL_PACS=true publishes the DICOM receiver alongside the web UI; one port cannot serve both."""
    result = run_xnat_reset(tmp_path, REAL_PACS="true", XNAT_PORT="8104", XNAT_WEB_PORT="8104")
    assert result.returncode != 0, "the collision was accepted"
    assert "must differ" in result.stdout + result.stderr


def test_one_port_is_fine_without_real_pacs(tmp_path):
    """Sharing the number is the normal case: only the web UI is published."""
    result = run_xnat_reset(tmp_path, XNAT_PORT="8104", XNAT_WEB_PORT="8104")
    assert result.returncode == 0, result.stdout + result.stderr


def test_real_pacs_overlay_is_only_added_on_request():
    """Publishing the receiver is an opening; the mocked PACS reaches it over the container network."""
    without = make_vars("STACK_FILES")["STACK_FILES"]
    with_overlay = make_vars("STACK_FILES", REAL_PACS="true")["STACK_FILES"]
    assert "real-pacs" not in without
    assert "docker-compose-stack.real-pacs.yml" in with_overlay
