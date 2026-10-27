.. _deploy-flip-node-aws-ec2:

#############################
Deploy a FLIP node on AWS EC2
#############################

A FLIP node on AWS EC2 runs the trust-side stack (trust-api, imaging-api, data-access-api, FL client,
XNAT, Orthanc, OMOP) on one EC2 instance, built from code in ``trust/deploy/terraform/aws/ec2`` and
configured by ``trust/deploy/ansible/aws-ec2.yml``. It can go in **any AWS account and VPC**, including
the site's own: it reads nothing from the hub (no remote state, no hub env file, no hub credentials).
Its only links to a hub are its **kit** and its **registration**, as for any trust. The instance has no
public IP and no inbound rule; it is reached through SSM Session Manager only. In one of the hub's
Landing Zone Accelerator accounts, outbound traffic leaves through the estate's central firewall, which
drops anything it has no rule for: the node can run there only once that firewall allows the hosts in
:ref:`deploy-trust-node-network` from the node's address.

For a node on a Trust's own host see :doc:`deploy-flip-node-on-prem`; on Azure,
:doc:`deploy-flip-node-azure`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The diagrams are generated when this documentation is built, by a script kept next to the Terraform
(``trust/deploy/terraform/aws/ec2/architecture/``). A test in that tree pins every drawn node to a resource
in the ``.tf`` files, and every load-bearing resource to a drawn node. Dashed boxes are optional: the own-VPC
network mode, the SSM endpoints, the stop/start schedule and the S3 kit read.

.. figure:: ../assets/generated/trust-node-aws-ec2-network.png
   :align: center
   :alt: In the site's AWS account, a VPC (the site's, or one this root creates) holds the node subnet with the
         EC2 trust node: a host running Docker, the SSM and CloudWatch agents, trust-api and the FL client, and an
         encrypted gp3 root volume, with no public IP and no inbound rule. Optional SSM interface endpoints sit in
         the subnet; a NAT gateway with an Elastic IP and an internet gateway exist only in the own-VPC mode.
         Regional services: the node IAM role, SSM Session Manager, CloudWatch Logs and an optional EventBridge
         stop/start schedule. An optional kit bucket and KMS key sit in the kit owner's account. Every arrow to
         the Central Hub, GHCR and Hugging Face leaves the node; the operator reaches it through SSM only.

   The node in its network, the AWS services it uses, and its outbound paths.

.. figure:: ../assets/generated/trust-node-aws-ec2-host.png
   :align: center
   :alt: On the node: the trust stack under Docker Compose (trust-api, the FL client, imaging-api,
         data-access-api, the OMOP database and Orthanc), XNAT as a swarm stack (xnat-web and its database), and
         the observability stack (Alloy, Loki, Grafana). trust-api polls the hub over HTTPS, the FL client
         connects to the hub's FL endpoint over mutual-TLS gRPC, and the host pulls images from GHCR and the
         seed data from Hugging Face.

   The services on the host, and what each connects out to.

The kit file stays on the operator's machine: ``make deploy`` evaluates the trust compose files there
and drives the node's Docker daemon over an SSH context, so the kit never has to exist on the instance.

********************
Network and security
********************

The root has two network modes:

.. list-table::
   :header-rows: 1
   :widths: 26 34 40

   * - Mode
     - Use it for
     - Outbound traffic
   * - **Existing subnet** (default; ``SUBNET_ID=<subnet-…>``)
     - A site's own account or VPC
     - The site's estate (inspection VPC, firewall or NAT). The root creates no VPC, internet gateway, NAT
       gateway or Elastic IP.
   * - **Own VPC** (no ``SUBNET_ID``)
     - A standalone or test install in an empty account
     - A NAT gateway created with the stack; its Elastic IP is destroyed with it.

The hub never checks a trust's IP address: it admits a trust by its API key and the kit's mTLS
certificates. So the node needs no fixed address, and no hub change is needed to add or remove it.

**Does the node expose an IP address?** No. The instance has no public IP and its security group no
inbound rule; even Session Manager is a connection the node's own agent opens. The one public address in
own-VPC mode is the NAT gateway's Elastic IP, which AWS requires for a public NAT gateway: it is the
*source* address of the node's outbound connections, accepts no unsolicited inbound traffic, and is
destroyed with the stack. In existing-subnet mode there is none: the node leaves through the site's own
egress, typically a central firewall or inspection VPC that holds the site's public addresses and
allowlists the destinations below.

**Egress the site must allow**: everything in :ref:`deploy-trust-node-network`, plus the AWS services
below. The node's security group allows only 443, 80, the FL port and the VPC DNS resolver; a site
firewall narrows that to these names.

.. list-table::
   :header-rows: 1
   :widths: 40 12 48

   * - Destination
     - Port
     - For
   * - ``awscli.amazonaws.com``
     - 443
     - the AWS CLI, installed once at provisioning
   * - AWS: SSM (``ssm``, ``ssmmessages``, ``ec2messages``), CloudWatch, and S3 in the node's region
     - 443
     - access, logs and metrics; the kit and vocabulary when they come from a bucket. On a subnet with
       no internet route to SSM, set ``SSM_ENDPOINTS=true`` for interface endpoints (~$24/month per AZ)

Neither row has been measured behind a firewall yet.

**What the instance role may do**: open Session Manager sessions (no ``ssm:GetParameter``, so it cannot
read parameters in its account), ship logs and metrics to CloudWatch, and, only when ``KIT_BUCKET`` is
given, read **its own slot's** FL kit and the OMOP vocabulary. IMDS takes v2 tokens with a hop limit of
1, so a container on the host, the FL client among them, cannot reach the role's credentials. Where
the account requires a permissions boundary on new roles, pass it as ``BOUNDARY_ARN``.

****
Cost
****

eu-west-2 on-demand list prices, 730 hours a month, approximate:

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - Item
     - per month
   * - ``t3.xlarge`` (the default ``INSTANCE_TYPE``)
     - ~$138
   * - ``t3.large`` / ``t3.medium``, for a mock trust whose workload fits
     - ~$69 / ~$34
   * - gp3 root volume, 100 GB
     - ~$9
   * - NAT gateway and its IP, own-VPC mode only
     - ~$40 + $0.05/GB
   * - SSM interface endpoints, only with ``SSM_ENDPOINTS=true``
     - ~$24 per AZ

A node on an existing subnet costs about $147 a month, one in its own VPC about $187. The instance
dominates. For mock and test trusts only, ``SCHEDULE=true`` stops the node outside working hours
(by default 08:00–18:00 Europe/London, weekdays): the hub shows the trust offline outside the window,
and no FL job may be scheduled then.

*************
Prerequisites
*************

- The trust registered on the hub, and its kit file ``trust/.env.<CODE>.<env>`` from the hub admin
  (:doc:`onboarding-a-trust`). The node's slot and FL port come from it.
- AWS credentials for the node's account (``AWS_PROFILE``), an S3 bucket for the node's Terraform state,
  Terraform >= 1.13, the AWS CLI with the Session Manager plugin, ``uv`` and Docker.
- An SSH key pair, ``~/.ssh/host-aws`` by default (``SSH_KEY`` / ``PUBLIC_KEY_FILE``). It is used only
  through SSM.

*************************
Build the node and join
*************************

.. code-block:: bash

   cd trust/deploy/terraform/aws/ec2
   KIT=<CODE>; HUB=<stag|prod>

   make init      KIT=$KIT HUB=$HUB STATE_BUCKET=<your state bucket>
   make plan      KIT=$KIT HUB=$HUB SUBNET_ID=<subnet-…>   # leave SUBNET_ID out for its own VPC
   make apply     KIT=$KIT HUB=$HUB                        # shows the plan and the account, asks for yes
   make ssh-config KIT=$KIT HUB=$HUB                       # ~/.ssh/config entry, Host flip-<env>-<code>
   make provision KIT=$KIT HUB=$HUB                        # aws-ec2.yml: packages, Docker, dirs, agent
   make stage-kit KIT=$KIT HUB=$HUB KIT_SRC=<slot kit dir> # copy the FL kit from the hub admin onto the node
   make deploy    KIT=$KIT HUB=$HUB                        # networks, seed with the stack down, start

The FL kit arrives out of band with the kit file. ``KIT_SRC`` is the slot's own kit, as the Kubernetes
chart takes it: ``local/``, ``startup/`` and ``transfer/`` for FLARE, ``certificates/`` and ``keys/`` for
Flower. ``stage-kit`` refuses a FLARE kit that names another slot. Without a ``KIT_BUCKET`` the seed
loads no OMOP vocabulary, so cohort queries use the vocabulary-free form. Check the node joined from the hub's *Trusts* page (the trust is
online) and from the trust: ``make shell`` and ``curl -s localhost:<TRUST_API_PORT>/health`` reports
``hub_key_match: true``.

Before ``deploy``:

- **Host paths in the kit must be absolute or unset.** A kit scaffolded by ``make new-trust`` carries
  relative ones (``ORTHANC_STORAGE_DIR=./orthanc/…``, ``BASE_IMAGES_DOWNLOAD_DIR``, ``XNAT_DATA_DIR``); over
  the ssh docker context they would resolve to your checkout, so ``deploy`` refuses them. Comment them out:
  each defaults under ``/opt/flip``.
- **A trust on slot 3 or above** needs ``SOURCE_TRUST=1`` (or 2): the mock dataset has two partitions,
  and the seed defaults to the slot number.
- **Credentials from** ``aws login`` are not read by Terraform. Export them first, and again when they
  expire (about every 15 minutes):
  ``eval "$(aws configure export-credentials --profile <profile> --format env)"``.
- **An AWS Free plan account** launches only Free-Tier-eligible instance types: ``t3.xlarge`` fails at
  ``apply``, after the network is created. ``INSTANCE_TYPE=m7i-flex.large`` (2 vCPU, 8 GiB) runs the x-ray
  tutorial end to end (checked on 21 Oct 2026, cohort to results, no memory pressure).

******************
Operate and remove
******************

- ``make upgrade KIT=… HUB=… TAG=vX.Y.Z`` moves the node to a release with its data in place (see
  :doc:`/sys-admin/admin-upgrading-sites`). ``make deploy`` is the first-install verb: never use it on a
  live node.
- ``make stage-kit`` re-stages the FL kit after a re-registration; ``make seed`` re-seeds after a data
  version bump (the stack must be stopped).
- ``make destroy KIT=… HUB=…`` removes the node and, in own-VPC mode, its network. Deregister the trust on
  the hub first.

*************
Not yet here
*************

- **The node self-test before join** (``selftest_node.sh`` over SSM, as the Azure node runs it through Run
  Command) and **the running-stack check after join** (FLIP#1449) are not wired to this root yet.
  ``make -C deploy/providers/AWS status`` checks only the hub's own trust EC2.
- **GPU hosts**: ``flip_gpu_host`` holds the NVIDIA driver and checks a container sees the GPU (it does
  nothing on a CPU host), but ``make deploy`` still starts the FL client CPU-only. Untested until the
  account has G-instance quota.
