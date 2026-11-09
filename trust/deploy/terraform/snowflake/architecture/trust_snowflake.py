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

"""Render the Snowflake trust node architecture diagrams from the Terraform beside this directory.

Diagram-as-code in the style of ``../../aws/eks/architecture/trust_eks.py``: nodes, clusters and edges are declared
with the ``diagrams`` library and rendered through graphviz, and ``TERRAFORM_ADDRESSES`` maps every drawn label to
the Terraform addresses it stands for, written ``<root>/<address>`` over the two roots (``snowflake-platform`` and
``snowflake``). ``tests/test_architecture_diagram.py`` checks that map against the ``.tf`` files in both directions,
so a resource cannot be removed, renamed or added without the diagrams changing with it.

Two pictures. The account picture shows who applies what: the platform tier (a person, break glass) and the workload
tier (the deploy user). The workload picture shows what runs: one SPCS service per container, the stage each one
mounts and the role every service is created as. Dashed nodes exist only in some configurations: the hub egress
integration (``enable_hub_egress``, impossible on a trial account) and the GPU pool (``enable_gpu_pool``).

Graphviz is not installed on the dev hosts; without ``dot`` this module refuses to render. Run it in docker::

    docker run --rm -v "$PWD:/work" -w /work/trust/deploy/terraform/snowflake python:3.12-slim sh -c \\
      "apt-get update -qq && apt-get install -y -qq graphviz >/dev/null && \\
       pip -q install diagrams && python -m architecture.trust_snowflake --out docs"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node
from diagrams.generic.storage import Storage
from diagrams.onprem.client import Users
from diagrams.onprem.compute import Server
from diagrams.onprem.container import Docker
from diagrams.onprem.database import PostgreSQL
from diagrams.onprem.network import Internet
from diagrams.onprem.security import Vault
from diagrams.onprem.vcs import Github
from diagrams.programming.flowchart import Database, Document, MultipleDocuments, PredefinedProcess
from diagrams.saas.analytics import Snowflake

_P = "snowflake-platform"
_W = "snowflake"

#: Drawn label -> Terraform addresses it stands for, as ``<root>/<address>``. Labels with no Terraform here (the
#: services and jobs of ``trust/deploy/snowflake``, the hub, GHCR, Hugging Face, the people) map to an empty tuple.
TERRAFORM_ADDRESSES: dict[str, tuple[str, ...]] = {
    # ---- outside the account -----------------------------------------------------------------------
    "Account admin": (),
    "Operator": (),
    "Central Hub": (),
    "GHCR": (),
    "Hugging Face": (),
    # ---- platform tier (snowflake-platform, break glass) ---------------------------------------------
    "Trust roles": (
        f"{_P}/snowflake_account_role.deploy",
        f"{_P}/snowflake_account_role.svc",
        f"{_P}/snowflake_account_role.omop_reader",
        f"{_P}/snowflake_account_role.omop_loader",
        f"{_P}/snowflake_account_role.observer",
        f"{_P}/snowflake_account_role.converter",
        f"{_P}/snowflake_grant_account_role.to_deploy",
        f"{_P}/snowflake_grant_account_role.omop_reader_to_svc",
        f"{_P}/snowflake_grant_account_role.deploy_to_sysadmin",
    ),
    "Deploy user": (f"{_P}/snowflake_service_user.deploy", f"{_P}/snowflake_grant_account_role.deploy_to_user"),
    "Account grant": (f"{_P}/snowflake_grant_privileges_to_account_role.deploy_account",),
    "Account policies": (
        f"{_P}/snowflake_network_policy.admin",
        f"{_P}/snowflake_network_policy_attachment.account",
        f"{_P}/snowflake_password_policy.account",
        f"{_P}/snowflake_account_password_policy_attachment.account",
        f"{_P}/snowflake_session_policy.account",
        f"{_P}/snowflake_account_session_policy_attachment.account",
    ),
    "Hub egress": (
        f"{_P}/snowflake_network_rule.hub_egress",
        f"{_P}/snowflake_external_access_integration.hub",
        f"{_P}/snowflake_grant_privileges_to_account_role.deploy_eai",
        f"{_P}/snowflake_grant_privileges_to_account_role.svc_eai",
    ),
    "Warehouse": (
        f"{_P}/snowflake_warehouse.trust",
        f"{_P}/snowflake_grant_privileges_to_account_role.deploy_warehouse",
        f"{_P}/snowflake_grant_privileges_to_account_role.warehouse_usage",
        f"{_P}/snowflake_grant_privileges_to_account_role.observer_warehouse",
    ),
    "Resource monitor": (f"{_P}/snowflake_resource_monitor.trust",),
    "Platform database": (f"{_P}/snowflake_database.platform", f"{_P}/snowflake_schema.platform"),
    "Event table": (
        f"{_P}/snowflake_execute.event_table",
        f"{_P}/snowflake_execute.event_table_time_travel",
        f"{_P}/snowflake_execute.observer_event_table",
        f"{_P}/snowflake_account_parameter.event_table",
    ),
    "Observability views": (
        f"{_P}/snowflake_view.events",
        f"{_P}/snowflake_view.service_status",
        f"{_P}/snowflake_view.service_restarts",
        f"{_P}/snowflake_view.recent_errors",
        f"{_P}/snowflake_view.fl_client_logs",
        f"{_P}/snowflake_view.compute_pool_cost",
        f"{_P}/snowflake_view.warehouse_cost",
        f"{_P}/snowflake_grant_privileges_to_account_role.observer_platform_database",
        f"{_P}/snowflake_grant_privileges_to_account_role.observer_obs_schema",
        f"{_P}/snowflake_grant_privileges_to_account_role.observer_obs_views",
        f"{_P}/snowflake_grant_privileges_to_account_role.observer_future_obs_views",
    ),
    "Prune task": (f"{_P}/snowflake_task.prune_events",),
    # ---- workload tier (snowflake, every deploy) ------------------------------------------------------
    "Trust database": (f"{_W}/snowflake_database.trust", f"{_W}/snowflake_schema.trust"),
    "Service pool": (f"{_W}/snowflake_compute_pool.trust",),
    "Job pool": (f"{_W}/snowflake_compute_pool.trust",),
    "GPU pool": (f"{_W}/snowflake_compute_pool.trust",),
    "Image repository": (f"{_W}/snowflake_image_repository.images",),
    "Stages": (f"{_W}/snowflake_stage_internal.trust",),
    "Converter procedures": (
        f"{_W}/snowflake_procedure_sql.convert_scan",
        f"{_W}/snowflake_procedure_sql.convert_status",
        f"{_W}/snowflake_grant_ownership.converter_procedures",
    ),
    "Suspend task": (f"{_W}/snowflake_task.suspend_pools",),
    # ---- the stages, one node each in the workload picture ----------------------------------------------
    "COHORT_SNAPSHOTS": (f"{_W}/snowflake_stage_internal.trust",),
    "GOVERNANCE": (f"{_W}/snowflake_stage_internal.trust",),
    "IMAGES": (f"{_W}/snowflake_stage_internal.trust",),
    "FL_KIT": (f"{_W}/snowflake_stage_internal.trust",),
    "XNAT_ARCHIVE": (f"{_W}/snowflake_stage_internal.trust",),
    "CONVERTER_IO": (f"{_W}/snowflake_stage_internal.trust",),
    "SEED": (f"{_W}/snowflake_stage_internal.trust",),
    # ---- trust/deploy/snowflake: services, jobs and what sync-kit writes (no Terraform) ------------------
    "trust-api": (),
    "data-access-api": (),
    "imaging-api": (),
    "fl-client-net-1": (),
    "xnat-web": (),
    "xnat-db": (),
    "orthanc": (),
    "dcm2niix job": (),
    "One-shot jobs": (),
    "OMOP tables": (),
    "Secrets": (),
    "Service role": (),
}

#: Resource types that are architecture: every resource of one of these types, in either root, must appear in
#: ``TERRAFORM_ADDRESSES``. Grants, attachments and ``snowflake_execute`` statements are listed with the object they
#: belong to where that helps, but are not required.
DRAWN_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "snowflake_account_role",
        "snowflake_service_user",
        "snowflake_warehouse",
        "snowflake_resource_monitor",
        "snowflake_network_rule",
        "snowflake_external_access_integration",
        "snowflake_network_policy",
        "snowflake_password_policy",
        "snowflake_session_policy",
        "snowflake_account_parameter",
        "snowflake_database",
        "snowflake_schema",
        "snowflake_view",
        "snowflake_task",
        "snowflake_compute_pool",
        "snowflake_image_repository",
        "snowflake_stage_internal",
        "snowflake_procedure_sql",
    }
)

#: Font sizes and spacing copied from the Central Hub diagrams so the pictures read as one set.
GRAPH_ATTR = {"fontsize": "24", "pad": "0.4", "nodesep": "1.0", "ranksep": "0.7", "splines": "spline"}
NODE_ATTR = {"fontsize": "15"}
EDGE_ATTR = {"fontsize": "15"}
CLUSTER_ATTR = {"fontsize": "15", "margin": "20"}
OPTIONAL_CLUSTER_ATTR = {**CLUSTER_ATTR, "style": "dashed"}
DEFAULT_NAME = "trust-node-snowflake"

#: The pictures ``render`` produces, as ``<DEFAULT_NAME>-<suffix>.png``.
DIAGRAMS: tuple[tuple[str, str, str], ...] = (
    ("account", "FLIP trust node on Snowflake — the two tiers and the account objects", "TB"),
    ("workload", "FLIP trust node on Snowflake — services, stages and data on Snowpark Container Services", "TB"),
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


def _draw_account(drawn: _Drawn) -> None:
    """Who applies what: the break-glass platform tier, the routine workload tier, and what each one owns."""
    admin = drawn.node("Account admin", Users, caption="Account admin\n(person, MFA)")
    operator = drawn.node("Operator", Users, caption="Operator\n(tofu, snow, docker)")
    ghcr = drawn.node("GHCR", Github, caption="GHCR\n(trust images)")
    hf = drawn.node("Hugging Face", Internet, caption="Hugging Face\n(seed data)")
    hub = drawn.node("Central Hub", Server, caption="Central Hub\n(any FLIP hub)")

    with _cluster("Snowflake account (one region: clinical data never leaves it)"):
        with _cluster("Platform tier — snowflake-platform, own state, applied by a person"):
            roles = drawn.node(
                "Trust roles",
                Vault,
                caption="Six trust roles\n(DEPLOY, SVC,\nOMOP_READER, ...)",
            )
            deploy_user = drawn.node("Deploy user", Users, caption="Deploy user\n(SERVICE, key pair)")
            grant = drawn.node(
                "Account grant",
                Document,
                caption="Account grant\n(four privileges,\ndeploy role only)",
            )
            drawn.node("Account policies", Document, caption="Account policies\nnetwork · password\nsession")
            warehouse = drawn.node("Warehouse", Snowflake, caption="Warehouse\nFLIP_<CODE>_WH")
            monitor = drawn.node("Resource monitor", Document, caption="Resource monitor\n(warehouse only)")
            egress = drawn.node(
                "Hub egress",
                Internet,
                caption="Egress rule + external\naccess integration\n(off by default;\nnot on a trial)",
                style="dashed",
            )
            with _cluster(drawn.mark("Platform database") + " FLIP_PLATFORM (outlives a workload destroy)"):
                events = drawn.node(
                    "Event table", Database, caption="Event table\nOBS.FLIP_EVENTS\n(account EVENT_TABLE)"
                )
                views = drawn.node(
                    "Observability views",
                    MultipleDocuments,
                    caption="Seven views\nstatus · restarts · errors\nFL logs · cost",
                )
                prune = drawn.node("Prune task", PredefinedProcess, caption="Prune task\n(daily retention)")

        with _cluster("Workload tier — snowflake, own state, applied as the deploy user"):
            with _cluster(drawn.mark("Trust database") + " FLIP_TRUST_<CODE> (schemas APP · OMOP · STAGES · OBS)"):
                repo = drawn.node("Image repository", Docker, caption="Image repository\nAPP.IMAGES")
                stages = drawn.node("Stages", Storage, caption="Seven internal stages\n(SSE, directory tables)")
                procedures = drawn.node(
                    "Converter procedures",
                    PredefinedProcess,
                    caption="CONVERT_SCAN\nCONVERT_STATUS\n(owner: CONVERTER)",
                )
                suspend = drawn.node("Suspend task", PredefinedProcess, caption="Nightly suspend task\n(pools)")
            service_pool = drawn.node("Service pool", Snowflake, caption="Service pool\nFLIP_<CODE>_POOL")
            job_pool = drawn.node("Job pool", Snowflake, caption="Job pool\nFLIP_<CODE>_JOB_POOL")
            drawn.node("GPU pool", Snowflake, caption="GPU pool\n(optional)", style="dashed")

    admin >> Edge(label="tofu apply\n(just-in-time grant)") >> roles
    roles - Edge(style="dotted", label="DEPLOY granted") - deploy_user
    grant - Edge(style="dotted") - roles
    monitor - Edge(style="dotted", label="caps") - warehouse
    prune >> Edge(label="deletes old rows") >> events
    views - Edge(style="dotted", label="read") - events
    operator >> Edge(label="signs in as") >> deploy_user
    deploy_user >> Edge(label="tofu apply (workload)\nsnow: services, secrets") >> stages
    operator >> Edge(label="docker push\n(mirrored by digest)") >> repo
    ghcr >> Edge(label="pull :443") >> operator
    hf >> Edge(label="seed data :443") >> operator
    operator >> Edge(label="PUT seed, FL kit") >> stages
    suspend >> Edge(label="suspends") >> service_pool
    suspend >> Edge(label="suspends") >> job_pool
    procedures >> Edge(label="EXECUTE JOB SERVICE") >> job_pool
    service_pool >> Edge(style="dashed", label="hub API :443, FL\n(only with the integration)") >> egress
    egress >> Edge(style="dashed") >> hub


def _draw_workload(drawn: _Drawn) -> None:
    """What runs: one SPCS service per container, the stage each mounts, and the role they all share."""
    hub = drawn.node("Central Hub", Server, caption="Central Hub\n(any FLIP hub)")
    egress = drawn.node("Hub egress", Internet, caption="External access\nintegration\n(optional)", style="dashed")

    with _cluster("Snowflake account"):
        role = drawn.node(
            "Service role",
            Vault,
            caption="FLIP_<CODE>_SVC\n(owns every service)",
        )
        secrets = drawn.node("Secrets", Vault, caption="SECRET objects\n(from sync-kit)")
        with _cluster(drawn.mark("Service pool") + ": one service per container, no public endpoint"):
            trust_api = drawn.node("trust-api", Docker)
            data_access = drawn.node("data-access-api", Docker)
            imaging_api = drawn.node("imaging-api", Docker)
            fl_client = drawn.node("fl-client-net-1", Docker, caption="fl-client-net-1\n(NVFLARE or Flower)")
            xnat_web = drawn.node("xnat-web", Docker, caption="xnat-web\n(no Container Service)")
            xnat_db = drawn.node("xnat-db", PostgreSQL, caption="xnat-db\n(block volume)")
            orthanc = drawn.node("orthanc", Docker, caption="orthanc\n(mock PACS,\nblock volume)")
        with _cluster(drawn.mark("Job pool") + ": job services, run on demand"):
            converter = drawn.node("dcm2niix job", Docker, caption="dcm2niix job\n(one per scan)")
            drawn.node("One-shot jobs", Docker, caption="One-shot jobs\n(configure, seed,\nsmoke, selftest)")
        with _cluster(drawn.mark("Trust database") + " FLIP_TRUST_<CODE>"):
            omop = drawn.node("OMOP tables", Database, caption="OMOP tables\n(schema OMOP)")
            procedures = drawn.node("Converter procedures", PredefinedProcess, caption="CONVERT_SCAN\nCONVERT_STATUS")
            with _cluster("Schema STAGES"):
                snapshots = drawn.node("COHORT_SNAPSHOTS", Storage)
                governance = drawn.node("GOVERNANCE", Storage)
                images = drawn.node("IMAGES", Storage, caption="IMAGES\n(net-N prefix\nper FL net)")
                kit = drawn.node("FL_KIT", Storage, caption="FL_KIT\n(net-N prefix)")
                archive = drawn.node("XNAT_ARCHIVE", Storage)
                converter_io = drawn.node("CONVERTER_IO", Storage)
                seed = drawn.node("SEED", Storage)
        events = drawn.node("Event table", Database, caption="Event table\n(container logs)")

    trust_api >> Edge(label="cohort tasks") >> data_access
    trust_api >> Edge(label="imaging tasks") >> imaging_api
    fl_client >> Edge(label="cohort data") >> data_access
    fl_client >> Edge(label="images") >> imaging_api
    data_access >> Edge(label="SQL, session token\n(OMOP_READER)") >> omop
    data_access - Edge(style="dotted", label="frozen cohorts") - snapshots
    data_access - Edge(style="dotted", label="policy") - governance
    imaging_api >> Edge(label="REST") >> xnat_web
    imaging_api - Edge(style="dotted", label="download cache") - images
    imaging_api >> Edge(label="CALL") >> procedures
    procedures >> Edge(label="launches") >> converter
    converter - Edge(style="dotted", label="reads DICOM") - archive
    converter - Edge(style="dotted", label="writes NIfTI") - converter_io
    fl_client - Edge(style="dotted", label="own net only") - images
    fl_client - Edge(style="dotted", label="read") - kit
    xnat_web >> xnat_db
    xnat_web >> Edge(label="DQR: C-MOVE / C-STORE", dir="both") >> orthanc
    xnat_web - Edge(style="dotted", label="archive") - archive
    orthanc - Edge(style="dotted", label="seed-orthanc job") - seed
    role - Edge(style="dotted", label="owner of all services") - trust_api
    secrets - Edge(style="dotted", label="mapped per service") - trust_api
    trust_api >> Edge(label="logs") >> events
    trust_api >> Edge(style="dashed", label="HTTPS :443 poll tasks, heartbeat") >> egress
    fl_client >> Edge(style="dashed", label="FL gRPC") >> egress
    egress >> Edge(style="dashed") >> hub


_PAINTERS: dict[str, Callable[[_Drawn], None]] = {"account": _draw_account, "workload": _draw_workload}


def render(out_dir: Path) -> list[Path]:
    """Render the Snowflake trust node diagrams.

    Args:
        out_dir (Path): Directory to write into; created if missing.

    Returns:
        list[Path]: The rendered files.

    Raises:
        RuntimeError: If graphviz ``dot`` is not on ``PATH``, or if the pictures leave a declared label undrawn.
    """
    if shutil.which("dot") is None:
        raise RuntimeError(
            "graphviz `dot` is not installed, so the Snowflake trust node diagrams cannot be rendered. Install "
            "graphviz (apt-get install graphviz) or run the docker one-liner in architecture/trust_snowflake.py."
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
    parser = argparse.ArgumentParser(description="Render the FLIP Snowflake trust node architecture diagrams.")
    parser.add_argument("--out", type=Path, default=Path("docs"), help="output directory (default: docs)")
    args = parser.parse_args(argv)
    for path in render(args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
