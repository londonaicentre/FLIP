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

"""Render the Azure trust node architecture diagrams from the Terraform in this directory.

Diagram-as-code in the style of ``deploy/providers/AWS/architecture/central_hub.py``. This tree has three roots
(``bootstrap/``, ``vm/``, ``aks/``) and two modules (``modules/network``, ``modules/kit_drop``), so
``TERRAFORM_ADDRESSES`` names each address as ``<directory>/<address>``. ``tests/test_architecture_diagram.py`` checks
that map against the ``.tf`` files in both directions, so a resource cannot be removed, renamed or added without the
diagrams (or an explicit exemption) changing with it. The Sphinx build (``docs/source/conf.py``) renders the pictures
for the Azure node page. Two pictures, never deployed together: the VM node and the one-node AKS cluster for the
``flip-trust`` Helm chart. Both read nothing from a hub and admit nothing inbound; every arrow to the hub, GHCR and
Hugging Face starts on the node. Dashed nodes and clusters exist only in some configurations: the network module's
own VNet + NAT gateway (only without ``existing_subnet_id``), the budget (``create_budget``), the daily shutdown
(``auto_shutdown_enabled``) and the kit drop (``kit_drop_enabled``, on by default).

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render. Run it in docker::

    docker run --rm -v "$PWD:/work" -w /work/trust/deploy/terraform/azure python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.trust_azure --out docs"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.azure.compute import VM, Disks, KubernetesServices
from diagrams.azure.general import CostBudgets, DevConsole, Scheduler
from diagrams.azure.identity import ManagedIdentities, Users
from diagrams.azure.network import NetworkInterfaces, NetworkSecurityGroupsClassic, PublicIpAddresses
from diagrams.azure.networking import Nat
from diagrams.azure.storage import BlobStorage, StorageAccounts
from diagrams.k8s.compute import Pod
from diagrams.onprem.compute import Server
from diagrams.onprem.network import Internet
from diagrams.onprem.vcs import Github

#: Drawn label -> ``<directory>/<address>`` it stands for. Labels with no Terraform (the services on the node, the
#: hub, GHCR, Hugging Face, the operator) map to an empty tuple.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside the subscription -------------------------------------------------------------------
    "Operator": (),
    "Central Hub": (),
    "GHCR": (),
    "Hugging Face": (),
    "GitHub": (),
    # ---- bootstrap (created once) --------------------------------------------------------------------
    "Bootstrap": ("bootstrap/azurerm_resource_group.bootstrap",),
    "State storage": (
        "bootstrap/azurerm_storage_account.state",
        "bootstrap/azurerm_storage_container.state",
        "bootstrap/azurerm_role_assignment.state_blob",
    ),
    "Budget alert": ("bootstrap/azurerm_consumption_budget_subscription.trial",),
    # ---- network (modules/network) --------------------------------------------------------------------
    "VNet": ("vm/module.network", "aks/module.network", "modules/network/azurerm_virtual_network.this"),
    "Node subnet": (
        "modules/network/azurerm_subnet.this",
        "modules/network/azurerm_network_security_group.this",
        "modules/network/azurerm_subnet_network_security_group_association.this",
    ),
    "NAT gateway": (
        "modules/network/azurerm_nat_gateway.this",
        "modules/network/azurerm_subnet_nat_gateway_association.this",
    ),
    "NAT public IP": (
        "modules/network/azurerm_public_ip.nat",
        "modules/network/azurerm_nat_gateway_public_ip_association.this",
    ),
    # ---- VM node (vm/) --------------------------------------------------------------------------------
    "Node resource group": ("vm/azurerm_resource_group.node",),
    "NIC": ("vm/azurerm_network_interface.node",),
    "NIC NSG": ("vm/azurerm_network_security_group.nic", "vm/azurerm_network_interface_security_group_association.nic"),
    "VM": ("vm/azurerm_linux_virtual_machine.node",),
    "Data disk": ("vm/azurerm_managed_disk.data", "vm/azurerm_virtual_machine_data_disk_attachment.data"),
    "Managed identity": ("vm/azurerm_linux_virtual_machine.node",),
    "Auto-shutdown": ("vm/azurerm_dev_test_global_vm_shutdown_schedule.node",),
    "Run Command": (),
    # ---- kit drop (modules/kit_drop) ------------------------------------------------------------------
    "Kit drop": (
        "vm/module.kit_drop",
        "modules/kit_drop/azurerm_storage_account.this",
        "modules/kit_drop/azurerm_storage_management_policy.this",
    ),
    "kits container": (
        "modules/kit_drop/azurerm_storage_container.kits",
        "modules/kit_drop/azurerm_role_assignment.reader",
        "modules/kit_drop/azurerm_role_assignment.uploader",
    ),
    # ---- AKS (aks/) -----------------------------------------------------------------------------------
    "AKS resource group": ("aks/azurerm_resource_group.aks",),
    "AKS cluster": ("aks/azurerm_kubernetes_cluster.this", "aks/azurerm_role_assignment.operator_cluster_admin"),
    "API server": ("aks/azurerm_kubernetes_cluster.this",),
    "Cluster identity": ("aks/azurerm_role_assignment.cluster_uses_subnet",),
    # ---- the trust services on the node ---------------------------------------------------------------
    "trust-api": (),
    "fl-client-net-1": (),
    "Other trust services": (),
}

#: Resource types that are architecture: every resource of one of these types, in any root or module of this tree,
#: must appear in ``TERRAFORM_ADDRESSES``.
DRAWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "azurerm_resource_group",
        "azurerm_storage_account",
        "azurerm_storage_container",
        "azurerm_role_assignment",
        "azurerm_consumption_budget_subscription",
        "azurerm_virtual_network",
        "azurerm_subnet",
        "azurerm_network_security_group",
        "azurerm_public_ip",
        "azurerm_nat_gateway",
        "azurerm_network_interface",
        "azurerm_linux_virtual_machine",
        "azurerm_managed_disk",
        "azurerm_dev_test_global_vm_shutdown_schedule",
        "azurerm_kubernetes_cluster",
    }
)

#: ``module`` blocks deliberately not drawn, as ``<root>/<name>`` (none: each module is a drawn cluster).
UNDRAWN_MODULES: frozenset[str] = frozenset()

#: Font sizes and spacing copied from the Central Hub diagrams so the pictures read as one set.
GRAPH_ATTR = {"fontsize": "24", "pad": "0.4", "nodesep": "0.6", "ranksep": "0.7", "splines": "spline"}
NODE_ATTR = {"fontsize": "15"}
EDGE_ATTR = {"fontsize": "15"}
CLUSTER_ATTR = {"fontsize": "15", "margin": "20"}
OPTIONAL_CLUSTER_ATTR = {**CLUSTER_ATTR, "style": "dashed"}
DEFAULT_NAME = "trust-node-azure"

#: The pictures ``render`` produces, as ``<DEFAULT_NAME>-<suffix>.png``.
DIAGRAMS: tuple[tuple[str, str, str], ...] = (
    ("vm", "FLIP trust node on an Azure VM (outbound only)", "TB"),
    ("aks", "FLIP trust node on AKS (flip-trust Helm chart, outbound only)", "TB"),
)


def _cluster(label: str, optional: bool = False) -> Cluster:
    """A ``Cluster`` with the shared label font size; dashed when it exists only in some configurations."""
    return Cluster(label, graph_attr=OPTIONAL_CLUSTER_ATTR if optional else CLUSTER_ATTR)


class _Drawn:
    """Tracks which map keys the pictures use, so a stale key is caught as loudly as a missing one.

    A label may appear in more than one picture (the bootstrap anchors both) but only once per picture.
    """

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


def _bootstrap(drawn: _Drawn, with_budget: bool) -> Node:
    """The once-created bootstrap layer: Terraform state (Entra ID only) and, on the VM picture, the budget."""
    with _cluster(drawn.mark("Bootstrap") + " (created once, kept across rebuilds)"):
        state = drawn.node("State storage", StorageAccounts, caption="State storage account\ntfstate · Entra ID only")
        if with_budget:
            drawn.node(
                "Budget alert",
                CostBudgets,
                caption="Budget alert\n20 / 50 / 80 %\n(optional: create_budget)",
                style="dashed",
            )
    return state


def _network(drawn: _Drawn) -> tuple[Node, Node]:
    """The NAT gateway pair the network module creates only without a site subnet; returns (nat, public IP)."""
    with _cluster("Own outbound (no existing_subnet_id)", optional=True):
        nat = drawn.node("NAT gateway", Nat, caption="NAT gateway", style="dashed")
        pip = drawn.node("NAT public IP", PublicIpAddresses, caption="Public IP\n(the node's egress)", style="dashed")
    return nat, pip


def _externals(drawn: _Drawn) -> tuple[Node, Node, Node]:
    """The places every Azure node dials out to."""
    hub = drawn.node("Central Hub", Server, caption="Central Hub\n(any FLIP hub)")
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(trust images)")
    hf = drawn.node("Hugging Face", Internet, caption="Hugging Face\naicentreflip/trust-data\n(seed data)")
    return hub, ghcr, hf


def _draw_vm(drawn: _Drawn) -> None:
    """The VM node: NIC behind a deny-all NSG, cloud-init, the kit drop, Run Command as the only way in."""
    operator = drawn.node("Operator", Users, caption="Operator\n(az CLI, Entra ID)")
    with _cluster("Azure subscription"):
        state = _bootstrap(drawn, with_budget=True)
        with _cluster(drawn.mark("Node resource group") + " (created and destroyed per session)"):
            with _cluster(
                drawn.mark("VNet")
                + " (the site's, via existing_subnet_id · or created)\n"
                + drawn.mark("Node subnet")
                + " · Microsoft.Storage service endpoint"
            ):
                nsg = drawn.node("NIC NSG", NetworkSecurityGroupsClassic, caption="NIC NSG\nDenyAllInbound (4096)")
                nic = drawn.node("NIC", NetworkInterfaces, caption="NIC\nprivate IP only")
                with _cluster("Ubuntu 24.04 VM, Trusted Launch (Standard_D4s_v5, no public IP)"):
                    vm = drawn.node("VM", VM, caption="VM\ncloud-init →\nflip-node, which\nruns azure.yml")
                    identity = drawn.node(
                        "Managed identity", ManagedIdentities, caption="System-assigned\nmanaged identity"
                    )
                    disk = drawn.node("Data disk", Disks, caption="Data disk\n/opt/flip (128 GB)")
                    trust_api = drawn.node("trust-api", Server)
                    fl_client = drawn.node("fl-client-net-1", Server)
                    drawn.node(
                        "Other trust services",
                        Server,
                        caption="imaging-api\ndata-access-api\nomop-db · orthanc\nXNAT (swarm)\nloki · alloy · grafana",
                    )
            nat, pip = _network(drawn)
            shutdown = drawn.node(
                "Auto-shutdown",
                Scheduler,
                caption="Daily shutdown 19:00\n(optional:\nauto_shutdown_enabled)",
                style="dashed",
            )
            with _cluster(
                drawn.mark("Kit drop") + " (kit_drop_enabled)\nshared keys off · firewall: node subnet + operator IP"
            ):
                kits = drawn.node("kits container", BlobStorage, caption="kits (private)\nblobs deleted after 1 day")
        run_command = drawn.node("Run Command", DevConsole, caption="Azure Run Command\n(the only way in)")

    hub, ghcr, hf = _externals(drawn)
    github = drawn.node("GitHub", Github, caption="GitHub\n(FLIP repo, cloned\nat first boot)")

    operator >> Edge(label="terraform (Entra ID)", style="dotted") >> state
    operator >> Edge(label="az vm run-command") >> run_command >> Edge(label="VM agent") >> vm
    operator >> Edge(label="kit-upload (Entra ID)") >> kits
    nsg - Edge(style="dotted") - nic
    nic - Edge(style="dotted") - vm
    vm - Edge(style="dotted") - disk
    identity >> Edge(label="Blob Data Reader\n(IMDS token)") >> kits
    shutdown >> Edge(style="dashed", constraint="false") >> vm

    # Outbound only, through the NAT gateway (or the site's routes on a site subnet).
    trust_api >> Edge(label="HTTPS :443\nTRUST_API_KEY") >> nat
    fl_client >> Edge(label="mTLS gRPC\n:8002 / :8003") >> nat
    vm >> Edge(label="git, images,\nseed data :443") >> nat
    nat - Edge(style="dashed") - pip
    pip >> Edge(label="hub API :443\nFL :8002 / :8003") >> hub
    pip >> Edge(label=":443") >> ghcr
    pip >> Edge(label=":443") >> hf
    pip >> Edge(label=":443") >> github


def _draw_aks(drawn: _Drawn) -> None:
    """The one-node AKS cluster: Entra-only API server, Cilium, the chart's pods dialling out."""
    operator = drawn.node("Operator", Users, caption="Operator\n(kubectl via kubelogin,\nmake aks-join / aks-deploy)")
    with _cluster("Azure subscription"):
        state = _bootstrap(drawn, with_budget=False)
        with _cluster(drawn.mark("AKS resource group") + " (never alongside the VM node)"):
            with _cluster(
                drawn.mark("VNet") + " (the site's + its route table · or created)\n" + drawn.mark("Node subnet")
            ):
                with _cluster(
                    drawn.mark("AKS cluster")
                    + " (Free tier · Entra ID + Azure RBAC, no local accounts · Cilium NetworkPolicies)"
                ):
                    api = drawn.node(
                        "API server",
                        KubernetesServices,
                        caption="API server\nadmits operator IP\n+ node egress IPs",
                    )
                    with _cluster("System node pool: 1 × Standard_D4s_v5 · namespace flip-trust"):
                        trust_api = drawn.node("trust-api", Pod)
                        fl_client = drawn.node("fl-client-net-1", Pod)
                        drawn.node(
                            "Other trust services",
                            Pod,
                            caption="imaging-api\ndata-access-api\nomop-db · orthanc\nXNAT\nloki · alloy · grafana",
                        )
                    identity = drawn.node(
                        "Cluster identity",
                        ManagedIdentities,
                        caption="Cluster identity\nNetwork Contributor\non the subnet",
                    )
            nat, pip = _network(drawn)

    hub, ghcr, hf = _externals(drawn)

    operator >> Edge(label="terraform (Entra ID)", style="dotted") >> state
    operator >> Edge(label="kubectl, helm (Entra ID)\nfrom the admitted IP") >> api
    identity - Edge(style="dotted") - api

    trust_api >> Edge(label="HTTPS :443\nTRUST_API_KEY") >> nat
    fl_client >> Edge(label="mTLS gRPC\n:8002 / :8003") >> nat
    nat - Edge(style="dashed", label="userAssignedNATGateway\n(or userDefinedRouting)") - pip
    pip >> Edge(label="hub API :443\nFL :8002 / :8003") >> hub
    pip >> Edge(label="image pulls :443") >> ghcr
    pip >> Edge(label="seed job :443") >> hf


_PAINTERS: dict[str, Callable[[_Drawn], None]] = {"vm": _draw_vm, "aks": _draw_aks}


def render(out_dir: Path) -> list[Path]:
    """Render the Azure trust node diagrams.

    Args:
        out_dir (Path): Directory to write into; created if missing.

    Returns:
        list[Path]: The rendered files.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH``, or if the pictures leave a declared label undrawn.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the Azure trust node diagrams cannot be rendered. Install graphviz "
            "(apt-get install graphviz) or run the docker one-liner in architecture/trust_azure.py."
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
    parser = argparse.ArgumentParser(description="Render the FLIP Azure trust node architecture diagrams.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    args = parser.parse_args(argv)
    for path in render(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
