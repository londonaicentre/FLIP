.. _deploy-flip-node-azure:

###########################
Deploy a FLIP node on Azure
###########################

A FLIP node on Azure is an on-prem node whose host happens to be an Azure VM: the same trust-side
stack (trust-api, imaging-api, data-access-api, FL client, XNAT, Orthanc), the same on-prem verbs,
built entirely from code in ``deploy/providers/azure``. The VM configures itself at first boot and
is operated only through Azure Run Command: there are no inbound ports and no SSH from outside. It
receives its kit the way any trust does, from the hub admin, through a private container it reads
with its own identity. For a node on a Trust's own host see :doc:`deploy-flip-node-on-prem`. The
same trust can run instead as the ``flip-trust`` Helm chart on a one-node AKS cluster: see
`Run it on AKS instead`_.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

.. code-block:: text

        Operator's machine                       Azure subscription
   ┌─────────────────────────┐        ┌──────────────────────────────────────────┐
   │ make plan / apply       │──ARM──▶│ subnet (inbound denied) ── VM ── data disk │
   │ make kit-upload         │──Entra▶│ kit drop (private container, 1-day blobs) │
   │ make selftest / join    │──Run──▶│   ▲ read with the VM's managed identity   │
   └─────────────────────────┘ Command└──┼───────────────────────────────────────┘
                                         │ outbound only: the site's firewall, or the
                                         │ node's own NAT gateway on a standalone node
                                         ▼
                           Central Hub API (HTTPS) and FL server (mTLS)

+---------------------------+--------------------------------------------------------------+
| Layer                     | What it holds                                                |
+===========================+==============================================================+
| Bootstrap (once)          | Terraform state storage (Entra access only), a budget alert  |
+---------------------------+--------------------------------------------------------------+
| Node (per session)        | All inbound denied at the VM's NIC, no public IP on the VM,  |
|                           | an Ubuntu 24.04 VM (Trusted Launch), a data disk for         |
|                           | ``/opt/flip``, a daily auto-shutdown. On a standalone node   |
|                           | also a VNet and subnet leaving through a NAT gateway with    |
|                           | its own IP; on a site subnet, none of these                  |
+---------------------------+--------------------------------------------------------------+
| Kit drop (with the node)  | A storage account with shared keys off and a private         |
|                           | container. Its firewall admits only the node subnet and the  |
|                           | operator's IP; a daily lifecycle run deletes each blob a     |
|                           | day after upload, so within about 48 hours                   |
+---------------------------+--------------------------------------------------------------+

********************
Network and security
********************

This section is for the Trust's network and security teams.

**The node never accepts a connection.** Its NIC carries its own network security group, which
denies all inbound traffic, so a permissive site subnet cannot open it. The VM has no public IP,
and nothing listens for SSH from outside. Operators reach it only through Azure Run Command,
which travels over the VM agent's own outbound channel.

**The hub admits the node by its credentials, never by its address.** Hub API calls carry the
Trust's API key over HTTPS. FL traffic is TLS authenticated with the credentials in the Trust's FL
kit (mutual TLS on NVFLARE).
The hub does not allowlist Trust IP addresses, so the node needs no fixed public IP. FLIP once
gave its AWS trust one for allowlisting and removed it, because it protected nothing.

**Outbound traffic takes one of two paths.**

- **The Trust's own subnet** (``SUBNET_ID``): the recommended deployment. The node follows the
  Trust's routes, normally through its firewall, and FLIP creates no network, no NAT gateway and
  no public IP. The subnet needs the ``Microsoft.Storage`` service endpoint for the kit drop.
- **A standalone node** (no ``SUBNET_ID``): for trials. FLIP creates a VNet whose subnet leaves
  through a NAT gateway. The gateway's IP is created and destroyed with the node, and no address
  outlives it.

**What the Trust's firewall must allow, outbound only.** Measured behind a deny-all Azure
Firewall: each registry serves its image layers from a second host, so allowing the registry
name alone makes every pull fail.

+------------------------------------------+---------------------------------------------------+
| Destination                              | Why                                               |
+==========================================+===================================================+
| The hub's URL, port 443                  | Task polling, heartbeats, results (HTTPS, API     |
|                                          | key)                                              |
+------------------------------------------+---------------------------------------------------+
| The hub's FL endpoint, port 8002 (8003   | Federated training. It has a fixed IP, so a       |
| on staging)                              | firewall without FQDN network rules (Azure        |
|                                          | Firewall Basic) can allow it by address. **Exempt |
|                                          | it from TLS inspection**: a proxy that re-signs   |
|                                          | the traffic breaks its certificate check          |
+------------------------------------------+---------------------------------------------------+
| ``ghcr.io``,                             | FLIP's container images and their layers          |
| ``pkg-containers.githubusercontent.com`` |                                                   |
+------------------------------------------+---------------------------------------------------+
| ``registry-1.docker.io``,                | The observability images (Grafana, Loki, Alloy)   |
| ``auth.docker.io``,                      | and XNAT's Docker socket proxy, from Docker Hub   |
| ``production.cloudfront.docker.com``     |                                                   |
+------------------------------------------+---------------------------------------------------+
| ``github.com``                           | First boot clones FLIP at a pinned commit         |
+------------------------------------------+---------------------------------------------------+
| ``huggingface.co``                       | First install loads the published mock trust data |
+------------------------------------------+---------------------------------------------------+
| The OS, Docker and uv package sources    | First-boot installs                               |
+------------------------------------------+---------------------------------------------------+

**What leaves the site.** Model updates during training, and aggregate results. Patient records,
images and OMOP rows stay on the node.

**Where the secrets live.** The Trust's API key and its trust-internal service key are in its
kit file, installed owner-only on the node; the hub never sees the trust-internal key. The kit
reaches the node through the kit drop, a storage account with shared keys off, a firewall that
admits only the node's subnet and the operator's IP, and a rule that deletes every kit within
about 48 hours. The node reads it with its own managed identity.

*************
Prerequisites
*************

- An Azure subscription you may create resources in, and its **name**. Every command takes it
  as ``AZ_SUBSCRIPTION``; nothing uses the Azure CLI's default subscription.
- A region with capacity for ``Standard_D4s_v5`` (4 vCPU). Free Trial subscriptions often
  cannot create 4-vCPU VMs in the UK regions; check with
  ``az vm list-skus --subscription "<name>" --location <region> --size Standard_D4 --all -o table``.
- On your machine: Azure CLI (logged in with ``az login``), Terraform 1.13 or newer, GNU make,
  ``uv`` and ``yq`` (the self-test kit is provisioned on your machine, never on the node), and an
  SSH public key at ``~/.ssh/id_ed25519.pub``, or pass ``SSH_PUBLIC_KEY_FILE=<path>`` to ``plan``
  and ``destroy``. Azure requires one, but nothing listens for SSH, so a throwaway key will do.
- The FLIP commit you deploy pushed to GitHub: the VM clones it.

**************************
Build the node and test it
**************************

.. code-block:: bash

   cd deploy/providers/azure
   SUB="<subscription name>"
   REGION=<region>   # the node's region
   SUBNET=<subnet resource id>   # the site's subnet; leave empty for a standalone node (see below)

   make bootstrap AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   make init      AZ_SUBSCRIPTION="$SUB"
   make plan      AZ_SUBSCRIPTION="$SUB" LOCATION=$REGION SUBNET_ID=$SUBNET
   make apply     AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make status    AZ_SUBSCRIPTION="$SUB"      # repeat until "provisioned: <time>"

``init`` can be refused with a 403 for a minute or two after ``bootstrap``, while Azure applies
your access to the new state storage; run it again. First boot takes a few minutes.

A **second node in the same subscription** needs its own name and its own checkout: give every
command the same ``NAME_PREFIX=<prefix>`` (default ``flipaz``), and run them from a separate clone.
The bootstrap keeps its Terraform state in the checkout, so a different prefix in the same checkout
would rename, and so replace, the first node's bootstrap.

Then run the **hubless self-test** on both FL backends. ``selftest-kit`` provisions a throwaway FL
kit on your machine, packs only the client's files and puts them in the kit drop, so it can run while
the node is still booting; ``selftest`` has the node fetch that kit, bring the trust stack up with
no hub, check it and tear it down:

.. code-block:: bash

   make selftest-kit AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
   make selftest     AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
   make logs         AZ_SUBSCRIPTION="$SUB" UNIT=flip-selftest-nvflare
   make report       AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare

The report has twelve checks: the delivered kit, the kit staged for the FL client, the kit file,
the stack starting, the three API health endpoints, the OMOP and Orthanc seed markers, Orthanc
refusing anonymous requests, a real DICOM C-STORE from Orthanc into XNAT, and the FL client
staying up. An unreachable hub is expected at this stage, not a failure. Repeat with
``FL_BACKEND=flower``.

On a new node the first self-test takes about 20 minutes, most of it loading the mock OMOP and
Orthanc data; later runs reuse that data and take about 10. ``make report`` shows the last finished run (nothing yet on a
new node), so check its start time. The node runs one Run Command at a time, so let one ``make`` call return before
starting the next: a second one is refused with ``Conflict: Run command extension execution is in
progress``.

The reports stay on the node, under ``/opt/flip/selftest/`` (``selftest-<backend>-<time>.md`` and
``.json``), and ``make destroy`` deletes them: keep a copy of each ``make report`` first.

**********
Join a hub
**********

On the hub admin's side (hub AWS credentials and the hub's env file), the steps are those for any
on-prem trust:

.. code-block:: bash

   make new-trust TRUST_CODE=<CODE> TRUST_NAME="<name>" PROD=<env>
   make -C deploy/providers/AWS register-trusts KIT=<CODE> PROD=<env>
   make sync-trust-kit KIT=<CODE> PROD=<env>
   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<slot> PROD=<env>

.. note::

   Until `FLIP#1419 <https://github.com/londonaicentre/FLIP/issues/1419>`_ is fixed, the admin
   also has to: run ``make generate-xnat-credentials KIT=<CODE> PROD=<env>``; copy
   ``trust/.env.<CODE>.<env>`` to ``trust/.env.<slot>`` (the packager reads that name and wants
   the FL slot, e.g. ``Trust_3``); and, for a node outside AWS, set ``DOCKER_REGISTRY`` and
   ``DOCKER_FL_REGISTRY`` to ``ghcr.io/londonaicentre/``, ``DOCKER_TAG`` to a tag every trust image
   exists at (``stag`` on staging) and, on the LZA staging edge, ``FL_SERVER_PORT=8003``.

   A test trust seeded with the published mock data on slot ``Trust_3`` or above also needs
   ``SOURCE_TRUST=1`` (or ``2``) in its kit: the mock data has two partitions, and the slot number
   is only the default choice of partition.

The tarball then goes to the node through the kit drop, and ``join`` installs it:

.. code-block:: bash

   make kit-upload AZ_SUBSCRIPTION="$SUB" TARBALL=flip-trust-kit-<slot>-<date>.tar.gz
   make join       AZ_SUBSCRIPTION="$SUB" NAME=flip-trust-kit-<slot>-<date>.tar.gz KIT=<slot> PROD=<env>
   make logs       AZ_SUBSCRIPTION="$SUB" UNIT=flip-join-<slot>

``join`` fetches the kit with the node's identity, installs its kit file (owner-only) with the
node's own settings appended, stages the FL kit for the client that reads it and runs
``up-onprem-trust``, whose readiness checklist must pass before anything starts. The node's FL
client dials the hub's FL edge outbound; on the LZA estates that edge admits any address and the
kit's mTLS certificates decide who gets in, so no allowlist change is needed.

******************
Operate and remove
******************

- ``make reprovision REF=<sha or vX.Y.Z>`` moves the node's code without rebuilding it.
- ``make stop`` / ``make start`` deallocate and start the VM (a stopped VM costs only its disks,
  plus the NAT gateway on a standalone node). The VM also shuts itself down at 19:00 UK time by default.
- ``make destroy`` (with the same ``SUBNET_ID``) removes the node, its network and the kit drop,
  and keeps the bootstrap layer;
  ``make destroy-bootstrap`` removes the rest and refuses while a node exists.

***************
Troubleshooting
***************

- **First boot fails.** ``make status`` shows cloud-init's state and ``make logs
  UNIT=cloud-final`` its output; the portal's boot diagnostics show the serial console. Fix the
  cause in the repo, push, then ``make reprovision REF=<new sha>``.
- **The node cannot fetch its kit.** It retries for about five minutes, then names the HTTP
  status: 403 right after an apply is the node's read role still propagating, and a 403 that
  persists on a site-supplied subnet (``SUBNET_ID``) means that subnet lacks the
  ``Microsoft.Storage`` service endpoint the kit drop's firewall needs; 404 means the kit
  was never uploaded or has expired (kits are deleted within about 48 hours).
- **kit-upload is refused (403).** Your public IP has changed since ``plan``: plan and apply
  again (or pass ``OPERATOR_IP=<ip>``).
- **A self-test check fails.** The report names it. Fix the cause in the repo and reprovision;
  do not edit the node by hand.

**********************
Run it on AKS instead
**********************

The ``aks`` root builds a one-node AKS cluster (``Standard_D4s_v5``, Free control-plane tier) on
the same bootstrap layer, and the trust runs on it as the ``flip-trust`` Helm chart. Sign-in is
through Entra. Outbound traffic follows the same two
paths as the VM (`Network and security`_): on the Trust's subnet (``SUBNET_ID``) the cluster uses
the Trust's routes and creates no outbound IP, and that subnet needs a route table sending
outbound traffic to the Trust's firewall, which AKS checks before it creates the cluster; without
``SUBNET_ID`` it leaves through its own NAT gateway. The API server admits only your IP and the
nodes' own egress: the NAT gateway's IP, or on the Trust's subnet the firewall's public IPs, which
you pass as ``NODE_EGRESS_IPS=<ip>[,<ip>]`` to ``aks-plan`` and ``aks-destroy``. A node that cannot
reach its API server never joins the cluster. The trial's 4 vCPUs are the VM's too, so
``aks-apply`` refuses while the VM exists: ``make destroy`` first. A running cluster costs roughly
$0.25 an hour, plus the NAT gateway on a standalone cluster.

Behind the Trust's firewall the cluster needs more than the VM's list (`Network and security`_):

- **AKS itself:** the outbound rules Microsoft documents for AKS ("Outbound network and FQDN
  rules for AKS clusters"). On Azure Firewall that is the ``AzureKubernetesService`` FQDN tag,
  and the region's ``AzureCloud`` service tag on TCP/UDP 443, 1194 and 9000, plus NTP.
- **XNAT plugins:** the chart downloads them at every pod start, from ``xnat.org``,
  ``api.bitbucket.org`` (served from ``bbuseruploads.s3.amazonaws.com``) and ``github.com``
  (served from ``release-assets.githubusercontent.com``). The VM's image carries them instead.

That list was measured on 17 Oct by running this chapter behind a deny-all Azure Firewall Basic.

On your machine you also need ``kubectl``, ``kubelogin``, Helm 3.16 or newer, ``jq`` and
``python3``.

.. code-block:: bash

   cd deploy/providers/azure
   SUB="<subscription name>"

   make bootstrap       AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   make aks-init        AZ_SUBSCRIPTION="$SUB"
   make aks-plan        AZ_SUBSCRIPTION="$SUB" LOCATION=<region> SUBNET_ID=<subnet resource id, or empty> NODE_EGRESS_IPS=<firewall IPs, with SUBNET_ID>
   make aks-apply       AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make aks-credentials AZ_SUBSCRIPTION="$SUB"      # kube context flipaz-aks, signed in through Entra

The hub admin registers the trust and packages its kit as in `Join a hub`_. With the tarball in
hand:

.. code-block:: bash

   make aks-join TARBALL=flip-trust-kit-<slot>-<date>.tar.gz KIT=<slot> PROD=<env>   # MOCK_PACS=1 on a test node
   make aks-selftest

``aks-join`` unpacks the tarball under ``build/kits/``, installs the kit file as
``trust/.env.<slot>.<env>``, generates the chart's ``values-secrets.yaml`` from it and puts the FL
kit in a Secret, because a managed node can be replaced at any time. A Flower kit's Secret holds only
``ca.crt`` and the slot's own SuperNode credential: the tarball's ``certificates/`` also carries the
SuperLink's ``server.key``, which never belongs on a trust. It then deploys the chart with
``values-aks.yaml`` and ``values-small-node.yaml``, and records them in the kit file as
``K8S_PLATFORM_VALUES``, so a later deploy or upgrade of that slot keeps them. The first install takes up to half an
hour, most of it XNAT's first start. ``MOCK_PACS=1`` seeds the published mock studies into the
chart's Orthanc, which a test node needs for an imaging pull and for the self-test.

The chart loads no OMOP core vocabulary: it is licensed, so each site loads its own copy, as
:ref:`onprem-core-vocabulary` describes. Until then, cohort queries that name concepts through
``omop.concept`` return nothing, so test with a query that does not.

``aks-selftest`` runs the VM self-test's checks against the release
and writes its report under ``build/selftest/`` at the repo root; it needs no hub. The trust has joined when its
heartbeat is answered:

.. code-block:: bash

   kubectl --context flipaz-aks -n flip-trust logs deploy/trust-release-flip-trust-trust-api | grep heartbeat   # 200 OK

Operate and remove:

- ``make aks-stop`` stops the node billing; ``make aks-start`` starts it and admits your current
  IP. After your IP changes, ``make aks-allow-ip`` admits the new one.
- ``make aks-deploy KIT=<slot> PROD=<env>`` redeploys or upgrades from the installed kit.
- ``make aks-destroy`` (with the same ``SUBNET_ID`` and ``NODE_EGRESS_IPS``) removes the cluster and its network and keeps
  the bootstrap layer;
  ``make destroy-bootstrap`` refuses while the cluster or a VM exists.

Troubleshooting:

- **kubectl times out or is refused.** Your IP has changed: ``make aks-allow-ip``.
- **Pods stay Pending while the node is full of dcm2niix Jobs.** Jobs created before the
  conversion cap still request a CPU each. ``make aks-clear-jobs`` selects the Jobs whose pod
  template carries a CPU limit at all (the capped registration sets none), lists them and deletes
  them once you type ``yes``. Then relaunch dcm2niix on those scans from XNAT
  (``trust/deploy/helm/TROUBLESHOOTING.md`` §2.3d).
