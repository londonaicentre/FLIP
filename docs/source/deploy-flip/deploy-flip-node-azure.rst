.. _deploy-flip-node-azure:

#################################
Deploy a FLIP node on an Azure VM
#################################

A FLIP node on Azure is an on-prem node whose host is an Azure VM. It runs the same trust-side
stack (trust-api, imaging-api, data-access-api, FL client, XNAT, Orthanc) and the same on-prem
commands, and is built entirely from code in ``trust/deploy/terraform/azure``.

- The VM configures itself at first boot.
- It is operated only through Azure Run Command: there are no inbound ports and no SSH from outside.
- It receives its kit the way any trust does, from the hub admin, through a private container it
  reads with its own identity.

For a node on a Trust's own host, see :doc:`deploy-flip-node-on-prem`. To run the same trust as the
``flip-trust`` Helm chart on a one-node AKS cluster instead, see :doc:`deploy-flip-node-azure-aks`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The diagram is drawn from the node's Terraform code, so it shows what ``make apply`` builds.
Dashed boxes exist only in some configurations: the node's own NAT gateway (no ``SUBNET_ID``),
the budget alert and the daily shutdown.

.. figure:: ../assets/generated/trust-node-azure-vm.png
   :align: center
   :alt: In the Azure subscription, the bootstrap layer holds the Terraform state storage account and an
         optional budget alert. The node resource group holds the VNet and node subnet with a NIC behind a
         network security group that denies all inbound traffic, and the Ubuntu VM with its managed identity, a
         data disk for /opt/flip, trust-api, the FL client and the other trust services. A NAT gateway with its
         own public IP exists only on a standalone node; an optional daily shutdown schedule stops the VM. The kit
         drop is a private blob container the VM reads with its managed identity. The operator reaches the VM
         only through Azure Run Command; every arrow to the Central Hub, GHCR, Hugging Face and GitHub leaves the
         node.

   The VM node, its kit drop and the bootstrap layer.

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

.. _deploy-flip-node-azure-network:

********************
Network and security
********************

This section is for the Trust's network and security teams.

**The node never accepts a connection.** Its NIC carries its own network security group, which
denies all inbound traffic, so a permissive site subnet cannot open it. The VM has no public IP,
and nothing listens for SSH from outside. Operators reach it only through Azure Run Command,
which travels over the VM agent's own outbound channel.

.. note::

   This holds while the PACS is the mock one on the node. A site PACS pushing studies into XNAT
   needs an inbound allow for ``XNAT_PORT`` (8104) from its address only, which FLIP does not
   open yet (`FLIP#1561 <https://github.com/londonaicentre/FLIP/issues/1561>`_).

**The hub identifies the node by its credentials, not by its address.** Hub API calls carry the
Trust's API key over HTTPS. FL traffic uses TLS, authenticated by the credentials in the Trust's
FL kit (mutual TLS on NVFLARE), so the node needs no fixed public IP. The hub's FL edge does,
however, admit only declared trust addresses: **before going live, give the FLIP team the public
address the node's traffic leaves from**, and tell them whenever it changes.

**Outbound traffic takes one of two paths.**

- **The Trust's own subnet** (``SUBNET_ID``): the recommended deployment. The node follows the
  Trust's routes, normally through its firewall, and FLIP creates no network, no NAT gateway and
  no public IP. The subnet needs the ``Microsoft.Storage`` service endpoint for the kit drop.
- **A standalone node** (no ``SUBNET_ID``): for trials. FLIP creates a VNet whose subnet leaves
  through a NAT gateway. The gateway's IP is created and destroyed with the node, and no address
  outlives it.

**What the Trust's firewall must allow, outbound only.** Everything in
:ref:`deploy-trust-node-network`, plus two first-boot extras on the VM:

- ``github.com``: first boot clones FLIP at a pinned commit.
- ``astral.sh`` and ``releases.astral.sh``: first boot installs uv.

**What leaves the site.** Model updates during training, and aggregate results. Patient records,
images and OMOP rows stay on the node.

**Where the secrets live.** The Trust's API key and its trust-internal service key are in its
kit file, installed owner-only on the node; the hub never sees the trust-internal key. The kit
reaches the node through the kit drop, a storage account with shared keys off, a firewall that
admits only the node's subnet and the operator's IP, and a rule that deletes every kit within
about 48 hours. The node reads it with its own managed identity.

.. _deploy-flip-node-azure-prerequisites:

*************
Prerequisites
*************

- An Azure subscription you may create resources in, and its **name**. Every command takes it
  as ``AZ_SUBSCRIPTION``; nothing uses the Azure CLI's default subscription.
- A region with capacity for ``Standard_D4s_v5`` (4 vCPU). Free Trial subscriptions often
  cannot create 4-vCPU VMs in the UK regions; check with
  ``az vm list-skus --subscription "<name>" --location <region> --size Standard_D4 --all -o table``.
- On your machine:

  - Azure CLI, logged in with ``az login``
  - Terraform 1.13 or newer and GNU make
  - ``uv`` and ``yq`` (the self-test kit is provisioned on your machine, never on the node)
  - an SSH public key at ``~/.ssh/id_ed25519.pub``, or pass ``SSH_PUBLIC_KEY_FILE=<path>`` to
    ``plan`` (``destroy`` needs no key). Azure requires a key, but nothing listens for SSH, so a
    throwaway key will do.
- The FLIP commit you deploy pushed to GitHub: the VM clones it.

**************************
Build the node and test it
**************************

.. code-block:: bash

   cd trust/deploy/terraform/azure
   SUB="<subscription name>"
   REGION=<region>   # the node's region
   SUBNET=<subnet resource id>   # the site's subnet; leave empty for a standalone node (see Network and security)

   make bootstrap AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   cd vm
   make init      AZ_SUBSCRIPTION="$SUB"
   make plan      AZ_SUBSCRIPTION="$SUB" LOCATION=$REGION SUBNET_ID=$SUBNET
   make apply     AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make status    AZ_SUBSCRIPTION="$SUB"      # repeat until "provisioned: <time>"

The node's commands run in ``vm/``. Only ``bootstrap`` and ``destroy-bootstrap`` run in
``trust/deploy/terraform/azure``, because the AKS cluster shares that layer.

``init`` can be refused with a 403 for a minute or two after ``bootstrap``, while Azure applies
your access to the new state storage; run it again. First boot takes a few minutes.

The VM is built from Canonical's latest Ubuntu 24.04 image (``image_version = "latest"``). To record
the exact build a node came from, run
``az vm show -g <resource group> -n <vm name> --query storageProfile.imageReference.exactVersion -o tsv``.
Setting ``image_version`` to that value rebuilds on the same image, and replaces the VM.

A **second node in the same subscription** needs its own name and its own checkout: give every
command the same ``NAME_PREFIX=<prefix>`` (default ``flipaz``), and run them from a separate clone.
The bootstrap keeps its Terraform state in the checkout, so a different prefix in the same checkout
would rename, and so replace, the first node's bootstrap.

Then run the **hubless self-test**, once per FL backend: ``FL_BACKEND=nvflare`` as below, then
again with ``FL_BACKEND=flower``.

- ``selftest-kit`` provisions a throwaway FL kit on your machine, packs only the client's files and
  puts them in the kit drop. It can run while the node is still booting.
- ``selftest`` has the node fetch that kit, bring the trust stack up with no hub, check it and tear
  it down.

.. code-block:: bash

   make selftest-kit AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
   make selftest     AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare
   make logs         AZ_SUBSCRIPTION="$SUB" UNIT=flip-selftest-nvflare
   make report       AZ_SUBSCRIPTION="$SUB" FL_BACKEND=nvflare

The report has twelve checks:

- the delivered kit, the kit staged for the FL client, and the kit file
- the stack starting
- the three API health endpoints
- the OMOP and Orthanc seed markers
- Orthanc refusing anonymous requests
- a real DICOM C-STORE from Orthanc into XNAT
- the FL client staying up

An unreachable hub is expected at this stage, not a failure.

On a new node the first self-test takes about 20 minutes, most of it loading the mock OMOP and
Orthanc data; later runs reuse that data and take about 10. ``make report`` shows the last
*finished* run, so check its start time to confirm it is the run you started. On a new node it
shows nothing until the first run ends.

.. note::

   The node runs one Run Command at a time. Let each ``make`` call return before starting the
   next; a second one is refused with
   ``Conflict: Run command extension execution is in progress``.

The reports stay on the node, under ``/opt/flip/selftest/`` (``selftest-<backend>-<time>.md`` and
``.json``), and ``make destroy`` deletes them: keep a copy of each ``make report`` first.

.. _deploy-flip-node-azure-join:

**********
Join a hub
**********

The hub admin, who holds the hub's AWS credentials and env file, runs the same steps as for any
on-prem trust, from the root of a FLIP checkout:

.. code-block:: bash

   make new-trust TRUST_CODE=<CODE> TRUST_NAME="<name>" HUB=<env>
   make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<env>
   make sync-trust-kit KIT=<CODE> HUB=<env>
   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE> HUB=<env>

.. note::

   A test trust seeded with the published mock data on slot ``Trust_3`` or above also needs
   ``SOURCE_TRUST=1`` (or ``2``) in its kit: the mock data has two partitions, and the slot number
   is only the default choice of partition.

The hub admin hands the tarball to the node operator. From ``trust/deploy/terraform/azure/vm``, the
operator uploads it to the kit drop and installs it with ``join``:

.. code-block:: bash

   make kit-upload AZ_SUBSCRIPTION="$SUB" TARBALL=flip-trust-kit-<slot>-<date>.tar.gz
   make join       AZ_SUBSCRIPTION="$SUB" NAME=flip-trust-kit-<slot>-<date>.tar.gz KIT=<CODE> HUB=<env>
   make logs       AZ_SUBSCRIPTION="$SUB" UNIT=flip-join-<CODE>

``join`` fetches the kit with the node's identity, installs its kit file (owner-only) with the
node's own settings appended, stages the FL kit for the client that reads it and runs
``up-onprem-trust``, whose readiness checklist must pass before anything starts. The node's FL
client dials the hub's FL edge outbound. The edge admits only declared trust addresses, so the FLIP
team must have added the node's egress address first (see :ref:`deploy-flip-node-azure-network`).

******************
Operate and remove
******************

Run these from ``vm/`` with ``AZ_SUBSCRIPTION="$SUB"``, like every node command.

- ``make reprovision REF=<sha or vX.Y.Z>`` moves the node's code without rebuilding it.
- ``make stop`` / ``make start`` deallocate and start the VM (a stopped VM costs only its disks,
  plus the NAT gateway on a standalone node). The VM also shuts itself down at 19:00 UK time by default.
- ``make destroy`` (with the same ``SUBNET_ID``) removes the node, its network and the kit drop,
  and keeps the bootstrap layer. It also deletes the self-test reports on the node.
- ``make destroy-bootstrap``, run in ``trust/deploy/terraform/azure``, removes the bootstrap layer.
  It refuses while a node or an AKS cluster exists.

***************
Troubleshooting
***************

- **First boot fails.** ``make status`` shows cloud-init's state and ``make logs
  UNIT=cloud-final`` its output; the portal's boot diagnostics show the serial console. Fix the
  cause in the repo, push, then ``make reprovision REF=<new sha>``.
- **The node cannot fetch its kit.** It retries for about five minutes, then names the HTTP
  status:

  - **403 right after an apply**: the node's read role is still propagating.
  - **403 that persists on a site subnet** (``SUBNET_ID``): the subnet lacks the
    ``Microsoft.Storage`` service endpoint the kit drop's firewall needs.
  - **404**: the kit was never uploaded or has expired (kits are deleted within about 48 hours).
- **kit-upload is refused (403).** Your public IP has changed since ``plan``: plan and apply
  again (or pass ``OPERATOR_IP=<ip>``).
- **A self-test check fails.** The report names it. Fix the cause in the repo and reprovision;
  do not edit the node by hand.
