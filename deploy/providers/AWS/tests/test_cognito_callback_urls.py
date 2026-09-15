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

The stag/prod list holds a ``local.`` reference rather than a quoted URL, because the
canonical origin differs between a DNS-managed environment and a zone-less LZA bring-up
(FLIP#749) and ``cloudfront.tf`` resolves the two into one expression. So the guard reads
raw list entries rather than quoted literals — a reference entry would otherwise read as
no entry at all — and follows each reference to its definition, asserting over that too.
"""

import re
from pathlib import Path

AWS_PROVIDER_DIR = Path(__file__).resolve().parent.parent
STAG_PROD_SERVICES_TF = AWS_PROVIDER_DIR / "services.tf"
DEV_VARIABLES_TF = AWS_PROVIDER_DIR / "dev" / "variables.tf"

# Where a `local.<name>` entry in the list may be defined.
LOCALS_SOURCES = (AWS_PROVIDER_DIR / "locals.tf", AWS_PROVIDER_DIR / "cloudfront.tf")

LOCAL_REFERENCE = re.compile(r"^local\.([A-Za-z0-9_]+)$")


def _block(source: str, header: str) -> str:
    """Extract a brace-balanced HCL block by its opening header.

    Args:
        source (str): Full contents of a Terraform file.
        header (str): The block header to find, e.g. ``module "cognito"``.

    Returns:
        str: The block body, without the enclosing braces.
    """
    start = source.index(header)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace + 1 : index]
    raise AssertionError(f"unbalanced braces after {header!r}")


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
    block = _block(STAG_PROD_SERVICES_TF.read_text(), 'module "cognito"')
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
    block = _block(DEV_VARIABLES_TF.read_text(), 'variable "cognito_callback_urls"')
    urls = _list_entries(block, "default")
    assert any("localhost" in url.lower() for url in urls), f"dev callback_urls lost its localhost origins: {urls}"
