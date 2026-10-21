.. _deploy-flip-node-aws-ec2:

#############################
Deploy a FLIP node on AWS EC2
#############################

A FLIP node on AWS EC2 runs the trust-side stack (trust-api, imaging-api, data-access-api, FL client,
XNAT, Orthanc, OMOP) on one EC2 instance, built from code in ``trust/deploy/terraform/aws-ec2`` and
configured by ``trust/deploy/ansible/aws-ec2.yml``. It can go in **any AWS account and VPC**, including
the site's own: it reads nothing from the hub (no remote state, no hub env file, no hub credentials).
Its only links to a hub are its **kit** and its **registration**, as for any trust. The instance has no
public IP and no inbound rule; it is reached through SSM Session Manager only.

For a node on a Trust's own host see :doc:`deploy-flip-node-on-prem`; on Azure,
:doc:`deploy-flip-node-azure`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

The diagrams are generated when this documentation is built, by a script kept next to the Terraform
(``trust/deploy/terraform/aws-ec2/architecture/``). A test in that tree pins every drawn node to a resource
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

**Egress the site must allow** (the node's security group allows these and nothing else):

- the hub URL on 443;
- the hub's FL endpoint on 8002 (prod) or 8003 (stag), the kit's ``FL_EDGE_PORT``. **Exempt it from TLS
  inspection**: the FL connection is mutual TLS, which a re-signing proxy breaks;
- the image registry (``ghcr.io``) on 443, and the Hugging Face dataset the seed downloads from;
- Ubuntu's apt mirrors on 80 and 443;
- AWS: SSM (``ssm``, ``ssmmessages``, ``ec2messages``), CloudWatch, and S3 if the kit and vocabulary come
  from a bucket. On a subnet with no internet route to SSM, set ``SSM_ENDPOINTS=true`` to create the
  interface endpoints (~$24/month per AZ). A subnet with no route to GHCR needs a registry mirror.

**What the instance role may do**: open Session Manager sessions (no ``ssm:GetParameter``, so it cannot
read parameters in its account), ship logs and metrics to CloudWatch, and, only when ``KIT_BUCKET`` is
given, read **its own slot's** FL kit and the OMOP vocabulary. IMDS takes v2 tokens with a hop limit of
1, so a container on the host, the FL client among them, cannot reach the role's credentials. On a
Landing Zone Accelerator estate pass the required permissions boundary as ``BOUNDARY_ARN``.

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

   cd trust/deploy/terraform/aws-ec2
   KIT=<CODE>; HUB=<stag|prod>

   make init      KIT=$KIT HUB=$HUB STATE_BUCKET=<your state bucket>
   make plan      KIT=$KIT HUB=$HUB SUBNET_ID=<subnet-…>   # leave SUBNET_ID out for its own VPC
   make apply     KIT=$KIT HUB=$HUB                        # shows the plan and the account, asks for yes
   make ssh-config KIT=$KIT HUB=$HUB                       # ~/.ssh/config entry, Host flip-<env>-<code>
   make provision KIT=$KIT HUB=$HUB                        # aws-ec2.yml: packages, Docker, dirs, agent
   make deploy    KIT=$KIT HUB=$HUB                        # networks, seed with the stack down, start

``make provision`` expects the FL kit delivered out of band unless ``KIT_BUCKET`` (and ``FL_KIT_DATE``)
name the bucket it is published in. Check the node joined from the hub's *Trusts* page (the trust is
online) and from the trust: ``make shell`` and ``curl -s localhost:<TRUST_API_PORT>/health`` reports
``hub_key_match: true``.

****************************************************
The hub-VPC preset (not on an LZA estate)
****************************************************

*Who: hub admin.* ``HUB_ENV_FILE`` places a node in the hub's own VPC, so a mock trust pays for no NAT
gateway of its own; the trust root itself still reads nothing from the hub.

.. warning::

   **It does not work on a Landing Zone Accelerator estate**, which is where every FLIP hub runs. The
   workload accounts there have no internet egress by design (VPC endpoints only), so a node in the hub's
   VPC can reach SSM and S3 but not the image registry, the seed dataset, or the hub's public URL and FL
   endpoint. Checked on stag on 21 Oct 2026. Run mock trusts off the estate: in an account with internet
   egress (own-VPC mode), on-prem, Kubernetes or Azure.

.. code-block:: bash

   make init plan apply KIT=$KIT HUB=stag HUB_ENV_FILE=../../../../.env.stag

The preset takes the hub's first app subnet (by its LZA ``Name`` tag, the subnet the hub's own trust EC2
uses), the kit bucket and its KMS key (``alias/flip-app-key``), the permissions boundary, and the hub's
state bucket (key ``trust-nodes/<NAME>.tfstate``). The node still reaches the hub through the public FL
endpoint and the hub URL, exactly as one in another account does.

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
