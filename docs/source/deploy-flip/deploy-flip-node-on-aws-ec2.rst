.. _deploy-flip-node-on-aws-ec2:

###############################
Deploy a FLIP node on AWS EC2
###############################

The Central Hub's Terraform can also provision one **cloud trust host**: an EC2
instance in the hub's VPC that runs the same trust-side Docker Compose stack an
on-prem node runs (trust-api, imaging-api, data-access-api, FL client, XNAT,
Orthanc, OMOP). It is the quickest way to get a working trust next to a new hub,
and — on a GPU instance — the way to exercise GPU training without on-prem
hardware.

.. important::

   The EC2 trust is a **test and demonstration trust**. Its OMOP database and
   Orthanc PACS are bundled mocks, seeded from the open datasets published for
   the tutorials; it is not wired to any hospital system. A real trust runs where
   its data lives — see :doc:`deploy-flip-node-on-prem` or
   :doc:`deploy-flip-node-in-tre`.

.. contents:: On this page
   :local:
   :depth: 2

************
Architecture
************

- The host sits in a **private subnet** of the hub's VPC with no public IP and a
  security group that has **no ingress rules**. Egress is limited to HTTPS/HTTP,
  S3, DNS, the VPC endpoints and the hub's FL server port.
- Operators reach it only through **AWS SSM Session Manager**. SSH (for Ansible
  and the remote Docker context) is tunnelled through SSM, never over port 22.
- Like every FLIP node, it polls the hub over HTTPS; the hub never connects in.
- Terraform creates it when ``DEPLOY_TRUST_EC2=true`` (the default).
  ``make full-deploy-hub-only`` sets it ``false`` and creates no trust host.

*****************
Choose the host
*****************

The host shape is three settings in the hub's env file (``.env.<env>``) or on the
``make`` command line. Leaving them unset gives the CPU-only default.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Setting
     - CPU default
     - GPU example
   * - ``TRUST_INSTANCE_TYPE``
     - ``t3.xlarge``
     - ``g4dn.xlarge`` (T4, 16 GB) or ``g5.xlarge`` (A10G, 24 GB)
   * - ``TRUST_AMI_SSM_PARAMETER``
     - Canonical Ubuntu 24.04
     - ``/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id``
   * - ``TRUST_ROOT_VOLUME_SIZE``
     - ``100`` (GiB)
     - ``150`` — the CUDA builds of the FL images are larger

For a GPU host, use AWS's *Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu
24.04)*: it ships the NVIDIA driver and the NVIDIA Container Toolkit, so nothing
has to be installed on the host. The FL images carry CUDA 13 builds of PyTorch and
need **NVIDIA driver 580 or newer**. Current releases of that AMI ship the 595
branch; its 2025 releases shipped 570, so check ``nvidia-smi`` on any host launched
from a pinned or older image.

Before choosing a GPU type, check the account's **Running On-Demand G and VT
instances** vCPU quota in the region — new accounts often start at 0, and the
apply then fails with ``VcpuLimitExceeded``.

.. warning::

   Changing the instance type or the AMI **replaces** the host; its data volumes
   are not preserved. Resizing the root volume grows it in place.

*************
Prerequisites
*************

- A deployed Central Hub (:doc:`deploy-central-hub-aws`) and the operator
  workstation set up for it: AWS SSO profile, the Session Manager plugin, and
  ``uv sync`` in ``deploy/providers/AWS/`` (which installs Ansible).
- The hub's SSH key pair at ``~/.ssh/host-aws`` / ``~/.ssh/host-aws.pub``. The
  trust host is launched with that key pair, and Ansible connects with it.
- An env file for the environment that matches what is deployed. On a hub applied
  by CI, rebuild it from deployed state rather than trusting a local copy:
  ``deploy/providers/AWS/scripts/reconcile_ci_env.py --env stag --profile stag --out .env.stag``.

****************
Deploy the node
****************

All commands run from ``deploy/providers/AWS/`` with ``PROD`` set to the
environment.

1. **Pause FL.** Applying Terraform can restart the hub's FL services, which kills
   any training run in flight. Turn on *Deployment Mode* as an admin and wait
   until ``GET /api/fl/quiesce`` reports it on with no ``BUSY`` net.

2. **Create the host.** Review the plan before applying it:

   .. code-block:: bash

      make plan PROD=stag DEPLOY_TRUST_EC2=true \
        TRUST_INSTANCE_TYPE=g4dn.xlarge \
        TRUST_AMI_SSM_PARAMETER=/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id \
        TRUST_ROOT_VOLUME_SIZE=150
      make apply PROD=stag   # same settings

   On a hub whose Terraform is applied by CI, a later CI apply uses the GitHub
   environment's values — set ``DEPLOY_TRUST_EC2`` and the ``TRUST_*`` keys there
   too, or that apply will remove the host again.

3. **Provision it.** ``make update-env ssh-config PROD=stag`` writes the SSM-tunnelled
   ``flip-trust`` SSH alias; ``make ansible-init PROD=stag`` then installs Docker
   (skipped when the image already has the engine and compose plugin), the AWS CLI and the CloudWatch agent and creates the trust's directories. On a
   GPU host the play also holds the NVIDIA driver and kernel packages at the
   versions the AMI shipped, re-registers the NVIDIA runtime with Docker, and
   **fails** unless a container can see the GPU — the check that the
   training client will not silently fall back to CPU.

4. **Register the trust.** Scaffold its kit from the repo root and register it with
   the hub (this writes ``trust/.env.<CODE>.<env>``):

   .. code-block:: bash

      make new-trust TRUST_CODE=<CODE> TRUST_NAME="..." PROD=stag   # repo root
      make register-trusts KIT=<CODE> PROD=stag                     # deploy/providers/AWS

   Leave ``ORTHANC_STORAGE_DIR`` unset in the kit: the host's default
   (``/opt/flip/orthanc/orthanc-storage``) is the one that is seeded.

5. **Opt into the GPU** (GPU hosts only) by adding ``TRUST_EC2_NUM_GPUS=1`` to the
   kit. The deploy then adds the GPU overlay and sets ``NUM_AVAILABLE_GPUS`` for
   the FL client. It is a separate key on purpose: the kit template's
   ``NUM_AVAILABLE_GPUS=1`` does not move a CPU host onto a GPU it does not have.

6. **Deploy.**

   .. code-block:: bash

      make deploy-trust KIT=<CODE> PROD=stag

   This stages the trust's FL kit, seeds OMOP and Orthanc at the data version
   pinned in ``trust/.data_version``, and starts the stack on the host with
   ``--pull always`` through a Docker context over the SSM tunnel.

7. **Unpause FL.** Turn Deployment Mode off.

*******
Verify
*******

- The hub lists the trust as online (the admin *Trusts* page, or ``GET /api/trust``).
- On a GPU host, over SSM:

  .. code-block:: bash

     nvidia-smi                                   # driver >= 580
     docker exec $(docker ps -qf name=fl-client-net-1) nvidia-smi   # the FL client sees the GPU

- Run the end-to-end smoke test against the hub, limited to this trust, as
  described in ``flip-api/AGENTS.md`` ("Smoking a REMOTE hub": pass the hub URL and an admin token):
  ``make e2e_smoke PROD=stag EXTRA_ARGS="--trusts <CODE>"``. During training
  ``nvidia-smi`` on the host shows the FL client's process.

*********
Teardown
*********

Set ``DEPLOY_TRUST_EC2=false`` and plan/apply again (pausing FL first, as above).
The host and its volumes are destroyed; the trust's hub registration stays until
removed by an admin.

***************
Troubleshooting
***************

- **Imaging pull fails with ``QueueFailed`` and Orthanc has 0 studies** — the
  kit sets a workstation-relative ``ORTHANC_STORAGE_DIR``; remove it and redeploy.
- **Every task fails with ``Invalid payload: failed authentication``** — the kit's
  ``AES_KEY_BASE64`` no longer matches the hub's. Reconcile the env file from
  deployed state, run ``make sync-trust-kit KIT=<CODE> PROD=<env>`` from the repo
  root, and redeploy the trust.
- **FL client exits with ``num_of_gpus specified (1) exceeds available GPUs: 0``** —
  ``TRUST_EC2_NUM_GPUS`` is set on a host without a working GPU; re-run
  ``make ansible-init`` to see the GPU check fail, or unset it for a CPU host.
- **``could not select device driver "nvidia"``** — Docker has no NVIDIA runtime
  registered; ``make ansible-init`` re-registers it.
