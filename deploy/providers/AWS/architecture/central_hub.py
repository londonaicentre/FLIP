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

"""Render the Central Hub AWS architecture diagram from the Terraform in this directory.

The picture is *diagram-as-code*: nodes, clusters and edges are declared here with the
``diagrams`` library and rendered through graphviz. What keeps it honest is ``TERRAFORM_ADDRESSES``,
the map from every drawn label to the Terraform addresses it stands for. ``tests/test_architecture_diagram.py``
checks that map against the ``.tf`` files in both directions on every CI run, so a resource cannot be
removed, renamed or added without the diagram (or an explicit exemption) changing in the same PR.

Two consumers render it:

* the Sphinx build (``docs/source/conf.py``) — at build time, into a gitignored assets directory, so the
  ReadTheDocs page always shows the picture for the commit it documents;
* ``make aws-diagram`` at the repo root — into ``docs/`` here, committed so the README can embed them.

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render rather than
producing an empty file. Run it in docker instead::

    docker run --rm -v "$PWD:/work" -w /work/deploy/providers/AWS python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.central_hub --out docs"

Topology drawn: the current ``develop`` shape — CloudFront + WAF in front of an S3 SPA bucket and a VPC
origin onto the internal ALB; a public NLB passing FL mTLS traffic through to the FL server; ECS Fargate
in private subnets; RDS behind RDS Proxy. The Landing Zone Accelerator variant (``lza_managed_network``)
is not drawn until it merges.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.compute import EC2, Fargate
from diagrams.aws.database import RDS, RDSPostgresqlInstance
from diagrams.aws.engagement import SES
from diagrams.aws.general import Users
from diagrams.aws.management import CloudwatchLogs, ParameterStore
from diagrams.aws.network import ALB, NLB, CloudFront, CloudMap, Endpoint, NATGateway, Route53
from diagrams.aws.security import ACM, KMS, WAF, Cognito, SecretsManager
from diagrams.aws.storage import EFS, S3
from diagrams.onprem.compute import Server
from diagrams.onprem.vcs import Github

#: Drawn label -> Terraform addresses (``<type>.<name>`` or ``module.<name>``) in the root module that the
#: node stands for. Every node and cluster the diagram draws is a key here; the drift test checks each
#: address exists and that nothing load-bearing is left out. Labels without Terraform (users, the trust
#: node, GHCR) map to an empty tuple so they are still declared as drawn.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside AWS -------------------------------------------------------------------------------
    "Users": (),
    "trust-api": (),
    "fl-client-net-1": (),
    "GHCR": (),
    # ---- edge (global) -----------------------------------------------------------------------------
    "Route 53": ("aws_route53_record.alb", "aws_route53_record.fl_server_nlb"),
    "CloudFront": ("aws_cloudfront_distribution.flip_ui", "aws_cloudfront_function.spa_rewrite"),
    "WAFv2 WebACL": ("aws_wafv2_web_acl.flip_ui_cloudfront",),
    "ACM certificates": ("aws_acm_certificate.flip_cloudfront", "aws_acm_certificate.flip"),
    "flip-ui bucket": ("aws_s3_bucket.flip_ui", "aws_cloudfront_origin_access_control.flip_ui"),
    # ---- VPC ---------------------------------------------------------------------------------------
    "flip-vpc": ("module.flip_vpc", "aws_vpc_dhcp_options.flip"),
    "NAT Gateway": ("module.flip_vpc",),
    "FL NLB": ("module.fl_server_nlb", "aws_lb_target_group.ecs_fl_server_tcp"),
    "Internal ALB": (
        "module.alb",
        "aws_cloudfront_vpc_origin.flip_api",
        "aws_lb_target_group.ecs_flip_api",
        "aws_lb_listener_rule.api_routing",
    ),
    "ECS Fargate cluster": ("aws_ecs_cluster.flip",),
    "flip-api": ("aws_ecs_service.flip_api", "aws_ecs_task_definition.flip_api"),
    "fl-api-net-1": ("aws_ecs_service.fl_api_net_1", "aws_ecs_task_definition.fl_api_net_1"),
    "fl-server-net-1": ("aws_ecs_service.fl_server_net_1", "aws_ecs_task_definition.fl_server_net_1"),
    "RDS Proxy": ("aws_db_proxy.flip_db",),
    "RDS PostgreSQL": ("module.flip_db", "aws_db_subnet_group.flip_db_subnet_group"),
    "EFS": ("aws_efs_file_system.flip_fl",),
    "SSM bastion": ("aws_instance.ec2_instance",),
    "Cloud Map": ("aws_service_discovery_private_dns_namespace.flip_local",),
    "VPC endpoints": ("aws_vpc_endpoint.s3", "aws_vpc_endpoint.interface"),
    "AWS-hosted mock trust": ("module.trust_ec2",),
    # ---- regional services outside the VPC ----------------------------------------------------------
    "Cognito": ("module.cognito",),
    "SES": ("module.ses",),
    "Secrets Manager": ("module.flip_api_secret",),
    "Parameter Store": (
        "aws_ssm_parameter.flip_api_internal_url",
        "aws_ssm_parameter.flip_model_files_uploads_bucket",
        "aws_ssm_parameter.flip_fl_results_bucket",
        "aws_ssm_parameter.flip_app_bundles_bucket",
        "aws_ssm_parameter.vpc_id",
        "aws_ssm_parameter.private_subnet_ids",
        "aws_ssm_parameter.fl_kit_slot_names",
    ),
    "KMS": ("aws_kms_key.flip_app_key",),
    "Model files bucket": ("module.flip_model_files_uploads_bucket",),
    "FL results bucket": ("module.flip_fl_results_bucket",),
    "App bundles bucket": ("module.flip_app_bundles_bucket",),
    "Participant kits bucket": ("aws_s3_bucket.aicentre_bucket",),
    "Access logs buckets": ("aws_s3_bucket.flip_access_logs", "aws_s3_bucket.cloudfront_logs"),
    "CloudWatch Logs": (
        "aws_cloudwatch_log_group.ecs_flip_api",
        "aws_cloudwatch_log_group.ecs_fl_api_net_1",
        "aws_cloudwatch_log_group.ecs_fl_server_net_1",
        "aws_cloudwatch_log_group.flip_ui_waf",
        "aws_cloudwatch_log_group.flip_trust_log_group",
    ),
}

#: Resource types that are architecture: every root-module resource of one of these types must appear in
#: ``TERRAFORM_ADDRESSES``. Adding, say, ``aws_ecs_service.fl_server_net_2`` without drawing it fails CI.
DRAWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "aws_ecs_cluster",
        "aws_ecs_service",
        "aws_cloudfront_distribution",
        "aws_cloudfront_vpc_origin",
        "aws_wafv2_web_acl",
        "aws_lb_target_group",
        "aws_db_proxy",
        "aws_efs_file_system",
        "aws_instance",
        "aws_vpc_endpoint",
        "aws_service_discovery_private_dns_namespace",
        "aws_kms_key",
        "aws_s3_bucket",
        "aws_ssm_parameter",
        "aws_cloudwatch_log_group",
    }
)

#: Root modules deliberately not drawn as boxes. Security groups and IAM roles are attributes of the things
#: they protect, not components; a new module must be either drawn or added here with a reason.
UNDRAWN_MODULES: frozenset[str] = frozenset(
    {
        "ec2_security_group",  # SG of the SSM bastion
        "trust_security_group",  # SG of the AWS-hosted mock trust
        "rds_security_group",  # SG of RDS
        "alb_security_group",  # SG of the internal ALB
        "ec2_role",  # IAM role of the SSM bastion
        "trust_ec2_role",  # IAM role of the AWS-hosted mock trust
    }
)

GRAPH_ATTR = {"fontsize": "20", "pad": "0.4", "nodesep": "0.4", "ranksep": "0.7", "splines": "spline"}
DEFAULT_NAME = "central-hub-aws"

#: The two pictures ``render`` produces, as ``<name>-<suffix>.png``. One canvas holding every edge was legible but
#: 1:2 portrait; splitting request/network paths from data/platform services keeps each readable at page width.
DIAGRAMS: tuple[tuple[str, str, str], ...] = (
    ("network", "FLIP Central Hub on AWS — request and FL paths", "LR"),
    ("data", "FLIP Central Hub on AWS — data and platform services", "TB"),
)


class _Drawn:
    """Tracks which map keys the pictures use, so a stale key is caught as loudly as a missing one.

    A label may appear in more than one picture (``flip-api`` anchors both) but only once per picture.
    """

    def __init__(self) -> None:
        self.used: set[str] = set()
        self._in_picture: set[str] = set()

    def start_picture(self) -> None:
        """Reset the per-picture duplicate check."""
        self._in_picture = set()

    def node(self, label: str, cls: type[Node], caption: str | None = None, **kwargs: str) -> Node:
        """Create a node whose label is declared in ``TERRAFORM_ADDRESSES``.

        Args:
            label (str): The exact key in ``TERRAFORM_ADDRESSES``.
            cls (type[Node]): The ``diagrams`` node class to draw it with.
            caption (str | None): Text to print under the icon when it should say more than the key.
            **kwargs: Extra graphviz node attributes.

        Returns:
            Node: The created node.
        """
        self.mark(label)
        return cls(caption or label, **kwargs)

    def mark(self, label: str) -> str:
        """Record that ``label`` is drawn (used directly for clusters, which are not ``Node`` instances)."""
        if label not in TERRAFORM_ADDRESSES:
            raise KeyError(f"{label!r} is drawn but not declared in TERRAFORM_ADDRESSES")
        if label in self._in_picture:
            raise ValueError(f"{label!r} is drawn twice in the same picture")
        self._in_picture.add(label)
        self.used.add(label)
        return label

    def assert_complete(self) -> None:
        """Fail if a declared label was never drawn — the map would otherwise vouch for a box that is not there."""
        unused = sorted(set(TERRAFORM_ADDRESSES) - self.used)
        if unused:
            raise RuntimeError(f"TERRAFORM_ADDRESSES declares labels the diagram never draws: {unused}")


def _fargate_services(drawn: _Drawn) -> tuple[Node, Node, Node]:
    """Draw the ECS cluster with its three services; shared by both pictures."""
    with Cluster(drawn.mark("ECS Fargate cluster")):
        flip_api = drawn.node("flip-api", Fargate)
        fl_api = drawn.node("fl-api-net-1", Fargate)
        fl_server = drawn.node("fl-server-net-1", Fargate)
    return flip_api, fl_api, fl_server


def _draw_network(drawn: _Drawn) -> None:
    """Request and FL paths: who reaches what, through which edge component."""
    users = drawn.node("Users", Users)
    with Cluster("Trust node (on-prem or AWS-hosted)"):
        trust_api = drawn.node("trust-api", Server)
        fl_client = drawn.node("fl-client-net-1", Server)

    with Cluster("Edge (global)"):
        dns = drawn.node("Route 53", Route53)
        cloudfront = drawn.node("CloudFront", CloudFront)
        waf = drawn.node("WAFv2 WebACL", WAF)
        acm = drawn.node("ACM certificates", ACM)
        ui_bucket = drawn.node("flip-ui bucket", S3, caption="flip-ui bucket\n(SPA, OAC only)")
        access_logs = drawn.node("Access logs buckets", S3)

    with Cluster(drawn.mark("flip-vpc")):
        with Cluster("Public subnets"):
            nat = drawn.node("NAT Gateway", NATGateway)
            fl_nlb = drawn.node("FL NLB", NLB, caption="FL NLB\n(TCP pass-through)")
        with Cluster("Private subnets"):
            alb = drawn.node("Internal ALB", ALB, caption="Internal ALB\n(VPC origin)")
            flip_api, fl_api, fl_server = _fargate_services(drawn)
            cloud_map = drawn.node("Cloud Map", CloudMap, caption="Cloud Map\nflip.local")
            bastion = drawn.node("SSM bastion", EC2, caption="SSM bastion\n(no inbound ports)")
            mock_trust = drawn.node(
                "AWS-hosted mock trust", EC2, caption="AWS-hosted mock trust\n(optional)", style="dashed"
            )
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(container images)")

    # Browser and trust-api both enter through CloudFront; only /api/* reaches the VPC.
    users >> Edge(label="https") >> dns >> cloudfront
    cloudfront - Edge(style="dotted") - waf
    cloudfront - Edge(style="dotted") - acm
    cloudfront >> Edge(style="dotted") >> access_logs
    cloudfront >> Edge(label="/  (SPA)") >> ui_bucket
    cloudfront >> Edge(label="/api/*") >> alb >> Edge(label=":8000") >> flip_api
    trust_api >> Edge(label="poll tasks, heartbeat\nTRUST_API_KEY", style="dashed") >> cloudfront

    # FL path: mTLS gRPC passes through the NLB untouched to the FL server.
    fl_client >> Edge(label="mTLS gRPC :8002") >> fl_nlb >> fl_server
    mock_trust >> Edge(style="dashed") >> fl_nlb
    fl_server >> Edge(label="logs, metrics, status\nINTERNAL_SERVICE_KEY", style="dashed") >> flip_api
    flip_api >> Edge(label="submit job") >> fl_api >> Edge(label="admin API") >> fl_server
    cloud_map - Edge(style="dotted", label="service discovery") - fl_api
    bastion - Edge(style="dotted", label="SSM Session Manager") - nat
    nat >> Edge(label="image pulls") >> ghcr


def _draw_data(drawn: _Drawn) -> None:
    """Data and platform services: what the hub tasks read, write and authenticate against."""
    with Cluster(drawn.mark("flip-vpc")):
        with Cluster("Private subnets"):
            flip_api, fl_api, fl_server = _fargate_services(drawn)
            rds_proxy = drawn.node("RDS Proxy", RDS, caption="RDS Proxy\n(IAM auth, TLS)")
            rds = drawn.node("RDS PostgreSQL", RDSPostgresqlInstance)
            efs = drawn.node("EFS", EFS, caption="EFS\n(FL workspaces)")
            endpoints = drawn.node("VPC endpoints", Endpoint, caption="VPC endpoints\n(S3, Secrets, SSM, Logs)")

    with Cluster("S3"):
        kits = drawn.node("Participant kits bucket", S3, caption="Participant kits\nbucket")
        app_bundles = drawn.node("App bundles bucket", S3, caption="App bundles\nbucket")
        model_files = drawn.node("Model files bucket", S3, caption="Model files bucket\nuploaded/ → scanned/")
        fl_results = drawn.node("FL results bucket", S3, caption="FL results\nbucket")

    with Cluster("Regional services"):
        cognito = drawn.node("Cognito", Cognito, caption="Cognito\n(user pool, TOTP MFA)")
        ses = drawn.node("SES", SES)
        secrets = drawn.node("Secrets Manager", SecretsManager, caption="Secrets Manager\nFLIP_API")
        params = drawn.node("Parameter Store", ParameterStore, caption="Parameter Store\n/flip/*")
        kms = drawn.node("KMS", KMS, caption="KMS\nflip-app-key")
        logs = drawn.node("CloudWatch Logs", CloudwatchLogs)

    flip_api >> Edge(label="SQL") >> rds_proxy >> rds
    fl_api >> efs
    fl_server >> efs
    endpoints - Edge(style="dotted") - fl_server
    kits >> Edge(label="kits, certs", style="dotted") >> efs
    flip_api >> Edge(label="bundle") >> app_bundles
    fl_api >> Edge(style="dashed") >> app_bundles
    flip_api >> Edge(label="presigned POST / GET") >> model_files
    fl_server >> Edge(label="results") >> fl_results
    flip_api >> Edge(style="dashed") >> fl_results
    flip_api >> Edge(label="JWKS, MFA") >> cognito
    flip_api >> Edge(label="email") >> ses
    flip_api >> Edge(style="dotted") >> secrets
    flip_api >> Edge(style="dotted") >> params
    fl_server >> Edge(style="dotted") >> secrets
    fl_api >> Edge(style="dotted") >> logs
    kms - Edge(style="dotted") - model_files
    kms - Edge(style="dotted") - secrets


def render(out_dir: Path, name: str = DEFAULT_NAME) -> list[Path]:
    """Render the Central Hub diagrams as PNG.

    Args:
        out_dir (Path): Directory to write into; created if missing.
        name (str): File-stem prefix; each picture is ``<name>-<suffix>.png`` (see ``DIAGRAMS``).

    Returns:
        list[Path]: The rendered files, in ``DIAGRAMS`` order.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH`` — the module never writes an empty picture.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the Central Hub diagram cannot be rendered. Install graphviz "
            "(apt-get install graphviz) or run the docker one-liner in architecture/central_hub.py."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    drawn = _Drawn()
    painters = {"network": _draw_network, "data": _draw_data}
    outputs: list[Path] = []
    for suffix, title, direction in DIAGRAMS:
        target = out_dir / f"{name}-{suffix}"
        drawn.start_picture()
        with Diagram(
            title, filename=str(target), outformat="png", show=False, direction=direction, graph_attr=GRAPH_ATTR
        ):
            painters[suffix](drawn)
        outputs.append(target.with_suffix(".png"))
    drawn.assert_complete()
    return outputs


def main(argv: list[str] | None = None) -> int:
    """Command-line entrypoint.

    Args:
        argv (list[str] | None): Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(description="Render the FLIP Central Hub AWS architecture diagram.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    parser.add_argument("--name", default=DEFAULT_NAME, help=f"output file stem (default: {DEFAULT_NAME})")
    args = parser.parse_args(argv)
    for path in render(args.out, args.name):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
