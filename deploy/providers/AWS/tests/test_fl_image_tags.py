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

"""The two FL images carry their own tags (FLIP#1283).

A release apply pins `v<X.Y.Z>@sha256:…`, and a digest belongs to ONE
repository. While `fl_api_image` and `fl_server_image` were built from the same
`var.flip_fl_image_tag`, a release apply put fl-server's digest on
`flare-fl-api`: ECS pulls by digest and ignores the tag, so fl-api-net-1 fails
with CannotPullContainer after a green resolve and a green apply, and
`active_tag` reads the bad reference back on every later infrastructure-only
apply.

These are source-level assertions because the failure is invisible in a plan
diff — the reference is well-formed, it just does not exist.
"""

import re
from pathlib import Path

AWS_DIR = Path(__file__).resolve().parent.parent
ECS_TASKS = (AWS_DIR / "ecs_tasks.tf").read_text()
VARIABLES = (AWS_DIR / "variables.tf").read_text()
MAKEFILE = (AWS_DIR / "Makefile").read_text()
RESOLVER = (AWS_DIR / "scripts" / "resolve-image-tags.sh").read_text()


def _local(name: str) -> str:
    match = re.search(rf"^\s*{name}\s*=\s*(.+)$", ECS_TASKS, re.MULTILINE)
    assert match, f"local.{name} not found in ecs_tasks.tf"
    return match.group(1).strip()


class TestTerraformWiring:
    def test_fl_api_image_does_not_use_the_server_tag(self):
        assert "var.flip_fl_image_tag" not in _local("fl_api_image")

    def test_fl_api_image_uses_its_own_resolved_tag(self):
        assert "local.fl_api_tag" in _local("fl_api_image")
        assert "var.fl_api_name" in _local("fl_api_image")

    def test_fl_server_image_still_uses_the_server_tag(self):
        assert "var.flip_fl_image_tag" in _local("fl_server_image")
        assert "var.fl_server_name" in _local("fl_server_image")

    def test_an_unset_fl_api_tag_falls_back_to_the_server_tag(self):
        # Every caller that does not pin by digest — a laptop apply, an env file,
        # a plain sha-tag CI apply — leaves the variable empty and must keep the
        # pre-FLIP#1283 behaviour exactly.
        expr = _local("fl_api_tag")
        assert 'var.fl_api_image_tag != ""' in expr
        assert "var.flip_fl_image_tag" in expr

    def test_the_variable_is_declared_with_an_empty_default(self):
        block = re.search(r'variable "fl_api_image_tag" \{(.*?)\n\}', VARIABLES, re.DOTALL)
        assert block, "variable fl_api_image_tag is not declared"
        assert 'default     = ""' in block.group(1)
        # Same `latest` refusal as the other two image-tag variables.
        assert "must not be 'latest'" in block.group(1)


class TestResolverWiring:
    def test_both_fl_repositories_are_resolved(self):
        assert 'resolve "FL server image"' in RESOLVER
        assert 'resolve "FL API image"' in RESOLVER

    def test_the_fl_api_resolves_against_its_own_service(self):
        line = next(line for line in RESOLVER.splitlines() if line.startswith('resolve "FL API image"'))
        assert '"fl-api-net-1" "fl-api-net-1"' in line
        assert "FL_API_IMAGE" in line

    def test_the_image_names_match_fl_backend_mk(self):
        # deploy/fl_backend.mk's DOCKER_FL_API_NAME, which is what Terraform
        # receives as var.fl_api_name. A mismatch probes a repository that does
        # not exist and silently takes the fallback path.
        backend_mk = (AWS_DIR.parent.parent / "fl_backend.mk").read_text()
        for name in ("flare-fl-api", "flower-fl-api", "flare-fl-server", "flower-superlink"):
            assert name in backend_mk, f"{name} is not in deploy/fl_backend.mk"
            assert f'"{name}"' in RESOLVER, f"{name} is not resolved by resolve-image-tags.sh"

    def test_the_third_output_is_emitted(self):
        assert "DOCKER_FL_API_TAG=${fl_api_tag}" in RESOLVER

    def test_the_digest_is_the_top_level_descriptor(self):
        # `docker manifest inspect --verbose` reports a PER-PLATFORM digest for a
        # manifest list (the first entry is often an unknown/unknown
        # attestation); pinning one of those pins a single-platform artefact.
        assert "buildx imagetools inspect" in RESOLVER
        assert "{{.Manifest.Digest}}" in RESOLVER
        code = [line for line in RESOLVER.splitlines() if not line.lstrip().startswith("#")]
        assert not any("manifest inspect --verbose" in line for line in code)


class TestMakefileWiring:
    def test_the_tf_var_is_exported_only_when_set(self):
        # An unconditional export turns an absent key into TF_VAR_…="" — which is
        # the variable's default anyway here, but the guarded form keeps the
        # "only CI sets this" contract legible and matches the other optional
        # exports.
        assert "ifneq ($(DOCKER_FL_API_TAG),)" in MAKEFILE
        assert "export TF_VAR_fl_api_image_tag=${DOCKER_FL_API_TAG}" in MAKEFILE
