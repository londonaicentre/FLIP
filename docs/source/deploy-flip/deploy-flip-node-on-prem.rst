.. _deploy-flip-node-on-prem:

#########################################
Deploy on a Docker Compose host (on-prem)
#########################################

This page installs a trust node as a Docker Compose stack on an Ubuntu host the trust owns. It is step 5 of
:doc:`onboarding-a-trust`; a node inside a TRE installs the same way.

.. admonition:: Before you start
   :class: note

   Do steps 1 to 4 of :doc:`onboarding-a-trust` first: you need the kit tarball from the FLIP admin. When the stack
   is up, continue at step 6 there.

.. contents:: On this page
   :local:
   :depth: 1

*************
Prerequisites
*************

The requirements every trust node shares (trust data, Central Hub, network) are on :doc:`deploy-trust-node`. The
host itself needs:

- **Ubuntu 22.04 or newer**, physical or VM, with a user that has ``sudo`` rights (default ``ubuntu``).
- **Internet access**, to pull Docker images and packages.
- **Docker in swarm mode** (XNAT runs as a swarm stack). The readiness checklist tells you if it is missing; the
  one-off fix is ``sudo docker swarm init``.
- **A writable directory for FLIP**, default ``/opt/flip``, with the sub-directories the stack mounts. The
  :gh:`on-prem Ansible playbook <trust/deploy/ansible/onprem.yml>` creates them and shows the full list.
- **For GPU training or evaluation:** an NVIDIA GPU on driver **580 or newer** (check *Driver Version* in
  ``nvidia-smi``) and the
  `NVIDIA Container Toolkit <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>`_.
  The FL images bring their own CUDA 13 runtime, so the host needs no CUDA toolkit. The playbook installs neither
  the driver nor the toolkit. A CPU-only host (``NUM_AVAILABLE_GPUS=0`` in the kit) needs neither.

.. note::

   On the trust host, every command that touches Docker runs with ``sudo -E``. The playbook deliberately does not
   add the login user to the ``docker`` group, because membership is equivalent to root access. ``-E`` keeps your
   ``$HOME``, so root's Docker reuses your registry login for image pulls.

*******************************
Provision the host with Ansible
*******************************

*Who: whoever prepares the host.* Run this **on the trust host**, from the repo root of a FLIP checkout (it needs
`uv <https://docs.astral.sh/uv/guides/install-python/>`_):

.. code-block:: shell

   read -rsp 'Sudo password: ' ANSIBLE_BECOME_PASS && export ANSIBLE_BECOME_PASS
   make -C deploy/providers/AWS provision-local-trust

It installs Docker and the base packages, creates the directories under ``/opt/flip``, pre-creates the FL kit tree
under ``/opt/flip/fl-kit`` and copies the logging stack's config into place.

.. note::

   The target also reads the hub env file and downloads the FL participant kit from S3, so as shipped it suits a
   host the FLIP team provisions. A trust operator without AWS access prepares the host to the prerequisites above
   and gets the FL kit in the tarball instead. The :gh:`Ansible README <trust/deploy/ansible/README.md>` has the
   details, including the FL slot the playbook pre-creates (``Trust_2`` by default).

******************************
Step 1 — Check out the release
******************************

*Who: trust operator, on the trust host*

Check out the release the hub runs; the admin tells you the version. Never use a branch tip: the compose files and
Makefiles that run the images come from this checkout.

.. code-block:: shell

   git fetch --tags origin && git checkout v<X.Y.Z>

***********************
Step 2 — Unpack the kit
***********************

*Who: trust operator, from the repo root*

.. code-block:: shell

   mkdir -p $HOME/flip-trust-kit
   tar -C $HOME/flip-trust-kit -xzf flip-trust-kit-<slot>-<date>.tar.gz
   cp $HOME/flip-trust-kit/.env.<slot>.prod trust/

The tarball holds the kit file ``.env.<slot>.prod`` (``.stag`` for the staging hub) and an ``fl-kit/`` folder. Keep ``fl-kit/`` exactly as it is: the
stack mounts that layout into the FL client.

***********************************
Step 3 — Edit the kit for your host
***********************************

*Who: trust operator*

In ``trust/.env.<slot>.prod``, change only the settings below. Leave the *Hub-shared* and *Kit credentials* blocks alone;
the admin manages them.

.. list-table::
   :header-rows: 1
   :widths: 30 55 15

   * - Setting
     - What to set
     - Required?
   * - ``FL_KIT_DIR``
     - Path to the extracted ``fl-kit/`` folder (the one that contains ``net-1/``).
     - Yes
   * - ``XNAT_PORT`` and ``XNAT_WEB_PORT``
     - XNAT's DICOM receiver port and its web UI port. Both are published on the host, so they **must differ**
       (shipped defaults: 8104 and 8105).
     - Yes
   * - Other host ports, e.g. ``OMOP_DB_PORT``, ``TRUST_API_PORT``, ``GRAFANA_PORT``
     - The stack publishes these on the host. Change any that clash with something already there.
     - If needed
   * - Storage and bind-mount directories
     - Point them at your host's disk layout.
     - If needed
   * - Trust-local passwords
     - Replace every default password in the *Trust-local credentials* section with a real secret.
     - Yes
   * - ``NUM_AVAILABLE_GPUS``
     - Number of GPUs for FL jobs; ``0`` on a CPU-only host.
     - If needed
   * - ``FL_SITE_PRIVACY_POLICY`` and its parameters
     - Your site's own privacy filter on FL updates (FLARE only). See :ref:`onprem-site-privacy-policy`.
     - Optional

.. note::

   **Upgrading a trust deployed before the XNAT port split.** A kit that sets only ``XNAT_PORT`` gives the DICOM
   receiver and the web UI the same number, so the next ``up-onprem-trust`` is **refused** with a message naming
   both values and the file to edit. Add ``XNAT_WEB_PORT`` (for example 8105) to the kit before your next deployment.

********************************************************
Step 4 — Run the readiness checklist and start the stack
********************************************************

*Who: trust operator, from the repo root*

.. code-block:: shell

   sudo -E make onboard-onprem-trust KIT=<slot>   # readiness checklist
   sudo -E make up-onprem-trust KIT=<slot>        # starts only if every check passes

Both default to the production hub (``HUB=prod``); add ``HUB=stag`` for staging. The checklist prints a ✅ or ❌ for
each precondition: the kit file, Docker swarm, the hub-shared settings and credentials being filled in, trust-local
passwords, ``FL_KIT_DIR`` and the FL kit contents, and GPU capacity. Fix every ❌ row; ``up-onprem-trust`` runs the
same checks and will not start until they pass.

Then continue at step 6 of :doc:`onboarding-a-trust`.

.. tip::

   The checklist changes nothing, so you can run it at any time, even before the kit is in place.

.. warning::

   ``up-onprem-trust`` is the first-install command and resets XNAT. To change a running trust, use the rotate and
   upgrade sections of :doc:`onboarding-a-trust`.

***************
Troubleshooting
***************

Every trust container is named ``trust<N>-<service>-1``, where ``<N>`` is ``FL_KIT_SLOT_NUMBER`` in the kit file.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Symptom
     - Check
   * - Trust not polling the hub
     - Is the stack running (``sudo docker ps``)? Look for polling errors in ``sudo docker logs trust<N>-trust-api-1``.
   * - ``Connection timed out`` (FL)
     - Is a host or network firewall blocking outbound traffic on the FL port (8002 for production, 8003 for staging)?
   * - Ansible ``Permission denied``
     - Is the SSH key correct? Does the user have ``sudo``? In local-host mode, is ``ANSIBLE_BECOME_PASS`` set?

.. seealso::

   :ref:`ts-trust-node-compose` for storage, XNAT, imaging and governance problems on a Compose node.
