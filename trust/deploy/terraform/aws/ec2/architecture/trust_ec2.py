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

"""Render the AWS EC2 trust node architecture diagram from the Terraform in this directory.

Diagram-as-code in the style of ``deploy/providers/AWS/architecture/central_hub.py``: nodes, clusters and edges
are declared with the ``diagrams`` library and rendered through graphviz, and ``TERRAFORM_ADDRESSES`` maps every
drawn label to the Terraform addresses it stands for (``module.network.<address>`` for the network module's own
resources). ``tests/test_architecture_diagram.py`` checks that map against the ``.tf`` files in both directions, so a
resource cannot be removed, renamed or added without the diagram (or an explicit exemption) changing with it. The
Sphinx build (``docs/source/conf.py``) renders the pictures for the AWS EC2 node page.

The node reads nothing from a hub: it is one EC2 instance in the site's own account, with no public IP and no
inbound security-group rule. Every arrow to the hub, GHCR and Hugging Face starts on the node. Dashed nodes and
edges are optional: the network module's own VPC (only without ``subnet_id``), the SSM interface endpoints
(``ssm_endpoints``), the stop/start schedule (``schedule_enabled``) and the S3 kit read (``kit_bucket_name``).

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render. Run it in docker::

    docker run --rm -v "$PWD:/work" -w /work/trust/deploy/terraform/aws/ec2 python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.trust_ec2 --out docs"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.compute import EC2
from diagrams.aws.general import Users
from diagrams.aws.integration import EventbridgeScheduler
from diagrams.aws.management import CloudwatchLogs, SystemsManager
from diagrams.aws.network import IGW, Endpoint, NATGateway
from diagrams.aws.security import KMS, IAMRole
from diagrams.aws.storage import EBS, S3
from diagrams.onprem.compute import Server
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.logging import Loki
from diagrams.onprem.monitoring import Grafana
from diagrams.onprem.network import Internet
from diagrams.onprem.vcs import Github

#: Drawn label -> Terraform addresses in this root that the node stands for. Labels with no Terraform here (the
#: containers Ansible and Compose run on the host, the hub, GHCR, the operator) map to an empty tuple.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside the site's account ---------------------------------------------------------------
    "Operator": (),
    "Central Hub": (),
    "GHCR": (),
    "Hugging Face": (),
    # ---- network (../modules/network) --------------------------------------------------------------
    "VPC": ("module.network", "module.network.data.aws_vpc.existing", "module.network.aws_vpc.this"),
    "Public subnet": ("module.network.aws_subnet.public", "module.network.aws_route_table.public"),
    "Node subnet": (
        "module.network.data.aws_subnet.existing",
        "module.network.aws_subnet.private",
        "module.network.aws_route_table.private",
    ),
    "NAT gateway": ("module.network.aws_nat_gateway.this", "module.network.aws_eip.nat"),
    "Internet gateway": ("module.network.aws_internet_gateway.this",),
    "SSM endpoints": (
        "aws_vpc_endpoint.ssm",
        "aws_security_group.ssm_endpoints",
        "aws_vpc_security_group_ingress_rule.ssm_endpoints",
    ),
    # ---- the node -----------------------------------------------------------------------------------
    "EC2 trust node": (
        "aws_instance.node",
        "aws_security_group.node",
        "aws_vpc_security_group_egress_rule.node",
        "aws_key_pair.node",
        "data.aws_ssm_parameter.ami",
    ),
    "Host": (),
    "Root volume": ("aws_instance.node",),
    "trust-api": (),
    "imaging-api": (),
    "data-access-api": (),
    "fl-client-net-1": (),
    "omop-db": (),
    "orthanc": (),
    "xnat-web": (),
    "xnat-db": (),
    "loki": (),
    "alloy": (),
    "grafana": (),
    # ---- regional services --------------------------------------------------------------------------
    "IAM role": (
        "aws_iam_role.node",
        "aws_iam_instance_profile.node",
        "aws_iam_role_policy.ssm_session",
        "aws_iam_role_policy_attachment.cloudwatch_agent",
        "aws_iam_role_policy.kit_read",
    ),
    "Systems Manager": (),
    "CloudWatch Logs": (),
    "Kit bucket": (),
    "KMS": (),
    "Scheduler": (
        "aws_scheduler_schedule.node",
        "aws_iam_role.scheduler",
        "aws_iam_role_policy.scheduler",
    ),
}

#: Resource types that are architecture: every resource of one of these types, in this root or in
#: ``../modules/network``, must appear in ``TERRAFORM_ADDRESSES``.
DRAWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "aws_instance",
        "aws_security_group",
        "aws_iam_role",
        "aws_iam_role_policy",
        "aws_iam_role_policy_attachment",
        "aws_vpc_endpoint",
        "aws_scheduler_schedule",
        "aws_vpc",
        "aws_subnet",
        "aws_internet_gateway",
        "aws_eip",
        "aws_nat_gateway",
    }
)

#: Root modules deliberately not drawn as boxes (none: the network module is the VPC cluster).
UNDRAWN_MODULES: frozenset[str] = frozenset()

#: Font sizes and spacing copied from the Central Hub diagrams so the pictures read as one set.
GRAPH_ATTR = {"fontsize": "24", "pad": "0.4", "nodesep": "0.6", "ranksep": "0.7", "splines": "spline"}
NODE_ATTR = {"fontsize": "15"}
EDGE_ATTR = {"fontsize": "15"}
CLUSTER_ATTR = {"fontsize": "15", "margin": "20"}
OPTIONAL_CLUSTER_ATTR = {**CLUSTER_ATTR, "style": "dashed"}
DEFAULT_NAME = "trust-node-aws"

#: The pictures ``render`` produces, as ``<DEFAULT_NAME>-<suffix>.png``.
DIAGRAMS: tuple[tuple[str, str, str], ...] = (
    ("ec2-network", "FLIP trust node on AWS EC2 — network, access and AWS services", "TB"),
    ("ec2-host", "FLIP trust node on AWS EC2 — the services on the host", "TB"),
)


def _cluster(label: str, optional: bool = False) -> Cluster:
    """A ``Cluster`` with the shared label font size; dashed when it exists only in some configurations."""
    return Cluster(label, graph_attr=OPTIONAL_CLUSTER_ATTR if optional else CLUSTER_ATTR)


class _Drawn:
    """Tracks which map keys the picture uses, so a stale key is caught as loudly as a missing one."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self._in_picture: set[str] | None = None

    def start_picture(self) -> None:
        """Begin a picture: reset the per-picture duplicate check."""
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
        if self._in_picture is None:
            raise RuntimeError("start_picture() must be called before drawing")
        if label not in TERRAFORM_ADDRESSES:
            raise KeyError(f"{label!r} is drawn but not declared in TERRAFORM_ADDRESSES")
        if label in self._in_picture:
            raise ValueError(f"{label!r} is drawn twice in the same picture")
        self._in_picture.add(label)
        self.used.add(label)
        return label

    def assert_complete(self) -> None:
        """Fail if a declared label was never drawn."""
        unused = sorted(set(TERRAFORM_ADDRESSES) - self.used)
        if unused:
            raise RuntimeError(f"TERRAFORM_ADDRESSES declares labels the picture never draws: {unused}")


def _externals(drawn: _Drawn) -> tuple[Node, Node, Node]:
    """The three places the node dials out to; it reads nothing else from the hub."""
    hub = drawn.node("Central Hub", Server, caption="Central Hub\n(any FLIP hub)")
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(trust images)")
    hf = drawn.node("Hugging Face", Internet, caption="Hugging Face\naicentreflip/trust-data\n(seed data)")
    return hub, ghcr, hf


def _draw_network(drawn: _Drawn) -> None:
    """The node in its network (both modes), the AWS services it calls, the operator's only way in."""
    operator = drawn.node("Operator", Users, caption="Site operator\nTerraform · Ansible\ndocker over SSM")

    with _cluster("Site AWS account"):
        with _cluster(drawn.mark("VPC") + "\n(the site's, via subnet_id\nor created, 10.60.0.0/16)"):
            with _cluster(drawn.mark("Node subnet") + "\n(private · default route → NAT\nor the site's own routes)"):
                with _cluster(drawn.mark("EC2 trust node") + "\n(no public IP · IMDSv2\nSG: no inbound rules)"):
                    host = drawn.node(
                        "Host", EC2, caption="Host\nt3.xlarge Ubuntu\nDocker · SSM agent\nCloudWatch agent"
                    )
                    trust_api = drawn.node("trust-api", Server)
                    fl_client = drawn.node("fl-client-net-1", Server)
                    root = drawn.node("Root volume", EBS, caption="Root volume\ngp3, encrypted")
                endpoints = drawn.node(
                    "SSM endpoints",
                    Endpoint,
                    caption="SSM interface\nendpoints (×3)\n(optional:\nssm_endpoints)",
                    style="dashed",
                )
            with _cluster(drawn.mark("Public subnet") + "\n(created VPC only)", optional=True):
                nat = drawn.node("NAT gateway", NATGateway, caption="NAT gateway\n+ Elastic IP", style="dashed")
            igw = drawn.node("Internet gateway", IGW, caption="Internet gateway\n(created VPC only)", style="dashed")

        with _cluster("Regional services"):
            role = drawn.node(
                "IAM role",
                IAMRole,
                caption="Node IAM role\nSSM · CloudWatch agent\n+ kit read (optional)",
            )
            ssm = drawn.node("Systems Manager", SystemsManager, caption="SSM Session\nManager\n(the only way in)")
            logs = drawn.node("CloudWatch Logs", CloudwatchLogs, caption="CloudWatch Logs\n/aws/ec2/\nflip-trust")
            scheduler = drawn.node(
                "Scheduler",
                EventbridgeScheduler,
                caption="EventBridge\nScheduler: stop/start\n(optional:\nschedule_enabled)",
                style="dashed",
            )

    with _cluster("Kit owner's account\n(optional: kit_bucket_name)", optional=True):
        kit_bucket = drawn.node("Kit bucket", S3, caption="Kit bucket\nthis slot's FL kit · vocab/", style="dashed")
        kms = drawn.node("KMS", KMS, caption="KMS key\n(optional: kit_kms_key_arn)", style="dashed")

    hub, ghcr, hf = _externals(drawn)

    host - Edge(style="dotted") - root
    role - Edge(style="dotted", label="instance profile") - host
    operator >> Edge(label="aws ssm start-session") >> ssm

    # Outbound only: the node dials everything; nothing dials the node.
    trust_api >> Edge(label="HTTPS :443\nTRUST_API_KEY") >> nat
    fl_client >> Edge(label="mTLS gRPC\n:8002 prod / :8003 stag") >> nat
    host >> Edge(label="images, seed data :443") >> nat
    nat >> Edge(style="dashed") >> igw
    igw >> Edge(label="hub API :443\nFL :8002 / :8003") >> hub
    igw >> Edge(label=":443") >> ghcr
    igw >> Edge(label=":443") >> hf

    # AWS APIs, over the NAT / site route or, for SSM, the optional endpoints.
    host >> Edge(label="SSM agent :443\n(outbound)") >> ssm
    host >> Edge(style="dashed") >> endpoints >> Edge(style="dashed") >> ssm
    host >> Edge(label="syslog,\ndocker logs") >> logs
    host >> Edge(style="dashed", label="kit staging,\nvocab load") >> kit_bucket
    kms - Edge(style="dotted", label="decrypt via S3") - kit_bucket
    scheduler >> Edge(style="dashed", label="Start/StopInstances", constraint="false") >> host


def _draw_host(drawn: _Drawn) -> None:
    """The containers Ansible and the trust compose run on the node, and what each dials out to."""
    with _cluster(drawn.mark("EC2 trust node") + " (outbound only: via the NAT gateway or the site's routes)"):
        host = drawn.node("Host", EC2, caption="Host\nDocker engine")
        with _cluster("Trust stack (Docker Compose)"):
            trust_api = drawn.node("trust-api", Server)
            fl_client = drawn.node("fl-client-net-1", Server, caption="fl-client-net-1\n(NVFLARE or Flower)")
            imaging_api = drawn.node("imaging-api", Server)
            data_access = drawn.node("data-access-api", Server)
            omop = drawn.node("omop-db", PostgreSQL, caption="omop-db\n(OMOP CDM)")
            orthanc = drawn.node("orthanc", Server, caption="orthanc\n(mock PACS)")
        with _cluster("XNAT (swarm stack)"):
            xnat_web = drawn.node("xnat-web", Server, caption="xnat-web\n(+ nginx)")
            xnat_db = drawn.node("xnat-db", PostgreSQL)
        with _cluster("Observability"):
            alloy = drawn.node("alloy", Server)
            loki = drawn.node("loki", Loki)
            grafana = drawn.node("grafana", Grafana)

    hub, ghcr, hf = _externals(drawn)

    trust_api >> Edge(label="imaging tasks") >> imaging_api
    trust_api >> Edge(label="cohort tasks") >> data_access
    fl_client >> Edge(label="cohort data") >> data_access
    fl_client >> Edge(label="images") >> imaging_api
    data_access >> Edge(label="SQL") >> omop
    imaging_api >> Edge(label="REST") >> xnat_web
    xnat_web >> Edge(label="DQR: C-MOVE / C-STORE", dir="both") >> orthanc
    xnat_web >> xnat_db
    alloy >> Edge(label="container logs") >> loki >> grafana

    trust_api >> Edge(label="HTTPS :443 poll tasks, heartbeat\nTRUST_API_KEY") >> hub
    fl_client >> Edge(label="mTLS gRPC\n:8002 prod / :8003 stag") >> hub
    host >> Edge(label="image pulls :443") >> ghcr
    host >> Edge(label="seed omop-db + orthanc :443") >> hf


_PAINTERS: dict[str, Callable[[_Drawn], None]] = {"ec2-network": _draw_network, "ec2-host": _draw_host}


def render(out_dir: Path) -> list[Path]:
    """Render the trust node diagram.

    Args:
        out_dir (Path): Directory to write into; created if missing.

    Returns:
        list[Path]: The rendered files.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH``, or if the picture leaves a declared label undrawn.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the trust node diagram cannot be rendered. Install graphviz "
            "(apt-get install graphviz) or run the docker one-liner in architecture/trust_ec2.py."
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    drawn = _Drawn()
    outputs: list[Path] = []
    for suffix, title, direction in DIAGRAMS:
        target = out_dir / f"{DEFAULT_NAME}-{suffix}"
        drawn.start_picture()
        with Diagram(
            title,
            filename=str(target),
            outformat="png",
            show=False,
            direction=direction,
            graph_attr=GRAPH_ATTR,
            node_attr=NODE_ATTR,
            edge_attr=EDGE_ATTR,
        ):
            _PAINTERS[suffix](drawn)
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
    parser = argparse.ArgumentParser(description="Render the FLIP AWS EC2 trust node architecture diagram.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    args = parser.parse_args(argv)
    for path in render(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
