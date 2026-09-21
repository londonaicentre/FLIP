# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Static guards on the Cognito app client's ``callback_urls`` per Terraform root.

``callback_urls`` is not cosmetic: flip-api reads it back from live Cognito with
``describe_user_pool_client`` and serves the normalized origins as the browser CORS
allowlist with ``allow_credentials=true`` (see ``flip_api/utils/cors.py``). Nothing
in the runtime test suites can catch a bad value — it is read from AWS at start-up,
not from this tree — so the invariant is asserted here, over the ``.tf`` source.

Two failure modes are guarded, and both are silent in a plan diff:

* **Deletion.** ``modules/cognito`` defaults ``callback_urls`` to
  ``["https://localhost:443"]``, so dropping the explicit argument from the stag/prod
  root does not empty the list — it puts a localhost origin back into the production
  CORS allowlist.
* **Addition.** A localhost origin added to the stag/prod root for a debugging session
  and left behind has the same effect.

The dev root is asserted in the opposite direction: it *must* keep localhost, so that
a well-meant "no localhost in Terraform" sweep cannot break local development instead.
It also computes its two browser-origin lists rather than passing the variables straight
through — ``var.dev_ui_ports`` is expanded into one ``http://localhost:<port>`` origin each
(FLIP#1227) — so the variable defaults alone are a strict subset of what reaches AWS. The
wiring is therefore guarded too: without it, restoring ``callback_urls = var.cognito_callback_urls``
would leave this suite green while every pre-registered port silently stopped working in a
browser. The port bounds are pinned to the prose that documents them for the same reason —
a widened block that the docs still describe with the old numbers sends a developer to an
unregistered port, and the failure is a browser CORS error with nothing red anywhere.

The stag/prod list holds a ``local.`` reference rather than a quoted URL, because the
canonical origin differs between a DNS-managed environment and a zone-less LZA bring-up
(FLIP#749) and ``cloudfront.tf`` resolves the two into one expression. So the guard reads
raw list entries rather than quoted literals — a reference entry would otherwise read as
no entry at all — and follows each reference to its definition, asserting over that too.
"""

import re
from pathlib import Path

from tf_source import hcl_block, strip_comments

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AWS_PROVIDER_DIR.parents[2]
STAG_PROD_SERVICES_TF = AWS_PROVIDER_DIR / "services.tf"
DEV_VARIABLES_TF = AWS_PROVIDER_DIR / "dev" / "variables.tf"
DEV_MAIN_TF = AWS_PROVIDER_DIR / "dev" / "main.tf"

# Every file that writes the dev UI port bounds out in prose. CLAUDE.md is AGENTS.md's
# mandated mirror, so it is checked when present rather than required to exist.
DEV_PORT_DOCS = (
    AWS_PROVIDER_DIR / "dev" / "README.md",
    REPO_ROOT / ".env.development.example",
    REPO_ROOT / "AGENTS.md",
)
DEV_PORT_DOC_MIRROR = REPO_ROOT / "CLAUDE.md"

# Where a `local.<name>` entry in the list may be defined.
LOCALS_SOURCES = (AWS_PROVIDER_DIR / "locals.tf", AWS_PROVIDER_DIR / "cloudfront.tf")

LOCAL_REFERENCE = re.compile(r"^local\.([A-Za-z0-9_]+)$")


def _list_entries(block: str, argument: str) -> list[str]:
    """Read a list-valued argument out of an HCL block as its raw entry expressions.

    Args:
        block (str): An HCL block body.
        argument (str): The argument name, e.g. ``callback_urls``.

    Returns:
        list[str]: One entry per element, verbatim — a quoted URL keeps its quotes and any
        ``${...}`` interpolation, an unquoted expression (``local.ui_origin``) comes back as
        written.
    """
    match = re.search(rf"^\s*{re.escape(argument)}\s*=\s*\[(.*?)\]", block, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{argument} is not set as a literal list — the guard below cannot see it"
    return [entry.strip() for entry in match.group(1).split(",") if entry.strip()]


def _local_definition(name: str) -> str:
    """Return the expression a ``local.<name>`` reference is defined as.

    Args:
        name (str): The local's name, without the ``local.`` prefix.

    Returns:
        str: The right-hand side of its definition, as one line of source.
    """
    for path in LOCALS_SOURCES:
        match = re.search(rf"^\s*{re.escape(name)}\s*=\s*(.+)$", path.read_text(), re.MULTILINE)
        if match is not None:
            return match.group(1)
    raise AssertionError(f"local.{name} is defined in none of {[path.name for path in LOCALS_SOURCES]}")


def _cognito_module_argument(argument: str) -> list[str]:
    """Read one argument passed to the stag/prod root's ``module "cognito"`` block.

    Args:
        argument (str): The argument name.

    Returns:
        list[str]: The argument's entries.
    """
    block = hcl_block(STAG_PROD_SERVICES_TF.read_text(), 'module "cognito"')
    return _list_entries(block, argument)


def _cognito_origin_sources(argument: str) -> list[str]:
    """Every piece of source a stag/prod browser origin could be written into.

    An entry that is a ``local.`` reference contributes the reference *and* the expression
    it resolves to, so a localhost origin cannot hide one indirection away.

    Args:
        argument (str): The argument name, e.g. ``callback_urls``.

    Returns:
        list[str]: Entry expressions, plus the definition of each local they reference.
    """
    sources = []
    for entry in _cognito_module_argument(argument):
        sources.append(entry)
        reference = LOCAL_REFERENCE.match(entry)
        if reference is not None:
            sources.append(_local_definition(reference.group(1)))
    return sources


def test_stag_prod_passes_callback_urls_explicitly() -> None:
    """The stag/prod root must set callback_urls rather than inherit the module default.

    The module default is ``["https://localhost:443"]``, so an omitted argument is not an
    empty allowlist — it is a localhost origin in production.
    """
    assert _cognito_module_argument("callback_urls"), "stag/prod callback_urls must be non-empty"


def test_stag_prod_callback_urls_carry_no_localhost() -> None:
    """No stag/prod callback URL may name localhost, directly or via the local it references."""
    offenders = [source for source in _cognito_origin_sources("callback_urls") if "localhost" in source.lower()]
    assert not offenders, f"localhost is not a stag/prod browser origin: {offenders}"


def test_stag_prod_logout_urls_carry_no_localhost() -> None:
    """No stag/prod logout URL may name localhost.

    flip-api does not read these, but the client should advertise no redirect target
    that isn't a real FLIP origin.
    """
    offenders = [source for source in _cognito_origin_sources("logout_urls") if "localhost" in source.lower()]
    assert not offenders, f"localhost is not a stag/prod redirect target: {offenders}"


def test_dev_root_keeps_localhost_callback_urls() -> None:
    """The dev root must keep its localhost origins.

    This is the other half of the asymmetry: local development serves the UI from
    localhost, so stripping it here would break dev sign-in via the same CORS path.
    """
    block = hcl_block(DEV_VARIABLES_TF.read_text(), 'variable "cognito_callback_urls"')
    urls = _list_entries(block, "default")
    assert any("localhost" in url.lower() for url in urls), f"dev callback_urls lost its localhost origins: {urls}"


def _dev_argument(header: str, argument: str) -> str:
    """Read a single-valued argument out of a block in the dev root's ``main.tf``.

    Args:
        header (str): The block header, e.g. ``module "cognito"``.
        argument (str): The argument name, e.g. ``callback_urls``.

    Returns:
        str: The right-hand side, stripped — ``local.callback_urls`` for a reference.
    """
    block = strip_comments(hcl_block(strip_comments(DEV_MAIN_TF.read_text()), header))
    match = re.search(rf"^\s*{re.escape(argument)}\s*=\s*(.+)$", block, re.MULTILINE)
    assert match is not None, f"{header} does not set {argument}"
    return match.group(1).strip()


def _dev_ui_port_bounds() -> tuple[int, int]:
    """Return the lowest and highest port in ``var.dev_ui_ports``'s default.

    Returns:
        tuple[int, int]: ``(lowest, highest)``.
    """
    block = hcl_block(DEV_VARIABLES_TF.read_text(), 'variable "dev_ui_ports"')
    ports = [int(port) for port in _list_entries(block, "default")]
    assert ports, "var.dev_ui_ports default must not be empty — it is the whole mechanism"
    return min(ports), max(ports)


def test_dev_cognito_receives_the_generated_callback_urls() -> None:
    """The dev app client must be given the generated list, not the bare variable.

    ``var.cognito_callback_urls``'s default keeps a localhost origin, so the guard above
    stays green even if this wiring is dropped — at which point the ports pre-registered
    from ``var.dev_ui_ports`` quietly stop being browser origins.
    """
    assert _dev_argument('module "cognito"', "callback_urls") == "local.callback_urls"


def test_dev_buckets_receive_the_generated_cors_origins() -> None:
    """Both browser-facing dev buckets must take the same generated origins as Cognito.

    A bucket left on the bare variable is reachable from fewer origins than the API allows,
    so a presigned upload or download fails in the browser alone.
    """
    for header in ('module "flip_model_files_uploads_bucket"', 'module "flip_fl_results_bucket"'):
        assert _dev_argument(header, "cors_allowed_origins") == "local.bucket_cors_origins", header


def test_dev_generated_origins_expand_the_port_variable() -> None:
    """Both generated lists must fold in the origins expanded from ``var.dev_ui_ports``."""
    locals_block = strip_comments(hcl_block(strip_comments(DEV_MAIN_TF.read_text()), "locals {"))

    expansion = re.search(r"^\s*dev_ui_port_origins\s*=\s*(.+)$", locals_block, re.MULTILINE)
    assert expansion is not None, "local.dev_ui_port_origins is gone"
    assert "var.dev_ui_ports" in expansion.group(1), "local.dev_ui_port_origins no longer reads var.dev_ui_ports"

    for name in ("callback_urls", "bucket_cors_origins"):
        match = re.search(rf"^\s*{name}\s*=\s*(.+)$", locals_block, re.MULTILINE)
        assert match is not None, f"local.{name} is gone"
        assert "local.dev_ui_port_origins" in match.group(1), f"local.{name} dropped the generated origins"


def test_dev_ui_port_bounds_match_the_documentation() -> None:
    """Every file that writes the port bounds out in prose must name the current ones.

    The bounds live in four documents and one Terraform default. Widening the block without
    the docs sends a developer to an unregistered port; the only symptom is a browser CORS
    error, with nothing red in CI.
    """
    lowest, highest = _dev_ui_port_bounds()
    written = re.compile(rf"{lowest}\s*[-–—]\s*{highest}")

    paths = [*DEV_PORT_DOCS, *([DEV_PORT_DOC_MIRROR] if DEV_PORT_DOC_MIRROR.exists() else [])]
    stale = [path for path in paths if not written.search(path.read_text())]
    assert not stale, (
        f"var.dev_ui_ports now spans {lowest}-{highest}; these still document the old bounds: "
        f"{[str(path.relative_to(REPO_ROOT)) for path in stale]}"
    )
