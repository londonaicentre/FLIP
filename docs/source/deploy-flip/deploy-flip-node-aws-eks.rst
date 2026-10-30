.. _deploy-flip-node-aws-eks:

################################
Deploy a FLIP node on Amazon EKS
################################

A FLIP node on Amazon EKS runs the trust-side stack as the ``flip-trust`` Helm chart on a small EKS
cluster, built from code in ``trust/deploy/terraform/aws/eks``. It is the AWS twin of the AKS cluster on
:doc:`deploy-flip-node-azure`: a cluster for the chart and nothing else. It can go in **any AWS account and
VPC**, including the site's own: it reads nothing from the hub (no remote state, no hub env file, no hub
credentials). Its only links to a hub are its **kit** and its **registration**, as for any trust. The
nodes have no public IP and accept no inbound connection; the operator reaches the cluster's API from one
admitted IP address.

For the same trust on one EC2 instance under Docker Compose see :doc:`deploy-flip-node-aws-ec2`; for the
chart itself, its values and known limits, :doc:`deploy-flip-node-kubernetes`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The diagrams are generated when this documentation is built, by a script kept next to the Terraform
(``trust/deploy/terraform/aws/eks/architecture/``). A test in that tree pins every drawn node to a resource
in the ``.tf`` files, and every resource to a drawn node. Dashed boxes exist only when the root creates its
own network.

.. figure:: ../assets/generated/trust-node-aws-eks-network.png
   :align: center
   :alt: In the site's AWS account, a VPC (one this root creates, or the site's) holds a private node subnet
         in one availability zone with the managed node group (two m7i-flex.large nodes, no public IP, IMDSv2
         with a hop limit of 1), the EBS gp3 volumes of the chart and the mount target of the EFS file system
         holding XNAT's archive, and a private control-plane subnet in a
         second zone with the cluster's private API endpoint. A public subnet with a NAT gateway and an
         internet gateway exists only in the created network. Amazon EKS runs the control plane, on standard
         support only, with a public API endpoint that admits the operator's address only. Regional services:
         the cluster, node, EBS CSI and EFS CSI roles, and CloudWatch Logs for the control plane's api, audit and
         authenticator logs. Every arrow to the Central Hub, GHCR and Hugging Face leaves the nodes.

   The cluster in its network, the AWS services it uses, and its outbound paths.

.. figure:: ../assets/generated/trust-node-aws-eks-workload.png
   :align: center
   :alt: On the managed node group: the EKS add-ons in kube-system (the VPC CNI with NetworkPolicy
         enforcement on, kube-proxy, CoreDNS, the Pod Identity agent, and the EBS and EFS CSI drivers, each with
         its own role through Pod Identity, provisioning the gp3 volumes and XNAT's archive on EFS), and the flip-trust chart in namespace
         flip-trust: trust-api, the FL client, imaging-api, data-access-api, the OMOP database, Orthanc, XNAT
         (xnat-web and its database), the observability stack, the seed job, the FL kit Secret, the
         ReadWriteOnce volume claims and the NetworkPolicies. trust-api polls the hub over HTTPS, the FL client
         connects to the hub's FL endpoint over mutual-TLS gRPC, the nodes pull images from GHCR and the seed
         job fetches the seed data from Hugging Face.

   The ``flip-trust`` chart on the cluster, and what each part connects out to.

********************
Network and security
********************

- **Nothing inbound.** The nodes sit in a private subnet with no public IP. Without ``SUBNET_IDS`` the
  root creates a VPC that leaves through its own NAT gateway, whose Elastic IP is destroyed with the
  stack. With ``SUBNET_IDS`` (two or more, in different zones) the cluster uses the site's subnets and
  routes, and the root creates no network. The hub admits a trust by its API key and the kit's mTLS
  certificates, never by IP, so no fixed address is needed.
- **The API endpoint admits the operator only.** The public endpoint takes the address ``make plan``
  looks up (``OPERATOR_IP=`` overrides it) and refuses ``0.0.0.0/0``; the nodes reach the API server
  through the private endpoint. After your IP changes, plan and apply again.
- **Nodes in one availability zone.** EKS needs its control-plane interfaces in two zones, so a second
  private subnet holds them, but the node group stays in the first: the chart's volumes are
  ``ReadWriteOnce`` EBS volumes, which cannot follow a pod to another zone.
- **IMDSv2 with a hop limit of 1.** A pod on the pod network, the FL client running researcher code among
  them, cannot reach the node role's credentials.
- **NetworkPolicies are enforced.** The chart's policies are its isolation between services; the VPC CNI
  add-on is installed with NetworkPolicy enforcement turned on.
- **The EBS CSI driver has its own role**, through EKS Pod Identity, rather than borrowing the node role.
  Its gp3 storage class is the cluster default, since the chart's claims name none.
- **XNAT's archive is on EFS.** XNAT's Container Service runs each DICOM-to-NIfTI conversion as its own
  pod with xnat-web's volume mounted, on whichever node it lands, so that one volume must be mountable
  from every node. ``join`` creates the ``xnat-efs`` class (``make storage-class``): an access point per
  claim, owned by XNAT's uid 1001. The file system is encrypted, and only the cluster security group
  reaches it, on NFS. imaging-api and the FL client share the other two-pod volume and are kept on one
  node instead.
- **Standard support only.** The cluster's upgrade policy is ``STANDARD``: a version in extended support
  costs six times as much per hour, and nothing else would say so.
- **Control-plane logs** (``api``, ``audit``, ``authenticator``) go to CloudWatch Logs.
- **Egress** is the list on :doc:`deploy-flip-node-aws-ec2` (hub, FL endpoint exempt from TLS inspection,
  GHCR, Hugging Face, PyPI), plus the chart's own (*Extra network egress* on
  :doc:`deploy-flip-node-kubernetes`, and XNAT's plugin downloads, listed for AKS on
  :doc:`deploy-flip-node-azure`), plus the AWS endpoints EKS nodes need (EKS, ECR for the add-on images,
  STS, EC2).

Where the account requires a permissions boundary on new roles, pass it as ``BOUNDARY_ARN``.

****
Cost
****

eu-west-2 on-demand list prices, approximate:

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - Item
     - per month
   * - EKS control plane, standard support ($0.10 an hour)
     - ~$73
   * - Two ``m7i-flex.large`` nodes (the defaults ``INSTANCE_TYPE`` and ``NODE_COUNT``)
     - ~$165
   * - Node root volumes, 2 × 60 GB gp3, and the chart's volumes
     - ~$10 + the volumes' size
   * - EFS for XNAT's archive ($0.33/GB)
     - a few cents for a test trust
   * - NAT gateway and its IP, created network only
     - ~$40 + $0.05/GB

The cluster costs about $0.40 an hour while it exists; it has no stop schedule, so destroy a test cluster
when it is not in use.

*************
Prerequisites
*************

- The trust registered on the hub and its kit tarball from the hub admin (:doc:`onboarding-a-trust`).
- An AWS account with internet egress. The hub's own Landing Zone Accelerator accounts have none by
  design, so the node cannot run there.
- AWS credentials for that account (``AWS_PROFILE``) and an S3 bucket for the cluster's Terraform state.
- Terraform >= 1.13, the AWS CLI, ``kubectl``, Helm 3.16 or newer and ``python3``.

*********************
Build the cluster
*********************

On the hub admin's side, as for any on-prem trust:

.. code-block:: bash

   make -C deploy/providers/AWS register-trusts KIT=<CODE> HUB=<env>
   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE> HUB=<env>

On the site's side:

.. code-block:: bash

   cd trust/deploy/terraform/aws/eks
   KIT=<CODE>; HUB=<stag|prod>; REGION=eu-west-2

   make init        KIT=$KIT HUB=$HUB REGION=$REGION STATE_BUCKET=<your state bucket>
   make plan        KIT=$KIT HUB=$HUB REGION=$REGION   # SUBNET_IDS=<subnet-a>,<subnet-b> for the site's network
   make apply       KIT=$KIT HUB=$HUB REGION=$REGION   # shows the plan and the account, asks for yes
   make credentials KIT=$KIT HUB=$HUB REGION=$REGION   # kube context flip-<env>-<code>

*************
Join the hub
*************

.. code-block:: bash

   make join     TARBALL=flip-trust-kit-<slot>-<date>.tar.gz KIT=$KIT HUB=$HUB   # MOCK_PACS=1 on a test node
   make selftest KIT=$KIT HUB=$HUB
   make status   KIT=$KIT HUB=$HUB

``join`` does what ``aks-join`` does on Azure: it unpacks the tarball under ``build/kits/``, installs the
kit file as ``trust/.env.<CODE>.<env>``, puts the FL kit in a Secret (a managed node can be replaced at
any time; a Flower Secret holds only ``ca.crt`` and the slot's own credential), and deploys the chart with
``values-eks.yaml`` and ``values-small-node.yaml``, recording them in the kit file as
``K8S_PLATFORM_VALUES``. The first install takes up to half an hour, most of it XNAT's first start.
``MOCK_PACS=1`` seeds the published mock studies into the chart's Orthanc, which a test node needs for an
imaging pull and for the self-test.

The chart loads no OMOP core vocabulary: it is licensed, and the hub's copy sits in a bucket the site's
account cannot read. Load your own as :ref:`onprem-core-vocabulary` describes; until then, test with a
cohort query that does not name concepts through ``omop.concept``.

``selftest`` writes its report under ``build/selftest/`` and needs no hub. The trust has joined when the
hub's *Trusts* page shows it online, or when its heartbeat is answered:

.. code-block:: bash

   kubectl --context flip-<env>-<code> -n flip-trust logs deploy/trust-release-flip-trust-trust-api | grep heartbeat   # 200 OK

``make deploy KIT=… HUB=…`` redeploys or upgrades from the installed kit.

******
Remove
******

.. code-block:: bash

   make undeploy KIT=$KIT HUB=$HUB                  # the release, its namespace and its volumes (asks first)
   make destroy  KIT=$KIT HUB=$HUB REGION=$REGION   # cluster, roles, EFS and the created network

The chart's volumes are made by the EBS CSI driver, not by Terraform, so they would outlive the cluster
and keep billing: ``undeploy`` deletes the namespace and with it their claims, and ``destroy`` refuses
while any remain. Then deregister the trust on the hub.

****
Tips
****

- **Credentials from** ``aws login`` reach Terraform through a ``credential_process`` profile, which
  refreshes them itself:

  .. code-block:: ini

     [profile flip-eks]
     credential_process = aws configure export-credentials --profile <your aws login profile> --format process
     region = eu-west-2

  then ``AWS_PROFILE=flip-eks make plan …``.
- **A trust on slot 3 or above** needs a seed partition the mock dataset has (it has two): give ``join`` an
  extra overlay setting ``trustData.seed.sourceTrust: "1"``, by appending its path to ``EKS_VALUES``.
- **An AWS Free plan account** launches only Free-Tier-eligible instance types. ``m7i-flex.large``, the
  default, is the largest of them; two hold the trust stack with ``values-small-node.yaml``.
