..  Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.

.. _deploy-flip-node-azure-aks:

###############################
Deploy a FLIP node on Azure AKS
###############################

A FLIP node on AKS runs the trust-side stack as the ``flip-trust`` Helm chart on a one-node AKS cluster
(``Standard_D4s_v5``, Free control-plane tier). It shares its bootstrap layer (Terraform state and budget alert) with
the Azure VM, and you sign in to it through Microsoft Entra ID. Unless a step says otherwise, run the commands on this
page in ``trust/deploy/terraform/azure/aks``.

For the same trust on one Azure VM under Docker Compose see :doc:`deploy-flip-node-azure`; for the chart itself, its
values and known limits, :doc:`deploy-flip-node-kubernetes`. Its AWS twin is :doc:`deploy-flip-node-aws-eks`.

.. contents:: On this page
   :local:
   :depth: 1

************
Architecture
************

.. figure:: ../assets/generated/trust-node-azure-aks.png
   :align: center
   :alt: In the Azure subscription, the bootstrap state storage and the AKS resource group. In the node
         subnet, the AKS cluster (Free tier, Entra ID and Azure RBAC, Cilium network policies) with an API server
         that admits only the operator's IP and the nodes' egress IPs, a cluster identity scoped to the subnet,
         and one system node pool running the flip-trust chart's pods: trust-api, the FL client and the other
         trust services. A NAT gateway with its own public IP exists only on a standalone cluster. The operator
         uses kubectl and Helm through Entra ID; every arrow to the Central Hub, GHCR and Hugging Face leaves the
         cluster.

   The one-node AKS cluster running the ``flip-trust`` chart.

********************
Network and security
********************

Outbound traffic takes one of the same two paths as on the VM (:ref:`deploy-flip-node-azure-network`):

- **On the Trust's subnet** (``SUBNET_ID`` set): the cluster uses the Trust's routes and creates no outbound IP.
  The subnet must have a route table that sends outbound traffic to the Trust's firewall. AKS checks this before it
  creates the cluster.
- **Standalone** (no ``SUBNET_ID``): traffic leaves through the cluster's own NAT gateway.

The API server admits only your IP and the nodes' own egress IPs. A node that cannot reach its API server never joins
the cluster, so:

- standalone, the NAT gateway's IP is admitted for you;
- on the Trust's subnet, pass the firewall's public IPs to ``plan`` as ``NODE_EGRESS_IPS=<ip>[,<ip>]``
  (``destroy`` needs no addresses).

Behind the Trust's firewall the cluster needs, on top of :ref:`deploy-trust-node-network`:

- **AKS itself:** the outbound rules Microsoft documents for AKS ("Outbound network and FQDN
  rules for AKS clusters"). On Azure Firewall that is the ``AzureKubernetesService`` FQDN tag,
  and the region's ``AzureCloud`` service tag on TCP/UDP 443, 1194 and 9000, plus NTP.
- **The Helm chart's own:** the sample-data seed job, listed in :doc:`deploy-flip-node-kubernetes`.

The AKS rules were measured on 17 October 2026 by running this chapter behind a deny-all Azure Firewall Basic.

*************
Prerequisites
*************

- The subscription, region and tools of the VM's :ref:`prerequisites <deploy-flip-node-azure-prerequisites>`.
- On your machine you also need ``kubectl``, ``kubelogin``, Helm 3.16 or newer, ``jq``,
  ``python3`` and ``uv``.
- A trial subscription has a 4-vCPU quota, and the VM uses the same quota. ``apply`` therefore refuses while the Azure
  VM node exists: run ``make destroy`` in ``trust/deploy/terraform/azure/vm`` first.
- A running cluster costs roughly $0.25 an hour, plus the NAT gateway on a standalone cluster.

*****************
Build the cluster
*****************

.. code-block:: bash

   cd trust/deploy/terraform/azure
   SUB="<subscription name>"

   make bootstrap   AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   cd aks
   make init        AZ_SUBSCRIPTION="$SUB"
   # Standalone cluster (own NAT gateway):
   make plan        AZ_SUBSCRIPTION="$SUB" LOCATION=<region>
   # Or, on the Trust's subnet behind its firewall:
   make plan        AZ_SUBSCRIPTION="$SUB" LOCATION=<region> SUBNET_ID=<subnet resource id> NODE_EGRESS_IPS=<firewall public IPs>
   make apply       AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make credentials AZ_SUBSCRIPTION="$SUB"      # kube context flipaz-aks, signed in through Entra

**********
Join a hub
**********

The hub admin registers the trust and packages its kit as for the VM (:ref:`deploy-flip-node-azure-join`). With
the tarball in hand:

.. code-block:: bash

   make join TARBALL=flip-trust-kit-<slot>-<date>.tar.gz KIT=<CODE> HUB=<env>   # MOCK_PACS=1 on a test node
   make selftest

``join``:

#. unpacks the tarball under ``aks/build/kits/`` and installs the kit file as ``trust/.env.<CODE>.<env>``;
#. generates the chart's ``values-secrets.yaml`` from the kit file;
#. stores the FL kit in a Kubernetes Secret, because a managed node can be replaced at any time. For Flower, the Secret
   holds only ``ca.crt`` and the slot's own SuperNode credential. The tarball's ``certificates/`` also contains the
   SuperLink's ``server.key``, which must never be placed on a trust;
#. deploys the chart with ``values-aks.yaml`` and ``values-small-node.yaml``, and records them in the kit file as
   ``K8S_PLATFORM_VALUES`` so that later deploys and upgrades of that slot keep them. The first install takes up to half
   an hour, most of it XNAT's first start. ``MOCK_PACS=1`` seeds the published mock studies into the chart's Orthanc,
   which a test node needs for an imaging pull and for the self-test's C-STORE and conversion checks (skipped without
   it).

The chart loads no OMOP core vocabulary: it is licensed, so each site loads its own copy, as
:ref:`onprem-core-vocabulary` describes. Until then, cohort queries that name concepts through
``omop.concept`` return nothing, so test with a query that does not.

``selftest`` runs the same checks as the VM self-test against the deployed Helm release and writes its report under
``build/selftest/`` at the repo root. It needs no hub.

The trust has joined the hub once its heartbeat is answered. Look for ``200 OK`` in the trust-api log:

.. code-block:: bash

   kubectl --context flipaz-aks -n flip-trust logs deploy/trust-release-flip-trust-trust-api | grep heartbeat   # 200 OK

******************
Operate and remove
******************

- ``make stop`` stops the cluster so its node stops billing. ``make start`` starts it again and admits your current IP
  to the API server.
- After your IP changes, ``make allow-ip`` admits the new one.
- ``make deploy KIT=<CODE> HUB=<env>`` redeploys or upgrades from the installed kit.
- ``make destroy`` (with the same ``SUBNET_ID``) removes the cluster and its network, and keeps the bootstrap layer.
- ``make destroy-bootstrap``, run in ``trust/deploy/terraform/azure``, removes the bootstrap layer. It refuses while the
  cluster or a VM exists.

***************
Troubleshooting
***************

- **kubectl times out or is refused.** Your IP has changed: ``make allow-ip``.
- **Pods stay Pending while the node is full of dcm2niix Jobs.** dcm2niix Jobs created before the chart capped
  conversions each request a full CPU; the chart now registers the converter without a CPU limit. ``make clear-jobs``
  lists every Container Service Job (label ``XNAT_PROJECT``) whose pod template sets a CPU limit and deletes them once you type ``yes``. Then relaunch
  dcm2niix on those scans from XNAT (see :ref:`ts-k8s-dcm2niix-jobs-cap`).
