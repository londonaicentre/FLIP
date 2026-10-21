# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import sys
import tomllib
from pathlib import Path

from sphinx.application import Sphinx
from sphinx.errors import SphinxError
from sphinx.util import logging as sphinx_logging

logger = sphinx_logging.getLogger(__name__)

# Treat each application source tree as an independent package for AutoAPI.
sys.path.insert(0, os.path.abspath("../../flip-api/src"))
sys.path.insert(0, os.path.abspath("../../trust/data-access-api"))
sys.path.insert(0, os.path.abspath("../../trust/imaging-api"))
sys.path.insert(0, os.path.abspath("../../trust/trust-api"))
# Include flip-utils package.
# Insert the package *parent* dir so `import flip` resolves to flip-utils/flip/__init__.py.
sys.path.insert(0, os.path.abspath("../../flip-utils"))

# sys.path.insert(0, os.path.abspath('.'))

# -- Project information -----------------------------------------------------
project = "FLIP"
copyright = "2026, The London AI Centre for Value-Based Healthcare"
author = "The London AI Centre for Value-Based Healthcare"

# The FLIP platform version, read from the repository's root pyproject.toml so the docs
# never carry a hand-maintained copy of it. It reaches the HTML title and the search index,
# so an unreadable file fails the build rather than quietly producing an unversioned site —
# the same stance the diagram hook below takes.
REPO_ROOT = Path(__file__).resolve().parents[2]


def _platform_version() -> str:
    """Read ``project.version`` from the repository root ``pyproject.toml``.

    Returns:
        str: The declared platform version.

    Raises:
        SphinxError: If the file is missing, unparseable, or carries no ``project.version``.
    """
    pyproject = REPO_ROOT / "pyproject.toml"
    try:
        with pyproject.open("rb") as fh:
            return str(tomllib.load(fh)["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise SphinxError(f"Could not read project.version from {pyproject} ({exc!r}).") from exc


# Both Sphinx version strings carry the full platform version (no separate X.Y short form).
release = _platform_version()
version = release

# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "autoapi.extension",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
    "sphinx.ext.todo",
    "sphinx.ext.coverage",
    "sphinx.ext.ifconfig",
    "sphinx.ext.extlinks",
    "sphinxcontrib.bibtex",
    "sphinxcontrib.mermaid",
    "sphinx_reredirects",
]

# :gh:`path` links a repo file at the ref this build is of: the PR commit on a PR preview, the branch or tag
# ReadTheDocs built otherwise, develop locally.
if os.environ.get("READTHEDOCS_VERSION_TYPE") == "external":
    _gh_ref = os.environ.get("READTHEDOCS_GIT_COMMIT_HASH", "develop")
else:
    _gh_ref = os.environ.get("READTHEDOCS_GIT_IDENTIFIER", "develop")
extlinks = {"gh": (f"https://github.com/londonaicentre/FLIP/blob/{_gh_ref}/%s", "%s")}

# Swimlane diagrams (sphinxcontrib-mermaid) in the FLIP design-system colours of _static/style.css.
mermaid_light_theme = "base"
mermaid_dark_theme = "base"  # the docs theme has no dark mode, so keep the same colours
# Natural size, scaled down only to fit a narrow screen (the plugin default is a fixed 500px-high box).
mermaid_width = "auto"
mermaid_height = "auto"
mermaid_init_config = {
    "startOnLoad": False,
    # Counter-based SVG ids: time-based ones can collide when several diagrams render in one millisecond.
    "deterministicIds": True,
    "themeVariables": {
        "fontFamily": "Montserrat, system-ui, sans-serif",
        "fontSize": "14px",
        "actorBkg": "#F7F3F9",
        "actorBorder": "#61366E",
        "actorTextColor": "#61366E",
        "actorLineColor": "#B88AC6",
        "signalColor": "#2C3E6B",
        "signalTextColor": "#333333",
        "labelBoxBkgColor": "#F7F3F9",
        "labelBoxBorderColor": "#872B6E",
        "labelTextColor": "#872B6E",
        "loopTextColor": "#872B6E",
        "noteBkgColor": "#F7F3F9",
        "noteBorderColor": "#B88AC6",
        "sequenceNumberColor": "#FFFFFF",
    },
    "sequence": {"mirrorActors": False, "showSequenceNumbers": True, "useMaxWidth": False},
}

# Renamed or moved pages keep their old URLs as HTML redirect stubs (target is relative to the old page).
redirects = {
    "components/architecture-overview": "overview.html",
    "components/component-fl-nodes": "component-fl-nets.html",
    # The self-contained mode's page went with the mode (FLIP#1479).
    "deploy-flip/deploy-central-hub-aws": "deploy-central-hub-aws-lza.html",
    "user-guides/arkplus-fine-tuning": "../paper/arkplus-fine-tuning.html",
    "user-guides/reproducing-platform-overhead": "../paper/reproducing-platform-overhead.html",
    "working-with-flip-apps/package-model-as-map": "wrap-model-for-deployment.html",
}

autoapi_type = "python"

# autoapi
autoapi_dirs = [
    "../../flip-api/src",
    "../../trust/data-access-api",
    "../../trust/imaging-api",
    "../../trust/trust-api",
    "../../flip-utils/flip",
]
autoapi_ignore = [
    "*/.venv/*",
    "*/tests/*",
    "*/conftest.py",
    # Alembic revisions are migration scripts, not API modules.
    "*/migrations/*",
]

# Optional:
autoapi_keep_files = True  # useful for debugging
autoapi_add_toctree_entry = False

# Constants re-exported from flip.constants.flip_constants are reachable by two dotted paths, so
# every reference to them is ambiguous. Nothing is unresolved; the duplicates are the re-exports.
suppress_warnings = ["ref.python"]


# autosummary_generate = True
napoleon_google_docstring = True
napoleon_use_param = True
napoleon_use_ivar = True
bibtex_bibfiles = ["references.bib"]
# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []

# -- Options for HTML output -------------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
#
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "analytics_id": "",  # Provided by Google in your dashboard
    "analytics_anonymize_ip": False,
    "logo_only": True,
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    "vcs_pageview_mode": "",
    "style_nav_header_background": "#61366E",
    # Toc options
    "collapse_navigation": True,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}
html_sidebars = {
    "**": ["globaltoc.html"]  # Ensures ToC entries are always visible
}
html_scaled_image_link = False
html_show_sourcelink = True
html_favicon = "assets/favicon.ico"
html_logo = "assets/flip-logo.png"
# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]


# -- Generated figures -------------------------------------------------------
# The Central Hub AWS diagrams (request paths; data services) are diagram-as-code kept beside the Terraform they
# depict (deploy/providers/AWS/architecture/central_hub.py, drift-guarded by that tree's tests). They are rendered
# here at build time into assets/generated/ (gitignored), so the published deployment pages always show the
# pictures for the commit they document and no PNG has to be kept in sync by hand. Needs graphviz `dot`:
# ReadTheDocs installs it via build.apt_packages, the docs CI job via apt-get.

AWS_PROVIDER_DIR = REPO_ROOT / "deploy" / "providers" / "AWS"
GENERATED_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "generated"
SKIP_DIAGRAMS_ENV = "FLIP_DOCS_SKIP_DIAGRAMS"


def _render_generated_figures(app: Sphinx) -> None:
    """Render the Central Hub AWS diagrams before Sphinx reads the sources.

    Fails the build when graphviz is missing rather than publishing a page with an empty figure. A developer
    without graphviz can opt out with ``FLIP_DOCS_SKIP_DIAGRAMS=1`` for a text-only local build; that prints a
    warning here and Sphinx's own "image file not readable" warning on the Central Hub deployment pages, never silently.
    """
    if os.environ.get(SKIP_DIAGRAMS_ENV) == "1":
        logger.warning(
            "%s=1: not rendering the Central Hub AWS diagrams; the Central Hub deployment pages will report missing "
            "images",
            SKIP_DIAGRAMS_ENV,
        )
        return
    sys.path.insert(0, str(AWS_PROVIDER_DIR))
    from architecture.central_hub import render  # noqa: PLC0415  (import deferred so `diagrams` is only needed here)

    try:
        outputs = render(GENERATED_ASSETS_DIR)
    except RuntimeError as exc:
        raise SphinxError(
            f"{exc} On ReadTheDocs graphviz comes from build.apt_packages in .readthedocs.yaml; locally, "
            f"`apt-get install graphviz`, or set {SKIP_DIAGRAMS_ENV}=1 for a text-only build."
        ) from exc
    for path in outputs:
        logger.info("rendered %s", path.relative_to(REPO_ROOT))


# -- Documentation GIFs ------------------------------------------------------
# The user-guide GIFs are not tracked in git (FLIP#1236): each Cypress recording from develop is published to the
# public Hugging Face dataset aicentreflip/docs-gifs, each release tags the one it ships (FLIP#1547), and the hook
# below fetches the set this build calls for (a release's tag, a PR's own recording, else the newest) into
# assets/generated/gifs/ (gitignored, wiped by `make clean`) before Sphinx reads the sources, verifying every file
# against the published manifest. Needs outbound HTTPS to huggingface.co (and *.hf.co) and nothing else: the
# dataset is public.

DOCS_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
GIFS_DIR = GENERATED_ASSETS_DIR / "gifs"
SKIP_GIF_FETCH_ENV = "FLIP_DOCS_SKIP_GIF_FETCH"


def _fetch_docs_gifs(app: Sphinx) -> None:
    """Fetch the docs GIFs before Sphinx reads the sources.

    Fails the build when no set can be fetched or verified rather than publishing pages with missing figures.
    ``FLIP_DOCS_SKIP_GIF_FETCH=1`` opts out — for a text-only build offline, or to preview GIFs recorded locally
    with ``npm run docs:gifs``, which land in the same directory and would otherwise be restored to the published
    bytes; that prints a warning here and Sphinx's own "image file not readable" warning for every figure it then
    cannot find, never silently.
    """
    if os.environ.get(SKIP_GIF_FETCH_ENV) == "1":
        logger.warning(
            "%s=1: not fetching the docs GIFs; the user-guide pages will report missing images unless "
            "assets/generated/gifs/ already holds them",
            SKIP_GIF_FETCH_ENV,
        )
        return
    sys.path.insert(0, str(DOCS_SCRIPTS_DIR))
    import fetch_docs_gifs  # noqa: PLC0415  (import deferred: a build tool beside conf.py, not a Sphinx dependency)

    revisions = fetch_docs_gifs.revisions_to_try()
    try:
        report = fetch_docs_gifs.fetch(
            fetch_docs_gifs.repo_from_env(), revisions, GIFS_DIR, log=lambda message: logger.info("%s", message)
        )
    except fetch_docs_gifs.FetchError as exc:
        raise SphinxError(
            f"Could not fetch the docs GIFs: {exc}. Tried {', '.join(revisions)} (${fetch_docs_gifs.REVISION_ENV} "
            f"picks one) on the dataset ${fetch_docs_gifs.REPO_ENV} names; behind a proxy, allow huggingface.co "
            f"and *.hf.co; or set {SKIP_GIF_FETCH_ENV}=1 for a text-only build."
        ) from exc
    logger.info("docs GIFs: %s", report.summary())


def setup(app: Sphinx) -> dict[str, bool]:
    app.connect("builder-inited", _render_generated_figures)
    app.connect("builder-inited", _fetch_docs_gifs)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
