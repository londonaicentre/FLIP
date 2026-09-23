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
"""The ``docker_build_*.yml`` image workflows build and push ``:v<X.Y.Z>`` at a release tag (FLIP#1204).

A release is one tag across the whole stack, so every image is rebuilt at the release commit,
changed or not. Read as text, so a workflow that drops the trigger fails here rather than at the
next release. ``docker_build_flip_ui.yml`` publishes nothing and is not on the roster.

Usage:
    python3 .github/tests/workflows/test_docker_build.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_workflows import DOCKER_BUILD_WORKFLOWS, ReleaseTagContract  # noqa: E402


class DockerBuildReleaseTags(ReleaseTagContract, unittest.TestCase):
    workflows = DOCKER_BUILD_WORKFLOWS

    def test_roster_is_not_empty(self) -> None:
        assert len(self.workflows) >= 10, [p.name for p in self.workflows]


if __name__ == "__main__":
    unittest.main()
