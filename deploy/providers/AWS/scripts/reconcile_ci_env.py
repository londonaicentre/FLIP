#!/usr/bin/env python3
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

"""Rebuild an environment's Terraform inputs from what is actually deployed.

The Terraform CI pipeline (FLIP#962) needs every ``TF_VAR_*`` input present as a
GitHub environment secret or variable. The nominal source is the operator's
``.env.stag`` / ``.env.production``, but those files live on laptops, drift, and
in at least one observed case were months stale — a copy that still named a
renamed S3 bucket, which plans as ``must be replaced`` on a bucket carrying
``prevent_destroy``. Seeding GitHub from a stale file would bake that drift into
every future CI plan.

So this derives the values from the deployed infrastructure instead: the live
Terraform state, plus the handful of things state cannot answer (the current
image tags, the FL kit-slot list, the live secret material). What comes out is
what a plan would have to see to be a no-op — which is the definition of correct
for this purpose.

Distinct from ``update_env.py`` next door, which pushes Terraform *outputs*
(DB endpoint, Cognito IDs) back into an env file after an apply. This recovers
Terraform *inputs*.

Secret values are written to the output file but never printed: the summary
shows a truncated SHA-256 so two copies can be compared without either being
disclosed.

Usage:
    scripts/reconcile_ci_env.py --env prod --profile prod --out ../../../.env.production
    scripts/reconcile_ci_env.py --env stag --profile stag --compare ../../../.env.stag
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

# Written to the output file, never echoed. Anything whose name matches is
# summarised as a digest.
#
# POSTGRES_USER and POSTGRES_DB are deliberately absent: they are configuration,
# not credentials (../variables.tf says why at length), they are stored as GitHub
# environment variables rather than secrets, and they are rendered in the clear
# into every plan comment. Digesting them here would only make this tool's output
# harder to compare against a plan for no protection at all.
SECRET_KEYS = {
    "ADMIN_USER_PASSWORD",
    "AES_KEY_BASE64",
    "INTERNAL_SERVICE_KEY",
    "INTERNAL_SERVICE_KEY_HASH",
}


def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(1)


def aws(args: list[str], profile: str, region: str) -> str:
    """Run an AWS CLI command, returning stdout ('' on failure)."""
    proc = subprocess.run(
        ["aws", *args, "--profile", profile, "--region", region],
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


class State:
    """Read-only view of a Terraform state document."""

    def __init__(self, doc: dict) -> None:
        self.doc = doc

    def attrs(self, rtype: str, name: str) -> dict:
        # `mode` matters: a `data` source and a `managed` resource can share a
        # type and name (aws_ecs_task_definition.flip_api is both, by design —
        # see the revision-tracking block in ecs_services.tf). Matching without
        # it silently returns whichever came first in the file.
        for r in self.doc.get("resources", []):
            if r.get("mode") == "managed" and r.get("type") == rtype and r.get("name") == name:
                instances = r.get("instances") or []
                if instances:
                    return instances[0].get("attributes") or {}
        return {}

    def instance_keys(self, rtype: str, name: str) -> list[str]:
        """index_key of every instance — how a for_each map's keys are recovered."""
        for r in self.doc.get("resources", []):
            if r.get("mode") == "managed" and r.get("type") == rtype and r.get("name") == name:
                return [i.get("index_key") for i in r.get("instances", []) if i.get("index_key") is not None]
        return []

    def data_attrs(self, rtype: str, name: str) -> dict:
        """Attributes of a `data` source — the mirror of `attrs`.

        Needed where the deployed value only exists as a lookup: the Ark+ demo
        bucket is not Terraform-managed, so `data.aws_s3_bucket.demo_assets` is
        the only record of the name that was passed in.
        """
        for r in self.doc.get("resources", []):
            if r.get("mode") == "data" and r.get("type") == rtype and r.get("name") == name:
                instances = r.get("instances") or []
                if instances:
                    return instances[0].get("attributes") or {}
        return {}

    def has_module_resource(self, module_prefix: str, rtype: str, name: str) -> bool:
        """Whether a managed resource of this type/name exists inside a module.

        `attrs` does *not* filter on `module` — it matches a type/name pair
        wherever it sits, which is why the Cognito and SES module resources
        recover through it perfectly well. The bug this method was written for
        was not a missed module but a **wrong resource name**: `DEPLOY_TRUST_EC2`
        was read from the root's `aws_instance.ec2_instance`, which is the SSM
        bastion and exists in every environment, so the answer came back "true"
        whether or not a trust host was deployed. Observed on stag, which runs no
        cloud trust: the recovered value said `DEPLOY_TRUST_EC2=true` and the
        resulting plan created one.

        The right resource is `module.trust_ec2[0].aws_instance.trust_host`, and
        this method exists to say *which module* as well as which name — the
        module is the part that distinguishes it, so it is checked rather than
        ignored.

        The prefix has to match on a module boundary. A plain `startswith` would
        accept `module.trust_ec2_role`, which is present even in a hub-only
        deployment, so the very lookup that fixed one false "true" would hand
        back another.
        """
        for r in self.doc.get("resources", []):
            if r.get("mode") != "managed" or r.get("type") != rtype or r.get("name") != name:
                continue
            module = r.get("module") or ""
            # `module.trust_ec2`, `module.trust_ec2[0]`, `module.trust_ec2.module.inner`
            # — but never `module.trust_ec2_role`.
            if module == module_prefix or module.startswith((f"{module_prefix}[", f"{module_prefix}.")):
                return bool(r.get("instances"))
        return False

    def containers(self, task_family: str) -> list[dict]:
        raw = self.attrs("aws_ecs_task_definition", task_family).get("container_definitions")
        return json.loads(raw) if raw else []

    def container(self, task_family: str, container_name: str) -> dict:
        for c in self.containers(task_family):
            if c.get("name") == container_name:
                return c
        return {}


def env_of(container: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in (container.get("environment") or [])}


def first_port(container: dict) -> str:
    ports = container.get("portMappings") or []
    return str(ports[0]["containerPort"]) if ports else ""


def split_image(image: str) -> tuple[str, str]:
    """`ghcr.io/org/name:tag` -> ('ghcr.io/org/', 'tag'). Digest refs yield no tag."""
    if not image or "@" in image:
        return "", ""
    repo, _, tag = image.rpartition(":")
    prefix = repo.rsplit("/", 1)[0] + "/" if "/" in repo else ""
    return prefix, tag


def live_image_tag(service: str, container: str, cluster: str, profile: str, region: str) -> str:
    """Tag on the task definition the service is running *now*.

    State records the tag Terraform last applied, which is the bootstrap default
    (`:stag` / `:prod`). `make deploy-centralhub` registers newer revisions
    outside Terraform and the service tracks max(terraform, live), so state and
    reality legitimately disagree. Reality is what a CI apply must preserve —
    reading state here would hand CI the mutable tag and undo the FLIP#751 pin.
    """
    task_def = aws(
        ["ecs", "describe-services", "--cluster", cluster, "--services", service,
         "--query", "services[0].taskDefinition", "--output", "text"],
        profile, region,
    )
    if not task_def or task_def == "None":
        return ""
    image = aws(
        ["ecs", "describe-task-definition", "--task-definition", task_def,
         "--query", f"taskDefinition.containerDefinitions[?name=='{container}'].image | [0]",
         "--output", "text"],
        profile, region,
    )
    return split_image(image)[1] if image and image != "None" else ""


def build(env: str, profile: str, region: str, bucket: str, cluster: str) -> tuple[dict, dict]:
    """Return (values, sources) for every key the CI env file needs."""
    raw = subprocess.run(
        ["aws", "s3", "cp", f"s3://{bucket}/flip/terraform.tfstate", "-",
         "--profile", profile, "--region", region],
        capture_output=True, text=True,
    )
    if raw.returncode != 0:
        die(f"could not read s3://{bucket}/flip/terraform.tfstate — {raw.stderr.strip().splitlines()[-1:] or ''}")
    st = State(json.loads(raw.stdout))

    api = st.container("flip_api", "flip-api")
    fl_api = st.container("fl_api_net_1", "fl-api-net-1")
    fl_server = st.container("fl_server_net_1", "fl-server-net-1")
    api_env, fl_api_env, fl_server_env = env_of(api), env_of(fl_api), env_of(fl_server)
    registry = split_image(api.get("image", ""))[0]

    # The EFS provisioning task syncs the participant kit from a dated S3 prefix;
    # that date is the kit-date input, and the command line is the only place it
    # survives into deployed state.
    provision_cmd = " ".join(st.container("efs_provision", "provision-efs-certs").get("command") or [])

    def kit_date(flavour: str) -> str:
        m = re.search(rf"fl-{flavour}-participant-kits/(\d+)/", provision_cmd)
        return m.group(1) if m else ""

    def bucket_of(ssm_name: str) -> str:
        return st.attrs("aws_ssm_parameter", ssm_name).get("value", "").removeprefix("s3://").split("/")[0]

    secret = json.loads(
        aws(["secretsmanager", "get-secret-value", "--secret-id", "FLIP_API",
             "--query", "SecretString", "--output", "text"], profile, region) or "{}"
    )

    # Live, not state: the kit-slot list is grown by `make apply-fl-kit-slots`,
    # which is a targeted apply — state and the parameter can legitimately differ
    # between full applies.
    slots = aws(["ssm", "get-parameter", "--name", "/flip/fl_kit_slot_names",
                 "--query", "Parameter.Value", "--output", "text"], profile, region)

    trust_ips = st.instance_keys("aws_security_group_rule", "local_trust_fl_server_nlb")
    k8s_ips = st.instance_keys("aws_security_group_rule", "k8s_trust_fl_server_nlb")
    has_trust_ec2 = st.has_module_resource("module.trust_ec2", "aws_instance", "trust_host")

    v: dict[str, str] = {
        "AWS_REGION": region,
        "FLIP_TFSTATE_BUCKET_NAME": bucket,
        "VPC_NAME": st.attrs("aws_vpc", "this").get("tags", {}).get("Name", ""),
        "AICENTRE_BUCKET_NAME": st.attrs("aws_s3_bucket", "aicentre_bucket").get("bucket", ""),
        "FLIP_UI_BUCKET_NAME": st.attrs("aws_s3_bucket", "flip_ui").get("bucket", ""),
        # Read from the *data* source: the demo bucket is not Terraform-managed
        # (objects are staged by hand), so only the lookup records its name.
        "DEMO_ASSETS_BUCKET_NAME": st.data_attrs("aws_s3_bucket", "demo_assets").get("bucket", ""),
        "FLIP_APP_BUNDLES_BUCKET_NAME": bucket_of("flip_app_bundles_bucket"),
        "FLIP_FL_RESULTS_BUCKET_NAME": bucket_of("flip_fl_results_bucket"),
        "FLIP_MODEL_FILES_UPLOADS_BUCKET_NAME": bucket_of("flip_model_files_uploads_bucket"),
        "SES_VERIFIED_EMAIL": st.attrs("aws_ses_email_identity", "flip_sender").get("email", ""),
        "ALB_SUBDOMAIN": st.attrs("aws_route53_record", "alb").get("fqdn", ""),
        "NLB_SUBDOMAIN": st.attrs("aws_route53_record", "fl_server_nlb").get("fqdn", ""),
        "POSTGRES_DB": api_env.get("POSTGRES_DB", ""),
        "POSTGRES_USER": api_env.get("POSTGRES_USER", ""),
        "DB_PORT": api_env.get("DB_PORT", ""),
        # Not deployed anywhere — no resource in this root references UI_PORT.
        # It still has to be present and numeric: the Makefile exports it
        # unconditionally and Terraform rejects "" for a number variable.
        "UI_PORT": "443",
        "API_PORT": first_port(api),
        "FL_API_PORT": first_port(fl_api),
        "FL_SERVER_PORT": first_port(fl_server),
        "INTERNAL_SERVICE_KEY_HEADER": api_env.get("INTERNAL_SERVICE_KEY_HEADER", ""),
        "TRUST_API_KEY_HEADER": api_env.get("TRUST_API_KEY_HEADER", ""),
        "FL_BACKEND": api_env.get("FL_BACKEND", ""),
        "FL_ADMIN_DIRECTORY": fl_api_env.get("FL_ADMIN_DIRECTORY", ""),
        "MIN_CLIENTS": fl_server_env.get("MIN_CLIENTS", ""),
        "FLARE_KIT_DATE": kit_date("flare"),
        "FLOWER_KIT_DATE": kit_date("flower"),
        "DOCKER_REGISTRY": registry,
        "DOCKER_TAG": live_image_tag("flip-api", "flip-api", cluster, profile, region),
        "DOCKER_FL_TAG": live_image_tag("fl-server-net-1", "fl-server-net-1", cluster, profile, region),
        "FL_KIT_SLOT_NAMES": slots,
        # Empty is meaningful: locals.tf omits ENFORCE_MFA from the task env when
        # unset so flip-api's secure Pydantic default applies.
        "ENFORCE_MFA": api_env.get("ENFORCE_MFA", ""),
        "ADMIN_USER_PASSWORD": st.attrs("aws_cognito_user", "admin_user").get("password", ""),
        "AES_KEY_BASE64": secret.get("aes_key", ""),
        "INTERNAL_SERVICE_KEY": secret.get("internal_service_key", ""),
        "INTERNAL_SERVICE_KEY_HASH": secret.get("internal_service_key_hash", ""),
        "JOB_RESOURCE_SPEC_NUM_GPUS": fl_api_env.get("JOB_RESOURCE_SPEC_NUM_GPUS", ""),
        "JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB": fl_api_env.get("JOB_RESOURCE_SPEC_MEM_PER_GPU_IN_GIB", ""),
        "DEPLOY_TRUST_EC2": "true" if has_trust_ec2 else "false",
        "LOCAL_TRUST_PUBLIC_IPS": json.dumps(sorted(trust_ips)).replace('", "', '", "') if trust_ips else "[]",
        "K8S_TRUST_PUBLIC_IPS": json.dumps(sorted(k8s_ips)) if k8s_ips else "[]",
    }
    # Which values did NOT come from state. Built by grouping rather than as
    # literal `"KEY": "..."` pairs: secret-scanning treats a quoted string beside
    # a key named `*_KEY`/`*_PASSWORD` as a probable credential, and a file that
    # needs four `allowlist secret` pragmas to describe its own provenance is one
    # where a real leak would blend in.
    sources: dict[str, str] = {}
    for group, keys in (
        ("live ECS service", ("DOCKER_TAG", "DOCKER_FL_TAG")),
        ("live SSM parameter", ("FL_KIT_SLOT_NAMES",)),
        ("live Secrets Manager", ("AES_KEY_BASE64", "INTERNAL_SERVICE_KEY", "INTERNAL_SERVICE_KEY_HASH")),
    ):
        sources.update(dict.fromkeys(keys, group))
    return v, sources


def keys_expected_empty(env: str) -> set[str]:
    """Keys whose empty value is a real answer rather than a failed recovery.

    ``ENFORCE_MFA`` unset means "use flip-api's secure Pydantic default", and
    only one of the two FL kit dates is ever provisioned.

    ``DEMO_ASSETS_BUCKET_NAME`` is the asymmetric one, and getting it wrong is
    destructive rather than merely wrong. Empty is correct on **stag**, which
    hosts no public Ark+ demo. On **prod** it gates four live resources plus the
    ``/ark_demo/*`` CloudFront behaviour (``demo_assets_enabled =
    var.DEMO_ASSETS_BUCKET_NAME != ""``), so an empty recovered value there means
    the lookup failed, not that there is no demo — and seeding the GitHub
    environment from it would destroy the demo on the next apply. Treated as
    not-recovered on prod, and omitted from ``--out`` rather than written blank,
    so ``setup-github-environments.sh`` reports it as required-but-absent instead
    of filing it under "optional, which is fine".
    """
    expected = {"ENFORCE_MFA"}
    if env != "prod":
        expected.add("DEMO_ASSETS_BUCKET_NAME")
    return expected


def shown(key: str, value: str) -> str:
    if not value:
        return "(empty)"
    if key in SECRET_KEYS:
        return f"sha256:{hashlib.sha256(value.encode()).hexdigest()[:12]}"
    return value


def read_env_file(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\n"))
            if m:
                out[m.group(1)] = m.group(2)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", required=True, choices=["stag", "prod"])
    ap.add_argument("--profile", help="AWS profile (default: same as --env)")
    ap.add_argument("--region", default="eu-west-2")
    ap.add_argument("--bucket", help="state bucket (default: flip-terraform-state-<env>)")
    ap.add_argument("--cluster", default="flip-cluster")
    ap.add_argument("--out", help="write the recovered values here (mode 0600); refuses to overwrite")
    ap.add_argument(
        "--force",
        action="store_true",
        help="allow --out to overwrite an existing file (see the warning in --out's refusal)",
    )
    ap.add_argument("--compare", help="report differences against an existing env file")
    args = ap.parse_args()

    profile = args.profile or args.env
    bucket = args.bucket or f"flip-terraform-state-{args.env}"

    print(f"🔎 Reading deployed configuration for '{args.env}' (profile {profile})…\n")
    values, sources = build(args.env, profile, args.region, bucket, args.cluster)

    width = max(len(k) for k in values)
    for key in sorted(values):
        src = sources.get(key, "terraform state")
        print(f"  {key:<{width}}  {shown(key, values[key]):<28}  [{src}]")

    expected_empty = keys_expected_empty(args.env)
    expected_empty.add("FLOWER_KIT_DATE" if values.get("FL_BACKEND") == "nvflare" else "FLARE_KIT_DATE")
    empties = [k for k, val in values.items() if not val and k not in expected_empty]
    if empties:
        print("\n⚠️  Not recovered — set these by hand before use:")
        for k in empties:
            print(f"     - {k}")

    if args.compare:
        existing = read_env_file(args.compare)
        drift = [
            (k, existing.get(k), values[k])
            for k in sorted(values)
            if values[k] and existing.get(k, "") != values[k]
        ]
        print(f"\n📋 Compared against {args.compare}: {len(drift)} difference(s)")
        for k, was, now in drift:
            print(f"  {k}")
            print(f"      file    {shown(k, was or '')}")
            print(f"      deployed{'':<0} {shown(k, now)}")

    if args.out:
        # This writes ONLY the Terraform inputs. A real operator .env file also
        # carries the trust, XNAT, Orthanc and OMOP settings, which nothing here
        # can recover — so overwriting one silently destroys most of it. Refuse
        # by default and make the caller say otherwise.
        if os.path.exists(args.out) and not args.force:
            die(
                f"{args.out} already exists. This tool emits only the ~{len(values)} Terraform "
                "inputs, not a complete operator env file — overwriting would drop every other "
                "key in it (trust, XNAT, Orthanc, OMOP). Write somewhere else, use --compare to "
                "see the drift, or pass --force if you really mean to replace it."
            )
        with open(os.open(args.out, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(f"# Terraform inputs reconciled from the deployed {args.env} infrastructure by\n")
            fh.write("# deploy/providers/AWS/scripts/reconcile_ci_env.py. Review before use.\n")
            fh.write("# NOT a complete operator env file: the trust/XNAT/Orthanc/OMOP settings\n")
            fh.write("# are not recoverable from AWS and are absent here.\n")
            for key in sorted(values):
                if values[key] or key in keys_expected_empty(args.env):
                    fh.write(f"{key}={values[key]}\n")
        print(f"\n✅ Wrote {args.out} (0600).")


if __name__ == "__main__":
    main()
