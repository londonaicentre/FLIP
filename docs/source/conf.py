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

# Treat each API directory as an independent package
sys.path.insert(0, os.path.abspath("../../flip-api/src"))
sys.path.insert(0, os.path.abspath("../../trust/data-access-api"))
sys.path.insert(0, os.path.abspath("../../trust/imaging-api"))
sys.path.insert(0, os.path.abspath("../../trust/trust-api"))

# sys.path.insert(0, os.path.abspath('.'))

# -- Project information -----------------------------------------------------
project = "flip"
copyright = "2025, The London Medical Imaging & AI Centre for Value Based Healthcare (AIC)"
author = "The London Medical Imaging & AI Centre for Value Based Healthcare (AIC)"

# The full version, including alpha/beta/rc tags
release = "1.0"

# The full version of the FLIP platform, including alpha/beta/rc tags
# The rst_epilog list makes items within it globally-available to compiled .rst files.
rst_epilog = """
.. |flip_version| replace:: {flip_version}
""".format(
    flip_version="1.0",
)

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
]

autoapi_type = "python"

# autoapi
autoapi_dirs = [
    "../../flip-api/src",
    "../../trust/data-access-api",
    "../../trust/imaging-api",
    "../../trust/trust-api",
]
autoapi_ignore = [
    "*/.venv/*",
    "*/tests/*",
    "*/conftest.py",
]

# Optional:
autoapi_keep_files = True  # useful for debugging
autoapi_add_toctree_entry = False


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
    "logo_only": False,
    # 'display_version': False,  # To avoid confusion with documentation vs platform versions
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    "vcs_pageview_mode": "",
    "style_nav_header_background": "",
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
# html_favicon = 'assets/favicon.ico'
# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]
