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
import sys
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

    def test_module_lookup_rejects_a_resource_in_a_different_module(self):
        # The prefix must match on a module boundary. `module.trust_ec2_role` is
        # present even in a hub-only deployment, so a bare startswith() answers
        # "yes, a cloud trust exists" for an environment that has none — the same
        # false "true" the root-bastion lookup used to give.
        #
        # The type and name here deliberately MATCH what is being asked for, so
        # the module check is the only thing that can reject it. A fixture that
        # differed in type or name would pass whatever the prefix logic did.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "managed",
                        "module": "module.trust_ec2_role",
                        "type": "aws_instance",
                        "name": "trust_host",
                        "instances": [{"attributes": {"instance_type": "t3.xlarge"}}],
                    }
                ]
            }
        )
        assert not st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")

    @pytest.mark.parametrize("module", ["module.trust_ec2", "module.trust_ec2[0]", "module.trust_ec2.module.inner"])
    def test_module_lookup_accepts_every_real_address_shape(self, module):
        # count/for_each add `[0]`, nesting adds `.module.<name>`; all three are
        # the module being asked about.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "managed",
                        "module": module,
                        "type": "aws_instance",
                        "name": "trust_host",
                        "instances": [{"attributes": {}}],
                    }
                ]
            }
        )
        assert st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")


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


# The state-mining helpers above are pure functions, and testing them alone left
# the two entry points — build() and main() — with no coverage at all: reverting
# either the module-boundary fix or the DEMO_ASSETS_BUCKET_NAME one still left the
# suite green. These drive both, with every AWS call stubbed.


def _minimal_state(*, with_trust_host: bool, demo_bucket: str | None) -> dict:
    """Just enough state for build() to run end to end."""
    resources: list[dict] = [
        _task_definition(
            "flip_api",
            [
                {
                    "name": "flip-api",
                    "image": "ghcr.io/londonaicentre/flip-api:prod",
                    "portMappings": [{"containerPort": 8000}],
                    "environment": [
                        {"name": "POSTGRES_DB", "value": "centralhub"},
                        {"name": "POSTGRES_USER", "value": "flipuser"},
                        {"name": "FL_BACKEND", "value": "nvflare"},
                    ],
                }
            ],
        ),
        _task_definition(
            "fl_api_net_1",
            [{"name": "fl-api-net-1", "image": "ghcr.io/londonaicentre/flare-fl-api:prod"}],
        ),
        _task_definition(
            "fl_server_net_1",
            [{"name": "fl-server-net-1", "image": "ghcr.io/londonaicentre/flare-fl-server:prod"}],
        ),
        {
            "mode": "managed",
            "type": "aws_vpc",
            "name": "this",
            "instances": [{"attributes": {"tags": {"Name": "flip-vpc"}}}],
        },
        # The SSM bastion, present whether or not a cloud trust is deployed.
        {
            "mode": "managed",
            "type": "aws_instance",
            "name": "ec2_instance",
            "instances": [{"attributes": {}}],
        },
    ]
    if with_trust_host:
        resources.append(
            {
                "mode": "managed",
                "module": "module.trust_ec2[0]",
                "type": "aws_instance",
                "name": "trust_host",
                "instances": [{"attributes": {}}],
            }
        )
    else:
        # Present in a hub-only deployment, and the near-miss the module-boundary
        # check has to reject.
        resources.append(
            {
                "mode": "managed",
                "module": "module.trust_ec2_role",
                "type": "aws_iam_role",
                "name": "this",
                "instances": [{"attributes": {}}],
            }
        )
    if demo_bucket is not None:
        resources.append(
            {
                "mode": "data",
                "type": "aws_s3_bucket",
                "name": "demo_assets",
                "instances": [{"attributes": {"bucket": demo_bucket}}],
            }
        )
    return {"resources": resources}


@pytest.fixture
def stub_aws(monkeypatch):
    """Stub every AWS call build() makes. Returns a mutable state-document dict."""
    doc = {"resources": []}

    class _Completed:
        def __init__(self, stdout):
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(argv, **kwargs):
        # The only direct subprocess.run in build() is the state download.
        assert argv[:3] == ["aws", "s3", "cp"], argv
        return _Completed(json.dumps(doc))

    def fake_aws(args, profile, region):
        if args[0] == "secretsmanager":
            return json.dumps(
                {
                    "aes_key": "not-a-real-key",  # pragma: allowlist secret
                    "internal_service_key": "not-a-real-service-key",  # pragma: allowlist secret
                    "internal_service_key_hash": "0" * 64,
                }
            )
        if args[0] == "ssm":
            return '["Trust_1", "Trust_2"]'
        if args[0] == "ecs":
            # describe-services then describe-task-definition; the live tag is a
            # sha pin, which is exactly what must survive into the output.
            return "flip-api:41" if args[1] == "describe-services" else "ghcr.io/londonaicentre/flip-api:sha-abc1234"
        raise AssertionError(f"unexpected aws call: {args}")

    monkeypatch.setattr(rce.subprocess, "run", fake_run)
    monkeypatch.setattr(rce, "aws", fake_aws)
    return doc


class TestBuild:
    def test_hub_only_deployment_recovers_deploy_trust_ec2_false(self, stub_aws):
        stub_aws.update(_minimal_state(with_trust_host=False, demo_bucket=None))
        values, _ = rce.build("stag", "stag", "eu-west-2", "flip-terraform-state-stag", "flip-cluster")
        # The regression, at the level the tool actually runs: staging has a
        # bastion and a trust_ec2_role but no trust host, and must not be told to
        # create a t3.xlarge one.
        assert values["DEPLOY_TRUST_EC2"] == "false"

    def test_a_deployed_trust_host_recovers_true(self, stub_aws):
        stub_aws.update(_minimal_state(with_trust_host=True, demo_bucket=None))
        values, _ = rce.build("prod", "prod", "eu-west-2", "flip-terraform-state-prod", "flip-cluster")
        assert values["DEPLOY_TRUST_EC2"] == "true"

    def test_live_image_tag_beats_the_tag_recorded_in_state(self, stub_aws):
        # State records `:prod` (the bootstrap default); the service is running a
        # sha pin registered outside Terraform by deploy-centralhub. Reading state
        # here would hand CI the mutable tag and undo the FLIP#751 pin.
        stub_aws.update(_minimal_state(with_trust_host=False, demo_bucket=None))
        values, sources = rce.build("prod", "prod", "eu-west-2", "flip-terraform-state-prod", "flip-cluster")
        assert values["DOCKER_TAG"] == "sha-abc1234"
        assert sources["DOCKER_TAG"] == "live ECS service"

    def test_demo_bucket_comes_from_the_data_source(self, stub_aws):
        stub_aws.update(_minimal_state(with_trust_host=True, demo_bucket="flipprod-demo-assets"))
        values, _ = rce.build("prod", "prod", "eu-west-2", "flip-terraform-state-prod", "flip-cluster")
        assert values["DEMO_ASSETS_BUCKET_NAME"] == "flipprod-demo-assets"


class TestExpectedEmpty:
    def test_demo_bucket_may_be_empty_on_stag(self):
        assert "DEMO_ASSETS_BUCKET_NAME" in rce.keys_expected_empty("stag")

    def test_demo_bucket_may_not_be_empty_on_prod(self):
        # Prod carries the Ark+ demo, and cloudfront.tf gates four resources plus
        # the /ark_demo/* behaviour on the value being non-empty. An empty
        # recovery there is a failed lookup, and seeding from it destroys them.
        assert "DEMO_ASSETS_BUCKET_NAME" not in rce.keys_expected_empty("prod")

    def test_enforce_mfa_may_be_empty_anywhere(self):
        assert "ENFORCE_MFA" in rce.keys_expected_empty("prod")
        assert "ENFORCE_MFA" in rce.keys_expected_empty("stag")


class TestMain:
    @staticmethod
    def _run(monkeypatch, env, values, extra_argv=()):
        monkeypatch.setattr(rce, "build", lambda *a, **k: (dict(values), {}))
        monkeypatch.setattr(sys, "argv", ["reconcile_ci_env.py", "--env", env, *extra_argv])
        rce.main()

    BASE = {
        "AWS_REGION": "eu-west-2",
        "FL_BACKEND": "nvflare",
        "FLARE_KIT_DATE": "20260512",
        "FLOWER_KIT_DATE": "",
        "ENFORCE_MFA": "",
        "VPC_NAME": "flip-vpc",
        "DEMO_ASSETS_BUCKET_NAME": "",
    }

    def test_prod_reports_an_absent_demo_bucket_as_not_recovered(self, monkeypatch, capsys):
        self._run(monkeypatch, "prod", self.BASE)
        out = capsys.readouterr().out
        assert "Not recovered" in out
        assert "DEMO_ASSETS_BUCKET_NAME" in out.split("Not recovered", 1)[1]

    def test_stag_does_not_flag_an_absent_demo_bucket(self, monkeypatch, capsys):
        self._run(monkeypatch, "stag", self.BASE)
        out = capsys.readouterr().out
        # Nothing is missing on stag: the only empties are the legitimately empty
        # ones, so the warning block must not appear at all.
        assert "Not recovered" not in out

    def test_only_the_running_backends_kit_date_is_required(self, monkeypatch, capsys):
        # Only one backend is ever provisioned, so the other's kit date is
        # legitimately empty. A genuinely missing key alongside it proves the
        # warning block is being produced at all.
        values = {**self.BASE, "VPC_NAME": ""}
        self._run(monkeypatch, "stag", values)
        warning = capsys.readouterr().out.split("Not recovered", 1)[1]
        assert "VPC_NAME" in warning
        assert "FLOWER_KIT_DATE" not in warning

    def test_out_omits_an_unrecovered_prod_demo_bucket(self, monkeypatch, tmp_path):
        out_file = tmp_path / "recovered.env"
        self._run(monkeypatch, "prod", self.BASE, ("--out", str(out_file)))
        written = rce.read_env_file(str(out_file))
        # Writing it blank would have setup-github-environments.sh file it under
        # "optional, which is fine" and seed prod without it.
        assert "DEMO_ASSETS_BUCKET_NAME" not in written
        assert written["ENFORCE_MFA"] == ""

    def test_out_writes_an_empty_demo_bucket_on_stag(self, monkeypatch, tmp_path):
        out_file = tmp_path / "recovered.env"
        self._run(monkeypatch, "stag", self.BASE, ("--out", str(out_file)))
        assert rce.read_env_file(str(out_file))["DEMO_ASSETS_BUCKET_NAME"] == ""

    def test_out_refuses_to_overwrite(self, monkeypatch, tmp_path):
        out_file = tmp_path / "recovered.env"
        out_file.write_text("TRUST_API_KEY=irreplaceable\n")
        with pytest.raises(SystemExit):
            self._run(monkeypatch, "stag", self.BASE, ("--out", str(out_file)))
        assert "irreplaceable" in out_file.read_text()

    def test_out_is_written_0600(self, monkeypatch, tmp_path):
        out_file = tmp_path / "recovered.env"
        self._run(monkeypatch, "stag", self.BASE, ("--out", str(out_file)))
        assert oct(out_file.stat().st_mode & 0o777) == "0o600"
