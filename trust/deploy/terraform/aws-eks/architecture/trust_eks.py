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

"""Render the Amazon EKS trust node architecture diagrams from the Terraform in this directory.

Diagram-as-code in the style of ``../aws-ec2/architecture/trust_ec2.py``: nodes, clusters and edges are declared with
the ``diagrams`` library and rendered through graphviz, and ``TERRAFORM_ADDRESSES`` maps every drawn label to the
Terraform addresses it stands for. The network comes from the EC2 root's ``modules/network``, called with ``count``,
so its resources are addressed ``module.network[0].<address>``. ``tests/test_architecture_diagram.py`` checks that
map against the ``.tf`` files in both directions, so a resource cannot be removed, renamed or added without the
diagrams (or an explicit exemption) changing with it. The Sphinx build (``docs/source/conf.py``) renders the pictures
for the Amazon EKS node page.

The cluster reads nothing from a hub and admits nothing inbound: every arrow to the hub, GHCR and Hugging Face
starts on a node. Dashed nodes and clusters exist only in the created-network mode (no ``subnet_ids``): the public
subnet, the NAT gateway and the internet gateway.

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render. Run it in docker::

    docker run --rm -v "$PWD:/work" -w /work/trust/deploy/terraform/aws-eks python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.trust_eks --out docs"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.aws.compute import EC2Instances, ElasticKubernetesService
from diagrams.aws.general import Users
from diagrams.aws.management import CloudwatchLogs
from diagrams.aws.network import IGW, Endpoint, NATGateway
from diagrams.aws.security import IAMRole
from diagrams.aws.storage import EBS, ElasticFileSystemEFS
from diagrams.k8s.compute import Job, Pod
from diagrams.k8s.network import NetworkPolicy
from diagrams.k8s.podconfig import Secret
from diagrams.k8s.storage import PVC
from diagrams.onprem.compute import Server
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.network import Internet
from diagrams.onprem.vcs import Github

#: Drawn label -> Terraform addresses it stands for. Labels with no Terraform here (the chart's objects, the hub,
#: GHCR, Hugging Face, the operator) map to an empty tuple.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside the site's account ---------------------------------------------------------------
    "Operator": (),
    "Central Hub": (),
    "GHCR": (),
    "Hugging Face": (),
    # ---- network (../aws-ec2/modules/network, created only without subnet_ids) -----------------------
    "VPC": (
        "module.network",
        "module.network[0].aws_vpc.this",
        "module.network[0].aws_default_security_group.this",
        "module.network[0].data.aws_subnet.existing",
        "module.network[0].data.aws_vpc.existing",
    ),
    "Public subnet": (
        "module.network[0].aws_subnet.public",
        "module.network[0].aws_route_table.public",
        "module.network[0].aws_route_table_association.public",
    ),
    "Node subnet": (
        "module.network[0].data.aws_availability_zones.available",
        "module.network[0].aws_subnet.private",
        "module.network[0].aws_route_table.private",
        "module.network[0].aws_route_table_association.private",
    ),
    "Control-plane subnet": (
        "data.aws_availability_zones.available",
        "aws_subnet.control_plane",
        "aws_route_table_association.control_plane",
    ),
    "NAT gateway": ("module.network[0].aws_nat_gateway.this", "module.network[0].aws_eip.nat"),
    "Internet gateway": ("module.network[0].aws_internet_gateway.this",),
    # ---- the cluster -------------------------------------------------------------------------------
    "EKS control plane": ("aws_eks_cluster.this",),
    "Public endpoint": ("aws_eks_cluster.this",),
    "Private endpoint": ("aws_eks_cluster.this",),
    "Managed node group": ("aws_eks_node_group.this",),
    "Nodes": ("aws_launch_template.node",),
    "EBS volumes": ("aws_eks_addon.ebs_csi",),
    "VPC CNI": ("aws_eks_addon.vpc_cni",),
    "Cluster add-ons": ("aws_eks_addon.core",),
    "EBS CSI driver": ("aws_eks_addon.ebs_csi",),
    "XNAT archive (EFS)": (
        "aws_efs_file_system.xnat",
        "aws_efs_mount_target.xnat",
        "aws_security_group.efs",
        "aws_vpc_security_group_ingress_rule.efs_from_nodes",
    ),
    "EFS CSI driver": ("aws_eks_addon.efs_csi",),
    # ---- regional services --------------------------------------------------------------------------
    "Cluster role": (
        "aws_iam_role.cluster",
        "aws_iam_role_policy_attachment.cluster",
        "data.aws_iam_policy_document.eks_assume",
    ),
    "Node role": (
        "aws_iam_role.node",
        "aws_iam_role_policy_attachment.node",
        "data.aws_iam_policy_document.ec2_assume",
    ),
    "EBS CSI role": (
        "aws_iam_role.ebs_csi",
        "aws_iam_role_policy_attachment.ebs_csi",
        "data.aws_iam_policy_document.pod_identity_assume",
    ),
    "EFS CSI role": ("aws_iam_role.efs_csi", "aws_iam_role_policy_attachment.efs_csi"),
    "CloudWatch Logs": ("aws_eks_cluster.this",),
    # ---- the flip-trust chart (namespace flip-trust) ------------------------------------------------
    "trust-api": (),
    "imaging-api": (),
    "data-access-api": (),
    "fl-client-net-1": (),
    "omop-db": (),
    "orthanc": (),
    "xnat-web": (),
    "xnat-db": (),
    "Observability": (),
    "Seed job": (),
    "FL kit Secret": (),
    "PVCs": (),
    "NetworkPolicies": (),
}

#: Resource types that are architecture: every resource of one of these types, in this root or in the network
#: module, must appear in ``TERRAFORM_ADDRESSES``. Every address of this root itself must appear regardless of type.
DRAWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "aws_eks_cluster",
        "aws_eks_node_group",
        "aws_eks_addon",
        "aws_launch_template",
        "aws_iam_role",
        "aws_iam_role_policy_attachment",
        "aws_vpc",
        "aws_subnet",
        "aws_internet_gateway",
        "aws_eip",
        "aws_nat_gateway",
        "aws_efs_file_system",
        "aws_efs_mount_target",
        "aws_security_group",
        "aws_vpc_security_group_ingress_rule",
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
    ("eks-network", "FLIP trust node on Amazon EKS — network, access and AWS services", "TB"),
    ("eks-workload", "FLIP trust node on Amazon EKS — the flip-trust chart on the cluster", "TB"),
)


def _cluster(label: str, optional: bool = False) -> Cluster:
    """A ``Cluster`` with the shared label font size; dashed when it exists only in some configurations."""
    return Cluster(label, graph_attr=OPTIONAL_CLUSTER_ATTR if optional else CLUSTER_ATTR)


class _Drawn:
    """Tracks which map keys the pictures use, so a stale key is caught as loudly as a missing one."""

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
            raise RuntimeError(f"TERRAFORM_ADDRESSES declares labels the pictures never draw: {unused}")


def _externals(drawn: _Drawn) -> tuple[Node, Node, Node]:
    """The three places the cluster dials out to; it reads nothing else from the hub."""
    hub = drawn.node("Central Hub", Server, caption="Central Hub\n(any FLIP hub)")
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(trust images)")
    hf = drawn.node("Hugging Face", Internet, caption="Hugging Face\naicentreflip/trust-data\n(seed data)")
    return hub, ghcr, hf


def _draw_network(drawn: _Drawn) -> None:
    """The cluster in its network, its IAM roles and logs, and the operator's only way in."""
    operator = drawn.node("Operator", Users, caption="Site operator\nterraform · kubectl · helm\nfrom one admitted IP")

    with _cluster("Site AWS account"):
        with _cluster(drawn.mark("VPC") + "\n(created, 10.60.0.0/16,\nor the site's, via subnet_ids)"):
            with _cluster(drawn.mark("Node subnet") + "\n(private · AZ a · default route → NAT\nor the site's routes)"):
                with _cluster(drawn.mark("Managed node group") + "\n(one AZ: the chart's EBS volumes are RWO)"):
                    nodes = drawn.node(
                        "Nodes",
                        EC2Instances,
                        caption="2 × m7i-flex.large\nAL2023 · no public IP\nIMDSv2, hop limit 1\nencrypted gp3 root",
                    )
                volumes = drawn.node("EBS volumes", EBS, caption="EBS gp3 volumes\n(the chart's PVCs)")
                efs = drawn.node(
                    "XNAT archive (EFS)",
                    ElasticFileSystemEFS,
                    caption="EFS\nXNAT archive\nNFS from nodes",
                )
            with _cluster(drawn.mark("Control-plane subnet") + "\n(private · AZ b · same route table)"):
                private_ep = drawn.node(
                    "Private endpoint",
                    Endpoint,
                    caption="Private API endpoint\n(EKS-managed ENIs\nin both subnets)",
                )
            with _cluster(drawn.mark("Public subnet") + "\n(created VPC only)", optional=True):
                nat = drawn.node("NAT gateway", NATGateway, caption="NAT gateway\n+ Elastic IP", style="dashed")
            igw = drawn.node("Internet gateway", IGW, caption="Internet gateway\n(created VPC only)", style="dashed")

        with _cluster("Amazon EKS (AWS-managed)"):
            control_plane = drawn.node(
                "EKS control plane",
                ElasticKubernetesService,
                caption="EKS control plane\nstandard support only\naccess entries (API mode)",
            )
            public_ep = drawn.node(
                "Public endpoint",
                Endpoint,
                caption="Public API endpoint\nadmits operator_cidrs\nonly",
            )

        with _cluster("Regional services"):
            cluster_role = drawn.node("Cluster role", IAMRole, caption="Cluster role\n(EKS service)")
            node_role = drawn.node("Node role", IAMRole, caption="Node role\nworker · CNI\nECR read")
            csi_role = drawn.node("EBS CSI role", IAMRole, caption="EBS CSI role\n(Pod Identity)")
            efs_role = drawn.node("EFS CSI role", IAMRole, caption="EFS CSI role\n(Pod Identity)")
            logs = drawn.node(
                "CloudWatch Logs",
                CloudwatchLogs,
                caption="CloudWatch Logs\ncontrol plane:\napi · audit\nauthenticator",
            )

    hub, ghcr, hf = _externals(drawn)

    operator >> Edge(label="kubectl, helm :443\n(aws eks get-token)") >> public_ep >> control_plane
    nodes >> Edge(label="kubelet → API :443") >> private_ep >> control_plane
    nodes - Edge(style="dotted", label="attached") - volumes
    cluster_role - Edge(style="dotted") - control_plane
    node_role - Edge(style="dotted", label="instance profile") - nodes
    csi_role >> Edge(style="dotted", label="creates, attaches") >> volumes
    nodes - Edge(style="dotted", label="NFS mount") - efs
    efs_role >> Edge(style="dotted", label="access points") >> efs
    control_plane >> Edge(label="control-plane logs") >> logs

    # Outbound only: the nodes dial everything; nothing dials them.
    nodes >> Edge(label="trust-api HTTPS :443\nFL mTLS gRPC :8002 / :8003\nimages, seed data :443") >> nat
    nat >> Edge(style="dashed") >> igw
    igw >> Edge(label="hub API :443\nFL :8002 / :8003") >> hub
    igw >> Edge(label=":443") >> ghcr
    igw >> Edge(label=":443") >> hf


def _draw_workload(drawn: _Drawn) -> None:
    """The flip-trust chart's pods in namespace flip-trust, its storage and policies, and what each dials out to."""
    operator = drawn.node("Operator", Users, caption="Site operator\nmake join\ndeploy · selftest")
    control_plane = drawn.node("EKS control plane", ElasticKubernetesService, caption="EKS API server")

    with _cluster(drawn.mark("Managed node group") + ": 2 nodes, one AZ (outbound only)"):
        nodes = drawn.node("Nodes", EC2Instances, caption="Nodes\nkubelet, containerd")
        with _cluster("kube-system (EKS add-ons)"):
            cni = drawn.node("VPC CNI", Pod, caption="VPC CNI\n(NetworkPolicy\nenforcement on)")
            drawn.node("Cluster add-ons", Pod, caption="kube-proxy · CoreDNS\nPod Identity agent")
            csi = drawn.node("EBS CSI driver", Pod, caption="EBS CSI driver\n(default class: gp3)")
            efs_csi = drawn.node("EFS CSI driver", Pod, caption="EFS CSI driver\n(class xnat-efs)")
        with _cluster("Namespace flip-trust (flip-trust chart · values-eks + values-small-node)"):
            trust_api = drawn.node("trust-api", Pod)
            fl_client = drawn.node("fl-client-net-1", Pod, caption="fl-client-net-1\n(NVFLARE or Flower)")
            imaging_api = drawn.node("imaging-api", Pod)
            data_access = drawn.node("data-access-api", Pod)
            omop = drawn.node("omop-db", PostgreSQL, caption="omop-db\n(OMOP CDM)")
            orthanc = drawn.node("orthanc", Pod, caption="orthanc\n(mock PACS)")
            xnat_web = drawn.node("xnat-web", Pod, caption="xnat-web\n(+ nginx)")
            xnat_db = drawn.node("xnat-db", PostgreSQL)
            drawn.node("Observability", Pod, caption="observability\nalloy · loki\ngrafana")
            seed = drawn.node("Seed job", Job, caption="trust-seed Job\n(install, upgrade hook)")
            kit = drawn.node("FL kit Secret", Secret, caption="FL kit Secret\n(this slot's\nkit only)")
            pvcs = drawn.node("PVCs", PVC, caption="PVCs (RWO)\nomop-db · orthanc\nDBs · images")
            policies = drawn.node("NetworkPolicies", NetworkPolicy, caption="NetworkPolicies\n(default deny\n+ allows)")

    volumes = drawn.node("EBS volumes", EBS, caption="EBS gp3 volumes\n(AZ a)")
    csi_role = drawn.node("EBS CSI role", IAMRole, caption="EBS CSI role")
    efs = drawn.node(
        "XNAT archive (EFS)",
        ElasticFileSystemEFS,
        caption="EFS: XNAT archive\n(RWX: Container Service\npods on any node)",
    )
    efs_role = drawn.node("EFS CSI role", IAMRole, caption="EFS CSI role")
    hub, ghcr, hf = _externals(drawn)

    operator >> Edge(label="helm, kubectl") >> control_plane
    kit >> Edge(style="dotted", label="mounted") >> fl_client
    trust_api >> Edge(label="imaging tasks") >> imaging_api
    trust_api >> Edge(label="cohort tasks") >> data_access
    fl_client >> Edge(label="cohort data") >> data_access
    fl_client >> Edge(label="images") >> imaging_api
    data_access >> Edge(label="SQL") >> omop
    imaging_api >> Edge(label="REST") >> xnat_web
    xnat_web >> Edge(label="DQR: C-MOVE / C-STORE", dir="both") >> orthanc
    xnat_web >> xnat_db
    cni >> Edge(style="dotted", label="enforces") >> policies
    csi_role >> Edge(style="dotted", label="Pod Identity") >> csi
    csi >> Edge(label="provisions") >> volumes
    pvcs - Edge(style="dotted", label="bound") - volumes
    efs_role >> Edge(style="dotted", label="Pod Identity") >> efs_csi
    efs_csi >> Edge(label="access point") >> efs
    xnat_web - Edge(style="dotted", label="archive, build") - efs
    seed >> Edge(label="load") >> omop
    seed >> Edge(label="load") >> orthanc

    trust_api >> Edge(label="HTTPS :443 poll tasks, heartbeat\nTRUST_API_KEY") >> hub
    fl_client >> Edge(label="mTLS gRPC\n:8002 prod / :8003 stag") >> hub
    nodes >> Edge(label="image pulls :443") >> ghcr
    seed >> Edge(label="seed data :443") >> hf


_PAINTERS: dict[str, Callable[[_Drawn], None]] = {"eks-network": _draw_network, "eks-workload": _draw_workload}


def render(out_dir: Path) -> list[Path]:
    """Render the EKS trust node diagrams.

    Args:
        out_dir (Path): Directory to write into; created if missing.

    Returns:
        list[Path]: The rendered files.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH``, or if the pictures leave a declared label undrawn.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the EKS trust node diagrams cannot be rendered. Install graphviz "
            "(apt-get install graphviz) or run the docker one-liner in architecture/trust_eks.py."
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
    parser = argparse.ArgumentParser(description="Render the FLIP Amazon EKS trust node architecture diagrams.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    args = parser.parse_args(argv)
    for path in render(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
