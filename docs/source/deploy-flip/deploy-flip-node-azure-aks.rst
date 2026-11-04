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
(``Standard_D4s_v5``, Free control-plane tier), built from code in ``trust/deploy/terraform/azure`` by its ``aks``
root, on the same bootstrap layer as the Azure VM. Sign-in is through Entra.

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

The diagram is generated when this documentation is built, by the script that draws the VM's
(``trust/deploy/terraform/azure/architecture/``).

********************
Network and security
********************

Outbound traffic follows the same two paths as the VM (:ref:`deploy-flip-node-azure-network`): on the Trust's
subnet (``SUBNET_ID``) the cluster uses the Trust's routes and creates no outbound IP, and that subnet needs a route
table sending outbound traffic to the Trust's firewall, which AKS checks before it creates the cluster; without
``SUBNET_ID`` it leaves through its own NAT gateway. The API server admits only your IP and the nodes' own egress:
the NAT gateway's IP, or on the Trust's subnet the firewall's public IPs, which you pass as
``NODE_EGRESS_IPS=<ip>[,<ip>]`` to ``aks-plan`` (``aks-destroy`` needs no addresses). A node that cannot reach its
API server never joins the cluster.

Behind the Trust's firewall the cluster needs, on top of :ref:`deploy-trust-node-network`:

- **AKS itself:** the outbound rules Microsoft documents for AKS ("Outbound network and FQDN
  rules for AKS clusters"). On Azure Firewall that is the ``AzureKubernetesService`` FQDN tag,
  and the region's ``AzureCloud`` service tag on TCP/UDP 443, 1194 and 9000, plus NTP.
- **The Helm chart's own:** the sample-data seed job, listed in :doc:`deploy-flip-node-kubernetes`.

The AKS rules were measured on 17 Oct by running this chapter behind a deny-all Azure Firewall Basic.

*************
Prerequisites
*************

- The subscription, region and tools of the VM's :ref:`prerequisites <deploy-flip-node-azure-prerequisites>`.
- On your machine you also need ``kubectl``, ``kubelogin``, Helm 3.16 or newer, ``jq``,
  ``python3`` and ``uv``.
- The trial's 4 vCPUs are the VM's too, so ``aks-apply`` refuses while the VM exists: ``make destroy`` first. A
  running cluster costs roughly $0.25 an hour, plus the NAT gateway on a standalone cluster.

*****************
Build the cluster
*****************

.. code-block:: bash

   cd trust/deploy/terraform/azure
   SUB="<subscription name>"

   make bootstrap       AZ_SUBSCRIPTION="$SUB" STATE_SA=<unique storage name> ALERT_EMAIL=<you>   # once
   make aks-init        AZ_SUBSCRIPTION="$SUB"
   make aks-plan        AZ_SUBSCRIPTION="$SUB" LOCATION=<region> SUBNET_ID=<subnet resource id, or empty> NODE_EGRESS_IPS=<firewall IPs, with SUBNET_ID>
   make aks-apply       AZ_SUBSCRIPTION="$SUB"      # shows the plan and the subscription, asks for yes
   make aks-credentials AZ_SUBSCRIPTION="$SUB"      # kube context flipaz-aks, signed in through Entra

**********
Join a hub
**********

The hub admin registers the trust and packages its kit as for the VM (:ref:`deploy-flip-node-azure-join`). With
the tarball in hand:

.. code-block:: bash

   make aks-join TARBALL=flip-trust-kit-<slot>-<date>.tar.gz KIT=<CODE> HUB=<env>   # MOCK_PACS=1 on a test node
   make aks-selftest

``aks-join`` unpacks the tarball under ``build/kits/``, installs the kit file as
``trust/.env.<CODE>.<env>``, generates the chart's ``values-secrets.yaml`` from it and puts the FL
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

******************
Operate and remove
******************

- ``make aks-stop`` stops the node billing; ``make aks-start`` starts it and admits your current
  IP. After your IP changes, ``make aks-allow-ip`` admits the new one.
- ``make aks-deploy KIT=<CODE> HUB=<env>`` redeploys or upgrades from the installed kit.
- ``make aks-destroy`` (with the same ``SUBNET_ID``) removes the cluster and its network and keeps
  the bootstrap layer;
  ``make destroy-bootstrap`` refuses while the cluster or a VM exists.

***************
Troubleshooting
***************

- **kubectl times out or is refused.** Your IP has changed: ``make aks-allow-ip``.
- **Pods stay Pending while the node is full of dcm2niix Jobs.** Jobs created before the
  conversion cap still request a CPU each. ``make aks-clear-jobs`` selects the Jobs whose pod
  template carries a CPU limit at all (the capped registration sets none), lists them and deletes
  them once you type ``yes``. Then relaunch dcm2niix on those scans from XNAT
  (``trust/deploy/helm/TROUBLESHOOTING.md`` §2.3d).
