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

"""Tests for scripts/reconcile_ci_env.py — the state-mining helpers.

This tool reconstructs an environment's Terraform inputs from deployed state and
those values go on to seed a GitHub environment, so a wrong extraction is a wrong
production plan. The extraction helpers are pure functions over a state document,
which is what these exercise; the AWS calls are covered by running it for real
against an environment and diffing (`--compare`).
"""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reconcile_ci_env.py"
_spec = importlib.util.spec_from_file_location("reconcile_ci_env", SCRIPT)
rce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rce)


def _task_definition(name, containers, mode="managed"):
    return {
        "mode": mode,
        "type": "aws_ecs_task_definition",
        "name": name,
        "instances": [{"attributes": {"container_definitions": json.dumps(containers)}}],
    }


@pytest.fixture
def state():
    """A state document shaped like the real one, including the traps."""
    return rce.State(
        {
            "resources": [
                # The data source sorts first on purpose: aws_ecs_task_definition.flip_api
                # exists as both `data` and `managed` (the revision-tracking block in
                # ecs_services.tf), and a lookup that ignores `mode` picks this one —
                # which carries the *previously deployed* container definitions.
                _task_definition(
                    "flip_api",
                    [{"name": "flip-api", "image": "ghcr.io/londonaicentre/flip-api:STALE"}],
                    mode="data",
                ),
                _task_definition(
                    "flip_api",
                    [
                        {
                            "name": "flip-api",
                            "image": "ghcr.io/londonaicentre/flip-api:stag",
                            "portMappings": [{"containerPort": 8000}],
                            "environment": [
                                {"name": "POSTGRES_DB", "value": "centralhub"},
                                {"name": "FL_BACKEND", "value": "nvflare"},
                            ],
                        }
                    ],
                ),
                {
                    "mode": "managed",
                    "type": "aws_vpc",
                    "name": "this",
                    "instances": [{"attributes": {"tags": {"Name": "flip-vpc"}}}],
                },
                # The SSM bastion. Present in every environment, trust host or not —
                # which is why it is not a usable proxy for "a cloud trust exists".
                {
                    "mode": "managed",
                    "type": "aws_instance",
                    "name": "ec2_instance",
                    "instances": [{"attributes": {"instance_type": "t3.micro"}}],
                },
                {
                    "mode": "managed",
                    "type": "aws_security_group_rule",
                    "name": "local_trust_fl_server_nlb",
                    "instances": [
                        {"index_key": "203.0.113.9", "attributes": {}},
                        {"index_key": "198.51.100.4", "attributes": {}},
                    ],
                },
            ]
        }
    )


class TestStateLookup:
    def test_prefers_the_managed_resource_over_the_data_source(self, state):
        # The whole point: a `data` source and a `managed` resource share this
        # type and name, and the data source holds the older deployed definition.
        container = state.container("flip_api", "flip-api")
        assert container["image"].endswith(":stag")

    def test_missing_resource_yields_empty_rather_than_raising(self, state):
        assert state.attrs("aws_vpc", "nonexistent") == {}
        assert state.container("no_such_family", "x") == {}

    def test_reads_a_tag(self, state):
        assert state.attrs("aws_vpc", "this")["tags"]["Name"] == "flip-vpc"

    def test_recovers_for_each_keys(self, state):
        # LOCAL_TRUST_PUBLIC_IPS is only recoverable as the for_each keys of the
        # per-IP NLB ingress rules; losing them silently drops a trust's access.
        assert sorted(state.instance_keys("aws_security_group_rule", "local_trust_fl_server_nlb")) == [
            "198.51.100.4",
            "203.0.113.9",
        ]

    def test_absent_for_each_resource_yields_no_keys(self, state):
        assert state.instance_keys("aws_security_group_rule", "k8s_trust_fl_server_nlb") == []

    def test_data_attrs_reads_the_data_source_the_managed_lookup_skips(self, state):
        # The mirror of the mode trap: DEMO_ASSETS_BUCKET_NAME is only recoverable
        # from data.aws_s3_bucket.demo_assets, because the demo bucket is not
        # Terraform-managed. attrs() must not see it, data_attrs() must.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "data",
                        "type": "aws_s3_bucket",
                        "name": "demo_assets",
                        "instances": [{"attributes": {"bucket": "flipprod-demo-assets"}}],
                    }
                ]
            }
        )
        assert st.attrs("aws_s3_bucket", "demo_assets") == {}
        assert st.data_attrs("aws_s3_bucket", "demo_assets")["bucket"] == "flipprod-demo-assets"

    def test_data_attrs_ignores_the_managed_resource(self, state):
        # flip_ui is managed; asking data_attrs for it must not silently return it.
        assert state.data_attrs("aws_vpc", "this") == {}

    def test_module_resource_is_not_visible_to_the_root_lookup(self, state):
        # attrs() only walks the root module, so it cannot answer questions about
        # module.trust_ec2 — hence has_module_resource.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "managed",
                        "module": "module.trust_ec2[0]",
                        "type": "aws_instance",
                        "name": "trust_host",
                        "instances": [{"attributes": {}}],
                    }
                ]
            }
        )
        assert st.attrs("aws_instance", "trust_host") == {}
        assert st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")

    def test_the_root_bastion_does_not_imply_a_cloud_trust(self, state):
        # The regression. DEPLOY_TRUST_EC2 was derived from the root
        # aws_instance.ec2_instance, which exists everywhere, so it read "true"
        # on stag — an environment with no cloud trust at all. The recovered
        # value then planned a brand-new t3.xlarge trust host.
        assert state.attrs("aws_instance", "ec2_instance") != {}
        assert not state.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")

    def test_module_lookup_ignores_data_sources(self):
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "data",
                        "module": "module.trust_ec2[0]",
                        "type": "aws_instance",
                        "name": "trust_host",
                        "instances": [{"attributes": {}}],
                    }
                ]
            }
        )
        assert not st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")

    def test_module_lookup_rejects_a_resource_in_a_different_module(self, state):
        # module.trust_ec2_role sorts adjacent to module.trust_ec2 and is present
        # even in a hub-only deployment; a substring match would confuse them.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "managed",
                        "module": "module.trust_ec2_role",
                        "type": "aws_iam_role",
                        "name": "this",
                        "instances": [{"attributes": {}}],
                    }
                ]
            }
        )
        assert not st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")


class TestSplitImage:
    def test_splits_registry_prefix_and_tag(self):
        assert rce.split_image("ghcr.io/londonaicentre/flip-api:sha-badcff1") == (
            "ghcr.io/londonaicentre/",
            "sha-badcff1",
        )

    def test_digest_reference_yields_no_tag(self):
        # A digest-pinned image has no tag to reuse. Returning the digest
        # fragment would produce an unpullable `repo:sha256` reference.
        assert rce.split_image("ghcr.io/londonaicentre/flip-api@sha256:0123abcd") == ("", "")

    def test_empty_image_is_safe(self):
        assert rce.split_image("") == ("", "")


class TestContainerHelpers:
    def test_env_of_flattens_the_name_value_pairs(self, state):
        env = rce.env_of(state.container("flip_api", "flip-api"))
        assert env["POSTGRES_DB"] == "centralhub"
        assert env["FL_BACKEND"] == "nvflare"

    def test_first_port(self, state):
        assert rce.first_port(state.container("flip_api", "flip-api")) == "8000"

    def test_first_port_of_a_container_without_mappings(self):
        assert rce.first_port({"name": "x"}) == ""


class TestRedaction:
    @pytest.mark.parametrize("key", sorted(rce.SECRET_KEYS))
    def test_secret_values_are_never_shown(self, key):
        # The summary is printed to a terminal and pasted into tickets; the
        # digest is what makes two copies comparable without disclosing either.
        out = rce.shown(key, "a-real-secret-value")
        assert "a-real-secret-value" not in out
        assert out.startswith("sha256:")

    def test_non_secret_values_are_shown_verbatim(self):
        assert rce.shown("VPC_NAME", "flip-vpc") == "flip-vpc"

    def test_empty_is_labelled_not_digested(self):
        assert rce.shown("AES_KEY_BASE64", "") == "(empty)"


class TestEnvFileParsing:
    def test_reads_keys_and_keeps_values_verbatim(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text(
            '# comment\n'
            'FL_KIT_SLOT_NAMES=["Trust_1", "Trust_2"]\n'
            "EMPTY=\n"
            "not a variable line\n"
        )
        parsed = rce.read_env_file(str(f))
        # The HCL list literal must survive intact — it is handed to Terraform
        # as a list, not a string.
        assert parsed["FL_KIT_SLOT_NAMES"] == '["Trust_1", "Trust_2"]'
        assert parsed["EMPTY"] == ""
        assert "not a variable line" not in parsed
