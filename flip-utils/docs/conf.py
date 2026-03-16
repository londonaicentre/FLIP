"""Sphinx configuration for the FLIP package docs."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "flip"
PYPROJECT_PATH = ROOT / "pyproject.toml"


def get_project_name() -> str:
    """Read the distribution name from pyproject.toml."""
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return pyproject["project"]["name"]


def get_version() -> str:
    """Read the package version without importing the package."""
    init_text = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = ["\']([^"\']+)["\']', init_text, re.MULTILINE)
    if match is None:
        raise RuntimeError("Unable to find __version__ in flip/__init__.py")
    return match.group(1)


project = get_project_name()
copyright = "2026, Guy's and St Thomas' NHS Foundation Trust & King's College London"
author = "AI Centre for Value Based Healthcare"
release = get_version()
version = release
root_doc = "index"

extensions = [
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "autoapi.extension",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_title = f"{project} {version}"
html_theme_options = {
    "analytics_id": "",
    "analytics_anonymize_ip": False,
    "logo_only": False,
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    "vcs_pageview_mode": "",
    "style_nav_header_background": "",
    "collapse_navigation": True,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}
html_sidebars = {
    "**": ["globaltoc.html"],
}
html_scaled_image_link = False
html_show_sourcelink = True
html_static_path = ["_static"]
python_use_unqualified_type_names = False

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_rtype = False
napoleon_attr_annotations = True

autoapi_type = "python"
autoapi_dirs = [str(ROOT / "flip")]
autoapi_root = "reference/api"
autoapi_add_toctree_entry = False
autoapi_member_order = "bysource"
autoapi_python_class_content = "both"
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
