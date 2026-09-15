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
from pathlib import Path

from sphinx.errors import SphinxError
from sphinx.util import logging as sphinx_logging

logger = sphinx_logging.getLogger(__name__)

# Treat each API directory as an independent package
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

# The full version of the documentation, including alpha/beta/rc tags
release = ""

# The full version of the FLIP platform, including alpha/beta/rc tags
# The rst_epilog list makes items within it globally-available to compiled .rst files.
# rst_epilog = """
# .. |flip_version| replace:: {flip_version}
# """.format(
#     flip_version="1.0",
# )

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
    "sphinxcontrib.bibtex",
    "sphinx_reredirects",
]

# Pages renamed in FLIP#364 keep their old URLs as HTML redirect stubs (target is relative to the old page).
redirects = {
    "components/architecture-overview": "overview.html",
    "components/component-fl-nodes": "component-fl-nets.html",
}

autoapi_type = "python"

# autoapi
autoapi_dirs = [
    "../../flip-api/src",
    "../../trust/data-access-api",
    "../../trust/imaging-api",
    "../../trust/trust-api",
    # include flip-utils package for API docs
    "../../flip-utils/flip",
]
autoapi_ignore = [
    "*/.venv/*",
    "*/tests/*",
    "*/conftest.py",
    # Alembic scripts are one-shot upgrade/downgrade pairs, not public API. They also carry no
    # __init__.py, so AutoAPI would emit each as a revision-hash-named top-level module.
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
html_favicon = 'assets/favicon.ico'
html_logo = 'assets/flip-logo.png'
# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]


# -- Generated figures -------------------------------------------------------
# The Central Hub AWS diagrams (one pair per deployment mode) are diagram-as-code kept beside the Terraform they depict
# (deploy/providers/AWS/architecture/central_hub.py, drift-guarded by that tree's tests). It is rendered here at
# build time into assets/generated/ (gitignored), so the published deployment pages always show the pictures for the commit
# it documents and no PNG has to be kept in sync by hand. Needs graphviz `dot`: ReadTheDocs installs it via
# build.apt_packages, the docs CI job via apt-get.

REPO_ROOT = Path(__file__).resolve().parents[2]
AWS_PROVIDER_DIR = REPO_ROOT / "deploy" / "providers" / "AWS"
GENERATED_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "generated"
SKIP_DIAGRAMS_ENV = "FLIP_DOCS_SKIP_DIAGRAMS"


def _render_generated_figures(app):
    """Render the Central Hub AWS diagrams before Sphinx reads the sources.

    Fails the build when graphviz is missing rather than publishing a page with an empty figure. A developer
    without graphviz can opt out with ``FLIP_DOCS_SKIP_DIAGRAMS=1`` for a text-only local build; that prints a
    warning here and Sphinx's own "image file not readable" warning on the Central Hub deployment pages, never silently.
    """
    if os.environ.get(SKIP_DIAGRAMS_ENV) == "1":
        logger.warning(
            "%s=1: not rendering the Central Hub AWS diagrams; the Central Hub deployment pages will report missing images",
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


def setup(app):
    app.connect("builder-inited", _render_generated_figures)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
