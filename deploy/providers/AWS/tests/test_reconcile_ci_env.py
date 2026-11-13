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
                # The SSM bastion.
                {
                    "mode": "managed",
                    "type": "aws_instance",
                    "name": "ec2_instance",
                    "instances": [{"attributes": {"instance_type": "t3.micro"}}],
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

    def test_module_attrs_distinguishes_buckets_that_share_a_type_and_name(self, state):
        # Every caller of modules/flip_s3_bucket contributes an
        # `aws_s3_bucket.this`, so the module is the only thing that tells them apart.
        st = rce.State(
            {
                "resources": [
                    {
                        "mode": "managed",
                        "module": "module.flip_app_bundles_bucket",
                        "type": "aws_s3_bucket",
                        "name": "this",
                        "instances": [{"attributes": {"bucket": "flip-lza-app-bundles"}}],
                    },
                    {
                        "mode": "managed",
                        "module": "module.flip_fl_results_bucket[0]",
                        "type": "aws_s3_bucket",
                        "name": "this",
                        "instances": [{"attributes": {"bucket": "flip-lza-fl-results"}}],
                    },
                ]
            }
        )
        assert st.module_attrs("module.flip_fl_results_bucket", "aws_s3_bucket", "this")["bucket"] == (
            "flip-lza-fl-results"
        )
        # A module boundary: module.flip_fl never matches module.flip_fl_results_bucket.
        assert st.module_attrs("module.flip_fl", "aws_s3_bucket", "this") == {}


class TestSplitImage:
    @pytest.mark.parametrize(
        ("image", "expected"),
        [
            pytest.param(
                "ghcr.io/londonaicentre/flip-api:sha-badcff1", ("ghcr.io/londonaicentre/", "sha-badcff1"), id="tag"
            ),
            # A digest-pinned image has no tag to reuse. Returning the digest
            # fragment would produce an unpullable `repo:sha256` reference.
            pytest.param("ghcr.io/londonaicentre/flip-api@sha256:0123abcd", ("", ""), id="digest-only"),
            pytest.param("", ("", ""), id="empty"),
            # A release apply pins `:v<X.Y.Z>@sha256:…` (FLIP#1283). The tag part is
            # the whole `v<X.Y.Z>@sha256:…` string, because that is what Terraform
            # has to re-emit to keep the pin: reporting only `v1.2.3` would silently
            # un-pin the digest on the next apply.
            pytest.param(
                "ghcr.io/londonaicentre/flip-api:v1.2.3@sha256:" + "a" * 64,
                ("ghcr.io/londonaicentre/", "v1.2.3@sha256:" + "a" * 64),
                id="release-digest-pin-whole",
            ),
            # No colon at all, or only the registry port's: neither carries a tag to reuse.
            pytest.param("ghcr.io/londonaicentre/flip-api", ("", ""), id="untagged"),
            pytest.param("registry.example:5000/flip-api", ("", ""), id="registry-port-only"),
        ],
    )
    def test_split_image(self, image, expected):
        assert rce.split_image(image) == expected


class TestLiveImageTag:
    """The env-file value, which is NOT the task definition's reference.

    `DOCKER_TAG` in an operator env file is copied into every trust kit by
    sync-trust-kit, where it tags the trust images (trust-api, imaging-api,
    data-access-api, omop-db, orthanc). A digest belongs to one repository, so
    writing flip-api's `v1.2.3@sha256:…` there makes every one of those images
    unpullable. CI re-resolves the digest pin from live ECS on every run, so the
    tag alone is the right record here.
    """

    @staticmethod
    def _tag(monkeypatch, image: str) -> str:
        calls = iter(["arn:aws:ecs:::task-definition/flip-api:41", image])
        monkeypatch.setattr(rce, "aws", lambda *a, **k: next(calls))
        return rce.live_image_tag("flip-api", "flip-api", "flip-cluster", "p", "r")

    def test_plain_tag_is_unchanged(self, monkeypatch):
        assert self._tag(monkeypatch, "ghcr.io/londonaicentre/flip-api:sha-badcff1") == "sha-badcff1"

    def test_release_digest_pin_is_written_as_the_tag_alone(self, monkeypatch):
        assert self._tag(monkeypatch, "ghcr.io/londonaicentre/flip-api:v1.2.3@sha256:" + "a" * 64) == "v1.2.3"

    def test_bare_digest_reference_yields_nothing(self, monkeypatch):
        assert self._tag(monkeypatch, "ghcr.io/londonaicentre/flip-api@sha256:" + "a" * 64) == ""


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
        assert rce.shown("SES_VERIFIED_EMAIL", "noreply@example.com") == "noreply@example.com"

    def test_empty_is_labelled_not_digested(self):
        assert rce.shown("AES_KEY_BASE64", "") == "(empty)"


class TestEnvFileParsing:
    def test_reads_keys_and_keeps_values_verbatim(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text('# comment\nFL_KIT_SLOT_NAMES=["Trust_1", "Trust_2"]\nEMPTY=\nnot a variable line\n')
        parsed = rce.read_env_file(str(f))
        # The HCL list literal must survive intact — it is handed to Terraform
        # as a list, not a string.
        assert parsed["FL_KIT_SLOT_NAMES"] == '["Trust_1", "Trust_2"]'
        assert parsed["EMPTY"] == ""
        assert "not a variable line" not in parsed


# The state-mining helpers above are pure functions, and testing them alone left
# the two entry points — build() and main() — with no coverage at all: reverting
# the module-boundary fix still left the
# suite green. These drive both, with every AWS call stubbed.


#: compose-ci-env.sh's LZA_REQUIRED_KEYS, plus the two subdomains its REQUIRED_KEYS carries.
LZA_KEYS = (
    "ACCESS_LOGS_BUCKET_NAME",
    "EFS_PROVISION_IMAGE",
    "LZA_VPC_NAME",
    "MANAGE_DNS",
    "NETWORKING_INGRESS_CIDRS",
    "ALB_SUBDOMAIN",
    "NLB_SUBDOMAIN",
)


#: The LZA keys as an estate's state holds them (pre-FLIP#1479 addresses, with their [0]).
LZA_STATE = [
    {
        "mode": "data",
        "type": "aws_vpc",
        "name": "lza",
        "instances": [
            {"index_key": 0, "attributes": {"cidr_block": "10.20.0.0/16", "tags": {"Name": "AWSAccelerator-vpc"}}}
        ],
    },
    {
        "mode": "managed",
        "module": "module.fl_internal_nlb_security_group[0]",
        "type": "aws_security_group_rule",
        "name": "ingress",
        "instances": [{"index_key": "8002", "attributes": {"cidr_blocks": ["10.20.0.0/16", "10.12.0.0/24"]}}],
    },
    {
        "mode": "managed",
        "type": "aws_s3_bucket",
        "name": "flip_access_logs",
        "instances": [{"attributes": {"bucket": "flip-lza-access-logs"}}],
    },
    {
        "mode": "managed",
        "type": "aws_cloudfront_origin_access_control",
        "name": "flip_ui",
        "instances": [{"attributes": {"name": "flip-ui-app.flip.example"}}],
    },
    _task_definition(
        "efs_provision",
        [{"name": "provision-efs-certs", "image": "123.dkr.ecr.eu-west-2.amazonaws.com/ecr-public/aws-cli/aws-cli:2"}],
    ),
]


def _minimal_state(*, lza: bool = False) -> dict:
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
        # The SSM bastion.
        {
            "mode": "managed",
            "type": "aws_instance",
            "name": "ec2_instance",
            "instances": [{"attributes": {}}],
        },
    ]
    if lza:
        resources += LZA_STATE
    return {"resources": resources, "outputs": {"FlServerEndpoint": {"value": "fl.flip.example"}} if lza else {}}


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
                    "fl_api_key": "not-a-real-fl-api-key",  # pragma: allowlist secret
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
    def test_live_image_tag_beats_the_tag_recorded_in_state(self, stub_aws):
        # State records `:prod` (the bootstrap default); the service is running a
        # sha pin registered outside Terraform by deploy-centralhub. Reading state
        # here would hand CI the mutable tag and undo the FLIP#751 pin.
        stub_aws.update(_minimal_state())
        values, sources = rce.build("prod", "prod", "eu-west-2", "flip-terraform-state-prod", "flip-cluster")
        assert values["DOCKER_TAG"] == "sha-abc1234"
        assert sources["DOCKER_TAG"] == "live ECS service"

    def test_the_keys_every_estate_requires_come_from_state(self, stub_aws):
        stub_aws.update(_minimal_state(lza=True))
        values, _ = rce.build("stag", "lza-stag", "eu-west-2", "flip-terraform-state-lza-stag", "flip-cluster")
        assert {key: values[key] for key in LZA_KEYS} == {
            "ACCESS_LOGS_BUCKET_NAME": "flip-lza-access-logs",
            "EFS_PROVISION_IMAGE": "123.dkr.ecr.eu-west-2.amazonaws.com/ecr-public/aws-cli/aws-cli:2",
            "LZA_VPC_NAME": "AWSAccelerator-vpc",
            "MANAGE_DNS": "false",  # no certificate in state
            "NETWORKING_INGRESS_CIDRS": '["10.12.0.0/24"]',  # the rule's sources less the VPC's own
            "ALB_SUBDOMAIN": "app.flip.example",
            "NLB_SUBDOMAIN": "fl.flip.example",
        }

    def test_a_certificate_in_state_means_dns_is_managed(self, stub_aws):
        stub_aws.update(_minimal_state(lza=True))
        stub_aws["resources"].append(
            {
                "mode": "managed",
                "type": "aws_acm_certificate",
                "name": "flip",
                "instances": [{"attributes": {"id": "c"}}],
            }
        )
        values, _ = rce.build("prod", "lza-prod", "eu-west-2", "flip-terraform-state-lza", "flip-cluster")
        assert values["MANAGE_DNS"] == "true"

    def test_keys_state_cannot_answer_are_left_empty_to_be_flagged(self, stub_aws):
        """Empty is what main() reports as "Not recovered — set these by hand"."""
        stub_aws.update(_minimal_state())
        values, _ = rce.build("prod", "lza-prod", "eu-west-2", "flip-terraform-state-lza", "flip-cluster")
        for key in LZA_KEYS:
            if key != "MANAGE_DNS":
                assert values[key] == "", key
        assert not rce.keys_expected_empty() & set(LZA_KEYS)


class TestExpectedEmpty:
    def test_enforce_mfa_may_be_empty(self):
        assert rce.keys_expected_empty() == {"ENFORCE_MFA"}


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
        "SES_VERIFIED_EMAIL": "noreply@example.com",
    }

    @pytest.mark.parametrize(
        ("argv", "profile", "bucket"),
        [
            (("--env", "prod"), "prod", "flip-terraform-state-lza"),
            (("--env", "stag"), "stag", "flip-terraform-state-lza-stag"),
            # Another estate points them elsewhere.
            (("--env", "prod", "--profile", "own-prod", "--bucket", "own-state"), "own-prod", "own-state"),
        ],
    )
    def test_env_defaults_to_the_lza_estates(self, monkeypatch, argv, profile, bucket):
        seen = {}

        def fake_build(env, profile, region, bucket, cluster):
            seen.update(profile=profile, bucket=bucket)
            return dict(self.BASE), {}

        monkeypatch.setattr(rce, "build", fake_build)
        monkeypatch.setattr(sys, "argv", ["reconcile_ci_env.py", *argv])
        rce.main()
        assert seen == {"profile": profile, "bucket": bucket}

    def test_legitimate_empties_raise_no_warning(self, monkeypatch, capsys):
        self._run(monkeypatch, "stag", self.BASE)
        out = capsys.readouterr().out
        # The only empties are the legitimately empty ones, so the warning block
        # must not appear at all.
        assert "Not recovered" not in out

    def test_only_the_running_backends_kit_date_is_required(self, monkeypatch, capsys):
        # Only one backend is ever provisioned, so the other's kit date is
        # legitimately empty. A genuinely missing key alongside it proves the
        # warning block is being produced at all.
        values = {**self.BASE, "SES_VERIFIED_EMAIL": ""}
        self._run(monkeypatch, "stag", values)
        warning = capsys.readouterr().out.split("Not recovered", 1)[1]
        assert "SES_VERIFIED_EMAIL" in warning
        assert "FLOWER_KIT_DATE" not in warning

    def test_out_writes_a_legitimately_empty_key(self, monkeypatch, tmp_path):
        out_file = tmp_path / "recovered.env"
        self._run(monkeypatch, "prod", self.BASE, ("--out", str(out_file)))
        assert rce.read_env_file(str(out_file))["ENFORCE_MFA"] == ""

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
