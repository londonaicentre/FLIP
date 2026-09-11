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
Tests for the XNAT data type setup scripts.

``setup-datatypes.sh`` enables the data types XNAT ships but leaves inert (slide microscopy, so a
trust can archive whole-slide images). It has no REST API to call, so it drives the admin UI's own
setup wizard: read the form a page rendered, put it back with the next step's submit button.

That form handling is the part worth pinning. Everything it depends on -- which form on the page,
which inputs a browser would submit -- is invisible in a passing run and fails quietly: pick up the
wrong form and the script posts an empty body to the site search and reports success. So these tests
source the script (its work is behind a ``main`` guard) and call the parser directly against
fixtures shaped like the real pages, with no XNAT and no network.

The end of the chain is covered elsewhere and differently: ``verify-datatypes.sh`` reads the five
tables the setup writes, and ``setup-datatypes.sh`` execs it rather than reporting its own success.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(__file__).resolve().parents[1] / "xnat" / "config"
SETUP_SCRIPT = CONFIG_DIR / "setup-datatypes.sh"
VERIFY_SCRIPT = CONFIG_DIR / "verify-datatypes.sh"
COMMON_SCRIPT = CONFIG_DIR / "datatypes-common.sh"
XNAT_MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"

# Sourcing the script runs the `: "${VAR:?}"` requirement checks at the top of it and of
# datatypes-common.sh. The values are never used -- nothing here logs in or opens a connection.
STUB_ENV = {
    "XNAT_ADMIN_USER": "admin",
    "XNAT_ADMIN_PASSWORD": "unused",  # pragma: allowlist secret
    "XNAT_DATASOURCE_NAME": "xnat",
    "XNAT_DATASOURCE_USERNAME": "xnat",
    "XNAT_DATASOURCE_PASSWORD": "unused",  # pragma: allowlist secret
}

# A page shaped like XNAT's admin screens: the site-wide quick search comes first, the form we want
# is second, and a third follows it. Only the middle one may be picked up.
PAGE = """
<html><body>
<form id="quickSearchForm" method="post" action="/app/action/QuickSearchAction">
  <input type="hidden" name="decoy" value="wrong-form">
  <input type="text" name="search_value" value="">
</form>
<form name="form1" method="post" action="/app/action/ManageDataTypes">
  <input type="hidden" name="element[0]/element_name" value="xnat:ctSessionData">
  <input type="text" value="CT" name="element[0]/code">
  <input type="checkbox" name="element[0]/accessible" value="true" checked>
  <input type="checkbox" name="element[1]/accessible" value="true">
  <input type="radio" name="secure" value="0">
  <input type="radio" name="secure" value="1" CHECKED/>
  <INPUT TYPE="hidden" NAME="popup" VALUE="true">
  <input type="hidden" name="spaced" value="SM Sessions">
  <input type="hidden" name="escaped" value="a &amp; b">
  <input type="submit" name="save" value="Save">
</form>
<form action="/app/action/Trailing"><input type="hidden" name="decoy2" value="also-wrong"></form>
</body></html>
"""


def run_parse_form(tmp_path: Path, html: str, pattern: str) -> subprocess.CompletedProcess[str]:
    """Source the setup script and call its parser, returning the NUL-separated pairs."""
    page = tmp_path / "page.html"
    page.write_text(html)
    return subprocess.run(
        ["bash", "-c", 'source "$1"; parse_form "$2" "$3"', "_", str(SETUP_SCRIPT), str(page), pattern],
        capture_output=True,
        text=True,
        env=STUB_ENV,
        check=False,
    )


def parsed_fields(tmp_path: Path, html: str, pattern: str) -> tuple[str, dict[str, str]]:
    result = run_parse_form(tmp_path, html, pattern)
    assert result.returncode == 0, result.stderr
    parts = result.stdout.split("\0")[:-1]
    pairs = dict(zip(parts[0::2], parts[1::2], strict=True))
    return pairs.pop("__ACTION__", ""), pairs


def test_selects_the_form_by_action_not_by_position(tmp_path: Path) -> None:
    """The wanted form is never first on an XNAT admin page -- the quick search box is."""
    action, fields = parsed_fields(tmp_path, PAGE, "/app/action/ManageDataTypes$")

    assert action == "/app/action/ManageDataTypes"
    assert "decoy" not in fields
    assert "decoy2" not in fields
    assert fields["element[0]/element_name"] == "xnat:ctSessionData"


def test_submits_what_a_browser_would_submit(tmp_path: Path) -> None:
    """Hidden and text inputs always; radios and checkboxes only when checked; buttons never."""
    _, fields = parsed_fields(tmp_path, PAGE, "/app/action/ManageDataTypes$")

    assert fields["element[0]/code"] == "CT"
    assert fields["popup"] == "true", "uppercase attribute names must parse -- XNAT's templates use them"
    assert fields["element[0]/accessible"] == "true"
    assert "element[1]/accessible" not in fields, "an unchecked checkbox is not submitted"
    assert fields["secure"] == "1", "only the checked radio of a group is submitted"
    assert "save" not in fields, "the caller names the button it is clicking"


def test_preserves_values_needing_care(tmp_path: Path) -> None:
    """Values carry spaces and HTML entities; both survive the round trip verbatim."""
    _, fields = parsed_fields(tmp_path, PAGE, "/app/action/ManageDataTypes$")

    assert fields["spaced"] == "SM Sessions"
    assert fields["escaped"] == "a & b"


def test_reports_nothing_when_no_form_matches(tmp_path: Path) -> None:
    """A page without the wanted form yields no action, which the script treats as a hard error.

    This is how an expired session or a renamed screen presents: a login page, parsed happily.
    """
    action, fields = parsed_fields(tmp_path, PAGE, "/app/action/NoSuchAction$")

    assert action == ""
    assert fields == {}


def test_setup_aborts_when_the_expected_form_is_absent(tmp_path: Path) -> None:
    """require_form turns a missing form into a failure rather than an empty POST."""
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; read_form "$2" "/app/action/NoSuchAction$"; require_form "NoSuchAction" "page"',
            "_",
            str(SETUP_SCRIPT),
            str(page),
        ],
        capture_output=True,
        text=True,
        env=STUB_ENV,
        check=False,
    )

    assert result.returncode != 0
    assert "no form matching" in result.stderr


def test_sourcing_the_script_does_not_run_it(tmp_path: Path) -> None:
    """The main guard is what makes the tests above safe; without it they would try to log in."""
    result = subprocess.run(
        ["bash", "-c", f'source "{SETUP_SCRIPT}"; echo sourced-cleanly'],
        capture_output=True,
        text=True,
        env=STUB_ENV,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "sourced-cleanly" in result.stdout


@pytest.mark.parametrize("script", [SETUP_SCRIPT, VERIFY_SCRIPT])
def test_both_scripts_take_the_datatype_list_from_one_place(script: Path) -> None:
    """A type enabled by one script and not checked by the other is the bug this rules out."""
    body = script.read_text()

    assert "datatypes-common.sh" in body
    assert "REQUIRED_DATATYPES=(" not in body, "the list belongs in datatypes-common.sh alone"


def test_the_list_names_slide_microscopy() -> None:
    """The type the pathology tutorial needs; XNAT ships its schema but leaves it disabled."""
    assert "xnat:smSessionData|" in COMMON_SCRIPT.read_text()


def test_bring_up_runs_the_setup() -> None:
    """A trust that comes up without this cannot archive slide microscopy, and says so only later."""
    recipe = XNAT_MAKEFILE.read_text()

    assert "bash setup-datatypes.sh" in recipe
    assert recipe.index("bash configure-xnat.sh") < recipe.index("bash setup-datatypes.sh"), (
        "setup-datatypes.sh authenticates with the password configure-xnat.sh rotates to"
    )


def test_shellcheck_clean_if_available() -> None:
    """Cheap belt-and-braces on the three scripts when shellcheck is installed."""
    shellcheck = shutil.which("shellcheck")
    if not shellcheck:
        pytest.skip("shellcheck not installed")

    result = subprocess.run(
        [shellcheck, "-S", "error", str(SETUP_SCRIPT), str(VERIFY_SCRIPT), str(COMMON_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=os.fspath(CONFIG_DIR),
    )

    assert result.returncode == 0, result.stdout + result.stderr


# --- siteUrl (FLIP: OHIF whole-slide viewport rendered black) --------------------------------
#
# Separate concern from data types, but the same failure shape and the same demo: a slide that
# archives correctly and still will not display. Kept here rather than in a new file because both
# are "XNAT is configured wrong in a way nothing reports".

CONFIGURE_XNAT = CONFIG_DIR / "configure-xnat.sh"
K8S_INIT_JOB = (
    Path(__file__).resolve().parents[3] / "deploy" / "providers" / "kubernetes" / "templates" / "xnat-init-job.yaml"
)


def test_site_url_is_browser_reachable_not_the_docker_internal_host() -> None:
    """siteUrl reaches the *browser*, so it cannot be the internal name this script dials.

    The OHIF viewer builds its DICOMweb roots from siteUrl and fetches them from the user's
    machine. Set to ``http://xnat-web:8080`` every whole-slide tile fails at DNS and the viewport
    renders black -- with nothing in the XNAT logs, because the requests never reach XNAT.
    """
    body = CONFIGURE_XNAT.read_text()

    assert "${XNAT_SITE_URL:-http://127.0.0.1:${XNAT_WEB_PORT}}" in body, (
        "siteUrl must default to a browser-reachable URL, overridable via XNAT_SITE_URL"
    )
    # FLIP#993 split the port: XNAT_PORT is the DICOM receiver a PACS dials, XNAT_WEB_PORT the
    # web UI a browser opens. A siteUrl on the receiver port renders the same black viewport.
    assert "${XNAT_SITE_URL:-http://127.0.0.1:${XNAT_PORT}}" not in body, (
        "siteUrl must use the web port, not the DICOM receiver port"
    )
    assert "${XNAT_SITE_URL:-$XNAT_URL}" not in body, (
        "XNAT_URL is the Docker-internal host and is not reachable from a browser"
    )


def test_k8s_init_job_allows_a_browser_reachable_site_url() -> None:
    """The chart cannot know the ingress URL, but it must let an operator supply one.

    The init job activates the site by running configure-xnat.sh, so the override reaches it the
    same way every other knob does: as environment on the configure-xnat-web container. The script
    also refuses to start without XNAT_WEB_PORT, which is what it derives the default URL from.
    """
    body = K8S_INIT_JOB.read_text()

    assert "- name: XNAT_SITE_URL\n              value: {{ .Values.xnat.web.siteUrl | quote }}" in body, (
        "xnat.web.siteUrl must reach configure-xnat.sh as XNAT_SITE_URL"
    )
    assert "- name: XNAT_WEB_PORT\n              value: {{ .Values.xnat.web.service.port | quote }}" in body, (
        "configure-xnat.sh requires XNAT_WEB_PORT; the browser-facing port on k8s is the service port"
    )
    assert 'siteUrl\\": \\"${XNAT_URL}' not in body, "no site may hardcode the in-cluster URL"


XNAT_COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose-stack.yml"
TRUST_ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_site_url_override_actually_reaches_the_container() -> None:
    """xnat-web declares an explicit environment list, so an undeclared variable cannot arrive.

    Documenting XNAT_SITE_URL in the kit file without this line would leave a remote trust's
    operator setting a variable that goes nowhere.
    """
    body = XNAT_COMPOSE.read_text()

    assert "- XNAT_SITE_URL=${XNAT_SITE_URL:-}" in body, (
        "xnat-web must pass XNAT_SITE_URL through, defaulting to empty so compose does not warn"
    )


def test_kit_template_tells_remote_operators_about_site_url() -> None:
    """127.0.0.1 is right for a laptop and wrong for every host browsed from elsewhere."""
    body = TRUST_ENV_EXAMPLE.read_text()

    assert "XNAT_SITE_URL" in body
    assert "#XNAT_SITE_URL=" in body, "keep the example commented; an empty value must fall back"
