.. _deploy-flip-node-azure:

###########################
Deploy a FLIP node on Azure
###########################

A FLIP node on Azure is an on-prem node whose host happens to be an Azure VM: the same trust-side
stack (trust-api, imaging-api, data-access-api, FL client, XNAT, Orthanc), the same on-prem verbs,
built entirely from code in ``deploy/providers/azure``. The VM configures itself at first boot and
is operated only through Azure Run Command: there are no inbound ports and no SSH from outside. It
receives its kit the way any trust does, from the hub admin, through a private container it reads
with its own identity. For a node on a Trust's own host see :doc:`deploy-flip-node-on-prem`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

.. code-block:: text

        Operator's machine                       Azure subscription
   ┌─────────────────────────┐        ┌──────────────────────────────────────────┐
   │ make plan / apply       │──ARM──▶│ VNet (no inbound rules) ── VM ── data disk │
   │ make kit-upload         │──Entra▶│ kit drop (private container, 1-day blobs) │
   │ make selftest / join    │──Run──▶│   ▲ read with the VM's managed identity   │
   └─────────────────────────┘ Command└──┼───────────────────────────────────────┘
                                         │ outbound only, from a static IP
                                         ▼
                           Central Hub API (HTTPS) and FL server (mTLS)

The VM's outbound address is a static public IP held in a long-lived *bootstrap* layer, so it
survives the node being destroyed and rebuilt.

+---------------------------+--------------------------------------------------------------+
| Layer                     | What it holds                                                |
+===========================+==============================================================+
| Bootstrap (once)          | Terraform state storage (Entra access only), a budget alert, |
|                           | the static outbound IP                                       |
+---------------------------+--------------------------------------------------------------+
| Node (per session)        | VNet and subnet with no inbound rules, an Ubuntu 24.04 VM    |
|                           | (Trusted Launch), a data disk for ``/opt/flip``, a daily     |
|                           | auto-shutdown                                                |
+---------------------------+--------------------------------------------------------------+
| Kit drop (with the node)  | A storage account with shared keys off and a private         |
|                           | container. Its firewall admits only the node subnet and the  |
|                           | operator's IP; every blob is deleted a day after upload      |
+---------------------------+--------------------------------------------------------------+

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
  SSH public key (Azure requires one; nothing listens for SSH).
- The FLIP commit you deploy pushed to GitHub: the VM clones it.

**************************
Build the node and test it
**************************

.. code-block:: bash

   cd deploy/providers/azure
   SUB="<subscription name>"

   make bootstrap AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   make init      AZ_SUBSCRIPTION="$SUB"
   make plan      AZ_SUBSCRIPTION="$SUB" LOCATION=<region>
   make apply     AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make status    AZ_SUBSCRIPTION="$SUB"      # repeat until "provisioned: <time>"

Then run the **hubless self-test** on both FL backends. ``selftest-kit`` provisions a throwaway FL
kit on your machine, packs only the client's files and puts them in the kit drop; ``selftest``
has the node fetch that kit, bring the trust stack up with no hub, check it and tear it down:

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
- ``make stop`` / ``make start`` deallocate and start the VM (a stopped VM costs only its disks
  and the IP). The VM also shuts itself down at 19:00 UK time by default.
- ``make destroy`` removes the node and the kit drop and keeps the bootstrap layer;
  ``make destroy-bootstrap`` removes the rest and refuses while a node exists.

***************
Troubleshooting
***************

- **First boot fails.** ``make status`` shows cloud-init's state and ``make logs
  UNIT=cloud-final`` its output; the portal's boot diagnostics show the serial console. Fix the
  cause in the repo, push, then ``make reprovision REF=<new sha>``.
- **The node cannot fetch its kit.** It retries for about five minutes, then names the HTTP
  status: 403 right after an apply is the node's read role still propagating; 404 means the kit
  was never uploaded or has expired (kits last a day).
- **kit-upload is refused (403).** Your public IP has changed since ``plan``: plan and apply
  again (or pass ``OPERATOR_IP=<ip>``).
- **A self-test check fails.** The report names it. Fix the cause in the repo and reprovision;
  do not edit the node by hand.
