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

"""Fixture XNAT plugin jars for the tests that run ``wait-for-xnat-plugins.sh``.

With ``XNAT_PLUGIN_READINESS_ROSTER=1`` the wait reads which plugins to expect from the jars in
``${XNAT_HOME}/plugins`` — the ``id`` in each jar's ``META-INF/xnat/*-plugin.properties``, the file
XNAT itself scans — so a test that runs it that way, directly or through ``configure-xnat.sh``,
needs a plugin directory whose jars declare ids, and an XNAT stub whose ``GET /xapi/plugins``
lists them.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

# The roster FLIP installs, as the jars declare it: read on 14 Oct 2026 from each jar's
# META-INF/xnat/*-plugin.properties on a running XNAT 1.10.0, and the same ids its
# GET /xapi/plugins answered with. ``None`` would be a library jar that declares no plugin.
PLUGIN_JARS: dict[str, str | None] = {
    "batch-launch-0.9.0-xpl.jar": "batchLaunchPlugin",
    "container-service-3.8.1-fat.jar": "containers",
    "dicom-query-retrieve-3.0.0-xpl.jar": "dicom-query-retrieve",
    "ohif-viewer-3.8.0-fat.jar": "ohifViewerPlugin",
}


def declared_ids(jars: Mapping[str, str | None] = PLUGIN_JARS) -> list[str]:
    """The plugin ids a set of fixture jars declares, sorted as the wait sorts them."""
    return sorted(plugin_id for plugin_id in jars.values() if plugin_id is not None)


def write_plugin_jar(directory: Path, filename: str, plugin_id: str | None) -> Path:
    """Write a jar laid out the way a real XNAT plugin jar is.

    Args:
        directory: Where to write it.
        filename: The jar's name.
        plugin_id: The id its plugin properties declare, or None for a library jar (a valid zip
            that declares no plugin).

    Returns:
        The jar's path.
    """
    path = directory / filename
    with zipfile.ZipFile(path, "w") as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        if plugin_id is not None:
            jar.writestr(
                f"META-INF/xnat/{plugin_id}-plugin.properties",
                f"id={plugin_id}\nclass=org.example.{plugin_id}.Plugin\nname={plugin_id}\n",
            )
    return path


def xnat_home(tmp_path: Path, jars: Mapping[str, str | None] = PLUGIN_JARS) -> Path:
    """Create an XNAT home whose plugins directory holds ``jars``; returns the home to set as XNAT_HOME."""
    home = tmp_path / "xnat-home"
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    for filename, plugin_id in jars.items():
        write_plugin_jar(plugins, filename, plugin_id)
    return home


def plugins_listing(plugin_ids: Iterable[str]) -> str:
    """A ``GET /xapi/plugins`` body listing ``plugin_ids``: a JSON object keyed by plugin id."""
    return json.dumps({plugin_id: {"id": plugin_id, "version": "1.0"} for plugin_id in plugin_ids})
