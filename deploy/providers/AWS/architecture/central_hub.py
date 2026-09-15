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

"""Render the Central Hub AWS architecture diagrams from the Terraform in this directory.

The pictures are *diagram-as-code*: nodes, clusters and edges are declared here with the
``diagrams`` library and rendered through graphviz. What keeps them honest is ``TERRAFORM_ADDRESSES``,
the map from every drawn label to the Terraform addresses it stands for. ``tests/test_architecture_diagram.py``
checks that map against the ``.tf`` files in both directions on every CI run, so a resource cannot be
removed, renamed or added without the diagram (or an explicit exemption) changing in the same PR.

One Terraform root, two deployment modes, two picture sets (``Variant``):

* **legacy** — the self-contained account: FLIP's own VPC with NAT and IGW, an in-account CloudFront +
  WAF in front of an S3 SPA bucket and a VPC origin onto the internal ALB, a public NLB passing FL mTLS
  traffic through to the FL server;
* **lza** — ``var.lza_managed_network``: the accelerator-provisioned VPC of a Landing Zone Accelerator
  workload account (no IGW, no NAT), ingress from a shared networking account's edge over a Transit
  Gateway and Network Firewall onto one internal NLB with static per-subnet IPs, images from an ECR
  pull-through cache, interface endpoints centralised in the networking account.

The map is a single superset: a label gated to one mode is listed in ``VARIANT_ONLY_LABELS`` and may only
be drawn in that mode's pictures, every other label must appear in both. That keeps the guard's "every
load-bearing resource is drawn" invariant one rule while stopping an LZA picture from quietly showing the
public NLB, or a legacy one the Transit Gateway.

Two consumers render them:

* the Sphinx build (``docs/source/conf.py``) — at build time, into a gitignored assets directory, so the
  ReadTheDocs pages always show the pictures for the commit they document;
* ``make aws-diagram`` at the repo root — into ``docs/`` here, committed so the README can embed them.

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render rather than
producing an empty file. Run it in docker instead::

    docker run --rm -v "$PWD:/work" -w /work/deploy/providers/AWS python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.central_hub --out docs"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Iterable
from enum import StrEnum
from functools import partial
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.compute import EC2, ECR, Fargate
from diagrams.aws.database import RDS, RDSPostgresqlInstance
from diagrams.aws.engagement import SES
from diagrams.aws.general import Users
from diagrams.aws.management import CloudwatchLogs, ParameterStore
from diagrams.aws.network import (
    ALB,
    NLB,
    CloudFront,
    CloudMap,
    Endpoint,
    NATGateway,
    NetworkFirewall,
    Route53,
    Route53HostedZone,
    TransitGateway,
)
from diagrams.aws.security import ACM, KMS, WAF, Cognito, SecretsManager
from diagrams.aws.storage import EFS, S3
from diagrams.onprem.compute import Server
from diagrams.onprem.vcs import Github


class Variant(StrEnum):
    """The two deployment modes of the one Terraform root, each with its own picture set."""

    LEGACY = "legacy"
    LZA = "lza"


#: Drawn label -> Terraform addresses (``<type>.<name>``, ``data.<type>.<name>`` or ``module.<name>``) in the
#: root module that the node stands for. Every node and cluster either picture set draws is a key here; the
#: drift test checks each address exists and that nothing load-bearing is left out. Labels without Terraform
#: in this root (users, the trust node, GHCR, the networking account's edge) map to an empty tuple so they are
#: still declared as drawn.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside AWS -------------------------------------------------------------------------------
    "Users": (),
    "trust-api": (),
    "fl-client-net-1": (),
    "GHCR": (),
    # ---- edge (global), self-contained account -----------------------------------------------------
    "Route 53": ("aws_route53_record.alb", "aws_route53_record.fl_server_nlb"),
    "CloudFront": ("aws_cloudfront_distribution.flip_ui", "aws_cloudfront_function.spa_rewrite"),
    "WAFv2 WebACL": ("aws_wafv2_web_acl.flip_ui_cloudfront",),
    "ACM certificates": ("aws_acm_certificate.flip_cloudfront", "aws_acm_certificate.flip"),
    "flip-ui bucket": ("aws_s3_bucket.flip_ui", "aws_cloudfront_origin_access_control.flip_ui"),
    # ---- edge, LZA estate: built from this root's SSM handoff by the networking account's own stack --
    "Edge CloudFront + WAF": (),
    "Web relay NLB": (),
    "Edge NLB": (),
    "Central VPC endpoints": (),
    "Network Firewall": (),
    "Transit Gateway": ("data.aws_ec2_transit_gateway_vpc_attachment.lza", "data.aws_subnet.lza_tgw_attachment"),
    # ---- VPC, self-contained account ---------------------------------------------------------------
    "flip-vpc": ("module.flip_vpc", "aws_vpc_dhcp_options.flip"),
    "NAT Gateway": ("module.flip_vpc",),
    "FL NLB": ("module.fl_server_nlb", "aws_lb_target_group.ecs_fl_server_tcp"),
    "Internal ALB": (
        "module.alb",
        "aws_cloudfront_vpc_origin.flip_api",
        "aws_lb_target_group.ecs_flip_api",
        "aws_lb_listener_rule.api_routing",
    ),
    "VPC endpoints": ("aws_vpc_endpoint.s3", "aws_vpc_endpoint.interface"),
    # ---- VPC, LZA estate ---------------------------------------------------------------------------
    "Workload account VPC (accelerator-provisioned)": ("data.aws_vpc.lza",),
    "App subnets": ("data.aws_subnets.lza_app", "data.aws_subnet.lza_app"),
    "Data subnets": ("data.aws_subnets.lza_data",),
    "Internal NLB": (
        "module.fl_server_internal_nlb",
        "aws_lb_target_group.ecs_flip_api_lza",
        "aws_lb_target_group.ecs_fl_server_tcp_lza",
    ),
    "Private zone fl-server-net-1": (
        "aws_route53_zone.fl_server_bare_name",
        "aws_route53_record.fl_server_bare_name_apex",
    ),
    "SSM handoff": (
        "aws_ssm_parameter.lza_fl_nlb_private_ips",
        "aws_ssm_parameter.lza_fl_port",
        "aws_ssm_parameter.lza_web_nlb_dns_name",
        "aws_ssm_parameter.lza_web_port",
    ),
    "ECR pull-through cache": ("aws_iam_role_policy.ecs_task_execution_ecr_pull_through",),
    # ---- shared by both modes ----------------------------------------------------------------------
    "ECS Fargate cluster": ("aws_ecs_cluster.flip",),
    "flip-api": ("aws_ecs_service.flip_api", "aws_ecs_task_definition.flip_api"),
    "fl-api-net-1": ("aws_ecs_service.fl_api_net_1", "aws_ecs_task_definition.fl_api_net_1"),
    "fl-server-net-1": ("aws_ecs_service.fl_server_net_1", "aws_ecs_task_definition.fl_server_net_1"),
    "RDS Proxy": ("aws_db_proxy.flip_db",),
    "RDS PostgreSQL": ("module.flip_db", "aws_db_subnet_group.flip_db_subnet_group"),
    "EFS": ("aws_efs_file_system.flip_fl",),
    "SSM bastion": ("aws_instance.ec2_instance",),
    "Cloud Map": ("aws_service_discovery_private_dns_namespace.flip_local",),
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

#: Labels drawn in ONE mode's pictures only, because the component they stand for exists only there — the
#: Terraform behind them is ``count``/``create``-gated on ``var.lza_managed_network`` (or, for the networking
#: account's edge, lives in another repository altogether). Every other label is shared and must be drawn in
#: both picture sets. ``_Drawn`` rejects a legacy-only label in an LZA picture and vice versa, so the LZA
#: pictures cannot quietly show the public NLB, and the legacy ones cannot show the Transit Gateway.
VARIANT_ONLY_LABELS: dict[Variant, frozenset[str]] = {
    Variant.LEGACY: frozenset(
        {
            "Route 53",
            "CloudFront",
            "WAFv2 WebACL",
            "flip-vpc",
            "NAT Gateway",
            "FL NLB",
            "Internal ALB",
            "VPC endpoints",
        }
    ),
    Variant.LZA: frozenset(
        {
            "Edge CloudFront + WAF",
            "Web relay NLB",
            "Edge NLB",
            "Central VPC endpoints",
            "Network Firewall",
            "Transit Gateway",
            "Workload account VPC (accelerator-provisioned)",
            "App subnets",
            "Data subnets",
            "Internal NLB",
            "Private zone fl-server-net-1",
            "SSM handoff",
            "ECR pull-through cache",
        }
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
        "alb_security_group",  # SG of the internal ALB (self-contained mode)
        "fl_internal_nlb_security_group",  # SG of the internal NLB (LZA mode)
        "ec2_role",  # IAM role of the SSM bastion
        "trust_ec2_role",  # IAM role of the AWS-hosted mock trust
    }
)

GRAPH_ATTR = {"fontsize": "20", "pad": "0.4", "nodesep": "0.4", "ranksep": "0.7", "splines": "spline"}
DEFAULT_NAME = "central-hub-aws"

#: The pictures ``render`` produces, as ``<DEFAULT_NAME>-<suffix>.png``, per variant. One canvas holding every
#: edge was legible but 1:2 portrait; splitting request/network paths from data/platform services keeps each
#: readable at page width. The stems are fixed because the README image links and the Sphinx figure paths
#: embed them.
DIAGRAMS: tuple[tuple[Variant, str, str, str], ...] = (
    (Variant.LEGACY, "network", "FLIP Central Hub on AWS — request and FL paths", "LR"),
    (Variant.LEGACY, "data", "FLIP Central Hub on AWS — data and platform services", "TB"),
    (Variant.LZA, "lza-network", "FLIP Central Hub on an LZA estate — request and FL paths", "LR"),
    (Variant.LZA, "lza-data", "FLIP Central Hub on an LZA estate — data and platform services", "TB"),
)


class _Drawn:
    """Tracks which map keys each variant's pictures use, so a stale key is caught as loudly as a missing one.

    A label may appear in more than one picture (``flip-api`` anchors every picture) but only once per picture,
    and a label listed in ``VARIANT_ONLY_LABELS`` only in that variant's pictures.
    """

    def __init__(self) -> None:
        self.used: dict[Variant, set[str]] = {variant: set() for variant in Variant}
        self._variant: Variant | None = None
        self._in_picture: set[str] = set()

    def start_picture(self, variant: Variant) -> None:
        """Begin a picture of ``variant``: reset the per-picture duplicate check."""
        self._variant = variant
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
        if self._variant is None:
            raise RuntimeError("start_picture() must be called before drawing")
        if label not in TERRAFORM_ADDRESSES:
            raise KeyError(f"{label!r} is drawn but not declared in TERRAFORM_ADDRESSES")
        for other, only in VARIANT_ONLY_LABELS.items():
            if other is not self._variant and label in only:
                raise ValueError(f"{label!r} is {other.value}-only but is drawn in a {self._variant.value} picture")
        if label in self._in_picture:
            raise ValueError(f"{label!r} is drawn twice in the same picture")
        self._in_picture.add(label)
        self.used[self._variant].add(label)
        return label

    def assert_complete(self, variants: Iterable[Variant]) -> None:
        """Fail if a declared label was never drawn — the map would otherwise vouch for a box that is not there.

        Every rendered variant must draw each shared label and each of its own ``VARIANT_ONLY_LABELS``; the other
        variant's labels are exempt. Rendering both variants therefore demands that every key is drawn, which is
        the invariant the drift guard relies on.

        Args:
            variants (Iterable[Variant]): The variants whose pictures were rendered.
        """
        undeclared = sorted(
            label for only in VARIANT_ONLY_LABELS.values() for label in only if label not in TERRAFORM_ADDRESSES
        )
        if undeclared:
            raise RuntimeError(f"VARIANT_ONLY_LABELS names labels that are not in TERRAFORM_ADDRESSES: {undeclared}")
        for variant in variants:
            exempt = {label for other, only in VARIANT_ONLY_LABELS.items() if other is not variant for label in only}
            unused = sorted(set(TERRAFORM_ADDRESSES) - exempt - self.used[variant])
            if unused:
                raise RuntimeError(
                    f"TERRAFORM_ADDRESSES declares labels the {variant.value} pictures never draw: {unused}"
                )


def _fargate_services(drawn: _Drawn) -> tuple[Node, Node, Node]:
    """Draw the ECS cluster with its three services; shared by every picture."""
    with Cluster(drawn.mark("ECS Fargate cluster")):
        flip_api = drawn.node("flip-api", Fargate)
        fl_api = drawn.node("fl-api-net-1", Fargate)
        fl_server = drawn.node("fl-server-net-1", Fargate)
    return flip_api, fl_api, fl_server


def _hub_internal_edges(flip_api: Node, fl_api: Node, fl_server: Node, cloud_map: Node) -> None:
    """The hub-internal control edges, identical in both modes."""
    fl_server >> Edge(label="logs, metrics, status\nINTERNAL_SERVICE_KEY", style="dashed") >> flip_api
    flip_api >> Edge(label="submit job") >> fl_api >> Edge(label="admin API") >> fl_server
    cloud_map - Edge(style="dotted", label="service discovery") - fl_api


def _draw_network(drawn: _Drawn) -> None:
    """Self-contained account, request and FL paths: who reaches what, through which edge component."""
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
    _hub_internal_edges(flip_api, fl_api, fl_server, cloud_map)
    bastion - Edge(style="dotted", label="SSM Session Manager") - nat
    nat >> Edge(label="image pulls") >> ghcr


def _draw_network_lza(drawn: _Drawn) -> None:
    """LZA estate, request and FL paths: the networking account's edge, the Transit Gateway, one internal NLB."""
    users = drawn.node("Users", Users)
    with Cluster("Trust node (on-prem or AWS-hosted)"):
        trust_api = drawn.node("trust-api", Server)
        fl_client = drawn.node("fl-client-net-1", Server)

    with Cluster("Networking account (edge stack, separate repository)"):
        edge_cloudfront = drawn.node(
            "Edge CloudFront + WAF", CloudFront, caption="Edge CloudFront + WAF\n(UI via cross-account OAC)"
        )
        relay_nlb = drawn.node("Web relay NLB", NLB, caption="Web relay NLB\n(CloudFront VPC origin)")
        edge_nlb = drawn.node("Edge NLB", NLB, caption="Edge NLB (internet-facing)\nFL :8002 prod / :8003 stag")
        with Cluster("Transit Gateway"):
            firewall = drawn.node("Network Firewall", NetworkFirewall, caption="Network Firewall\n(central)")
            tgw = drawn.node("Transit Gateway", TransitGateway, caption="Transit Gateway\n(attachment, both AZs)")

    with Cluster("Workload account"):
        with Cluster(drawn.mark("Workload account VPC (accelerator-provisioned)") + "\n(no IGW, no NAT)"):
            with Cluster(drawn.mark("App subnets") + "\n(route to the TGW · both AZs)"):
                internal_nlb = drawn.node(
                    "Internal NLB", NLB, caption="Internal NLB\nstatic .251 per subnet\n:443 web · :8002 FL"
                )
                flip_api, fl_api, fl_server = _fargate_services(drawn)
                cloud_map = drawn.node("Cloud Map", CloudMap, caption="Cloud Map\nflip.local")
                drawn.node(
                    "SSM bastion", EC2, caption="SSM bastion\n(Session Manager over the\ncentral endpoints, no inbound)"
                )
                mock_trust = drawn.node(
                    "AWS-hosted mock trust", EC2, caption="AWS-hosted mock trust\n(optional)", style="dashed"
                )
            zone = drawn.node(
                "Private zone fl-server-net-1", Route53HostedZone, caption="Private zone\nfl-server-net-1 → NLB IPs"
            )
        ui_bucket = drawn.node("flip-ui bucket", S3, caption="flip-ui bucket\n(SPA, cross-account OAC)")
        handoff = drawn.node("SSM handoff", ParameterStore, caption="SSM handoff\n/flip/networking/*")
        ecr_cache = drawn.node(
            "ECR pull-through cache", ECR, caption="ECR pull-through cache\nghcr/ · ecr-public/\n(rules out of band)"
        )
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(upstream registry)")

    # Web: browser and trust-api enter through the edge CloudFront; /api/* rides the relay NLB over the TGW.
    users >> Edge(label="https") >> edge_cloudfront
    edge_cloudfront >> Edge(label="/  (SPA)") >> ui_bucket
    edge_cloudfront >> Edge(label="/api/*") >> relay_nlb
    trust_api >> Edge(label="poll tasks, heartbeat\nTRUST_API_KEY", style="dashed") >> edge_cloudfront

    # FL: mTLS gRPC passes through both NLBs untouched to the FL server.
    fl_client >> Edge(label="mTLS gRPC :8002") >> edge_nlb
    relay_nlb >> firewall
    edge_nlb >> firewall
    firewall >> tgw >> Edge(label=":443 web · :8002 FL") >> internal_nlb
    internal_nlb >> Edge(label=":443 → ecs-flip-api-lza") >> flip_api
    internal_nlb >> Edge(label=":8002 → ecs-fl-server-lza\n(mTLS pass-through)") >> fl_server
    mock_trust >> Edge(style="dashed") >> internal_nlb
    zone - Edge(style="dotted", label="A → NLB IPs") - internal_nlb
    _hub_internal_edges(flip_api, fl_api, fl_server, cloud_map)

    # The two-phase handoff: this root publishes the NLB's static IPs and ports, the edge stack registers them once.
    # The read-back edge points against the flow, so it is left out of the ranking to keep the layout left-to-right.
    internal_nlb >> Edge(style="dotted", label="publishes static IPs, ports") >> handoff
    handoff >> Edge(style="dashed", label="read by the edge stack", constraint="false") >> edge_nlb

    # No internet egress: images come from the in-account ECR mirror (over the central ecr endpoints).
    flip_api >> Edge(label="image pulls") >> ecr_cache >> Edge(style="dotted", label="upstream on cache miss") >> ghcr


def _draw_data(drawn: _Drawn, variant: Variant) -> None:
    """Data and platform services: what the hub tasks read, write and authenticate against.

    Identical in both modes except for where things sit: the self-contained account keeps RDS and its own
    interface endpoints in the private subnets, the LZA estate puts RDS in the isolated data subnets and reaches
    the interface endpoints centralised in the networking account.
    """
    if variant is Variant.LEGACY:
        with Cluster(drawn.mark("flip-vpc")):
            with Cluster("Private subnets"):
                flip_api, fl_api, fl_server = _fargate_services(drawn)
                rds_proxy = drawn.node("RDS Proxy", RDS, caption="RDS Proxy\n(IAM auth, TLS)")
                rds = drawn.node("RDS PostgreSQL", RDSPostgresqlInstance)
                efs = drawn.node("EFS", EFS, caption="EFS\n(FL workspaces)")
                endpoints = drawn.node("VPC endpoints", Endpoint, caption="VPC endpoints\n(S3, Secrets, SSM, Logs)")
    else:
        with Cluster(drawn.mark("Workload account VPC (accelerator-provisioned)")):
            with Cluster(drawn.mark("App subnets") + "\n(route to the TGW)"):
                flip_api, fl_api, fl_server = _fargate_services(drawn)
                rds_proxy = drawn.node("RDS Proxy", RDS, caption="RDS Proxy\n(IAM auth, TLS)")
                efs = drawn.node("EFS", EFS, caption="EFS\n(FL workspaces)")
            with Cluster(drawn.mark("Data subnets") + "\n(local routes only)"):
                rds = drawn.node("RDS PostgreSQL", RDSPostgresqlInstance)
        with Cluster("Networking account"):
            endpoints = drawn.node(
                "Central VPC endpoints",
                Endpoint,
                caption="Central interface endpoints\n(Secrets, Cognito, SES, SSM, Logs)",
            )

    with Cluster("S3"):
        kits = drawn.node("Participant kits bucket", S3, caption="Participant kits\nbucket")
        app_bundles = drawn.node("App bundles bucket", S3, caption="App bundles\nbucket")
        model_files = drawn.node("Model files bucket", S3, caption="Model files bucket\nuploaded/ → scanned/")
        fl_results = drawn.node("FL results bucket", S3, caption="FL results\nbucket")
        if variant is Variant.LZA:
            # The legacy network picture draws these two at its edge; the LZA network picture has no room for them.
            drawn.node("Access logs buckets", S3, caption="Access logs\nbucket")

    with Cluster("Regional services"):
        cognito = drawn.node("Cognito", Cognito, caption="Cognito\n(user pool, TOTP MFA)")
        ses = drawn.node("SES", SES)
        secrets = drawn.node("Secrets Manager", SecretsManager, caption="Secrets Manager\nFLIP_API")
        params = drawn.node("Parameter Store", ParameterStore, caption="Parameter Store\n/flip/*")
        kms = drawn.node("KMS", KMS, caption="KMS\nflip-app-key")
        logs = drawn.node("CloudWatch Logs", CloudwatchLogs)
        if variant is Variant.LZA:
            drawn.node("ACM certificates", ACM, caption="ACM certificate\n(NLB :443 once DNS moves)")

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


_PAINTERS: dict[str, Callable[[_Drawn], None]] = {
    "network": _draw_network,
    "data": partial(_draw_data, variant=Variant.LEGACY),
    "lza-network": _draw_network_lza,
    "lza-data": partial(_draw_data, variant=Variant.LZA),
}


def render(out_dir: Path, variants: Iterable[Variant] = tuple(Variant)) -> list[Path]:
    """Render the Central Hub diagrams as PNG.

    Each picture is ``<DEFAULT_NAME>-<suffix>.png`` (see ``DIAGRAMS``); the stems are fixed because the README
    image links and the Sphinx figure paths embed them.

    Args:
        out_dir (Path): Directory to write into; created if missing.
        variants (Iterable[Variant]): Which picture sets to render; both by default.

    Returns:
        list[Path]: The rendered files, in ``DIAGRAMS`` order.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH`` — the module never writes an empty picture — or if
            a rendered variant leaves a declared label undrawn.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the Central Hub diagrams cannot be rendered. Install graphviz "
            "(apt-get install graphviz) or run the docker one-liner in architecture/central_hub.py."
        )
    wanted = tuple(variants)
    out_dir.mkdir(parents=True, exist_ok=True)
    drawn = _Drawn()
    outputs: list[Path] = []
    for variant, suffix, title, direction in DIAGRAMS:
        if variant not in wanted:
            continue
        target = out_dir / f"{DEFAULT_NAME}-{suffix}"
        drawn.start_picture(variant)
        with Diagram(
            title, filename=str(target), outformat="png", show=False, direction=direction, graph_attr=GRAPH_ATTR
        ):
            _PAINTERS[suffix](drawn)
        outputs.append(target.with_suffix(".png"))
    drawn.assert_complete(wanted)
    return outputs


def main(argv: list[str] | None = None) -> int:
    """Command-line entrypoint.

    Args:
        argv (list[str] | None): Arguments, defaulting to ``sys.argv[1:]``.

    Returns:
        int: Process exit code.
    """
    parser = argparse.ArgumentParser(description="Render the FLIP Central Hub AWS architecture diagrams.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    parser.add_argument(
        "--variant",
        choices=["all", *(variant.value for variant in Variant)],
        default="all",
        help="which picture set to render (default: all)",
    )
    args = parser.parse_args(argv)
    variants = tuple(Variant) if args.variant == "all" else (Variant(args.variant),)
    for path in render(args.out, variants):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
