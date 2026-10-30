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

- **Ubuntu 22.04 or newer**, physical or VM, with a login user that has the access listed under
  `Access the operator needs`_ (the Ansible step also needs ``sudo``).
- **Internet access**, to pull Docker images and packages.
- **Docker in swarm mode** (XNAT runs as a swarm stack). The readiness checklist tells you if it is missing; the
  one-off fix is ``docker swarm init``.
- **A writable directory for FLIP**, default ``/opt/flip``, with the sub-directories the stack mounts. The
  :gh:`on-prem Ansible playbook <trust/deploy/ansible/onprem.yml>` creates them and shows the full list.
- **For GPU training or evaluation:** an NVIDIA GPU on driver **580 or newer** (check *Driver Version* in
  ``nvidia-smi``) and the
  `NVIDIA Container Toolkit <https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>`_.
  The FL images bring their own CUDA 13 runtime, so the host needs no CUDA toolkit. The playbook installs neither
  the driver nor the toolkit. A CPU-only host (``NUM_AVAILABLE_GPUS=0`` in the kit) needs neither.

.. _onprem-operator-access:

*************************
Access the operator needs
*************************

No FLIP command on this page runs ``sudo`` itself. The account that runs them needs:

- **Docker socket access**: ``docker ps`` works, and the daemon is in swarm mode (``docker swarm init`` once, for
  XNAT). Socket access is root-equivalent on the host, so granting it is the host owner's decision, not FLIP's.
- **Bind-mount directories owned by the uid of the container that writes them.** Two ways to get there:

  - **Run the containers as yourself.** Set ``FLIP_UID`` and ``FLIP_GID`` in the kit to your own ids (``id -u``,
    ``id -g``). Every container that writes a bind mount then runs as you, so the directories you create with
    ``mkdir -p`` are already right and nothing needs root. This is the route for a host where you have Docker but
    no ``sudo``. The one exception is ``xnat-db``, which still starts as root and re-owns its own data directory
    (its Alpine Postgres cannot run as a uid it does not know): you create that directory too, and deleting it
    later needs root.
  - **Keep the images' own users** (the table below) and have whoever prepares the host run, once, as root:

    .. code-block:: shell

       make -C trust prepare-host-dirs KIT=<CODE> HUB=prod

    It creates every directory below with its owner and deletes nothing, so it is safe to re-run.

  ``make up-onprem-trust`` only checks the ownership either way, and a failed check names the fix.

  .. list-table::
     :header-rows: 1
     :widths: 40 12 48

     * - Written by
       - uid (default)
       - Directory (kit file)
     * - trust-api, imaging-api, data-access-api, the FLARE client
       - 1000
       - ``BASE_IMAGES_DOWNLOAD_DIR``, ``COHORT_SNAPSHOT_STORAGE_DIR``
     * - xnat-web
       - 1001
       - ``XNAT_DATA_DIR``
     * - Orthanc
       - 999
       - ``ORTHANC_STORAGE_DIR``
     * - Flower SuperNode
       - gid 49999
       - ``BASE_IMAGES_DOWNLOAD_DIR/net-<N>``, group-writable

  Set ``OMOP_DATA_DIR``, ``ORTHANC_STORAGE_DIR``, ``BASE_IMAGES_DOWNLOAD_DIR`` and ``COHORT_SNAPSHOT_STORAGE_DIR``
  in the kit to absolute paths on the data disk before they are created. The kit template carries the
  development values, which are relative and so land inside the checkout; unset ones fall back to ``/opt/flip``.
  The checklist's *Data dir paths* row warns on a relative path and fails on one that does not exist yet and that
  you cannot create.
- **Read access to the checkout and the FL kit** (``FL_KIT_DIR``).
- **Python 3.11 or later** for the readiness checklist and ``make -C trust check-governance``, which read TOML with
  the standard library. ``uv`` installs one when it can download it; otherwise the host must provide a
  ``python3.11`` or newer. The checklist refuses an older interpreter up front.

A host provisioned by the Ansible playbook below gives the login user none of this, on purpose: it does not add the
user to the ``docker`` group. There, every command on this page runs as ``sudo -E make …`` (``-E`` keeps your
``$HOME``, so root's Docker reuses your registry login), and the playbook has already created the directories. On
a host where you already have Docker access but no ``sudo``, as in many trusted research environments, run them as
yourself, with ``FLIP_UID``/``FLIP_GID`` set to your ids or once the host owner has run ``prepare-host-dirs``.
Rootless Docker is untested and not supported: under it the container uids map to subordinate ids of your
account, so ``FLIP_UID`` would have to name container root, which nothing here has exercised. Where Docker is not
available to you at all, use the Helm chart (:doc:`deploy-flip-node-kubernetes`).

.. _onprem-forward-proxy:

Behind a forward proxy
======================

Where all outbound traffic goes through a proxy, as in most trusted research environments, three settings matter:

- **Image pulls** use the Docker daemon's own proxy setting (a systemd drop-in). The host owner sets it.
- **Containers** get no proxy unless you give them one. Without it, trust-api cannot reach the hub and the FL client
  cannot reach the FL server. Put it in your Docker client config, ``~/.docker/config.json``; Compose passes it to
  every container it creates. ``noProxy`` must name the stack's own services, or their calls to each other go to the
  proxy too. Add your site's internal domains to it.

  .. code-block:: json

     {
       "proxies": {
         "default": {
           "httpProxy": "http://proxy.example.nhs.uk:3128",
           "httpsProxy": "http://proxy.example.nhs.uk:3128",
           "noProxy": "localhost,127.0.0.1,trust-api,data-access-api,imaging-api,orthanc,omop-db,xnat-web,loki"
         }
       }
     }

  Merge the ``proxies`` key into the file if it exists; containers created before the change keep their old
  environment until they are recreated. The proxy must also allow the FL server's port (the kit's
  ``FL_EDGE_PORT``, else ``FL_SERVER_PORT``), not only 443.
- **Your shell** may export ``http_proxy`` too. Then ``curl http://127.0.0.1:<port>`` goes to the proxy, and a
  published port seems not to answer. Use ``curl --noproxy '*' …``, or export ``NO_PROXY=localhost,127.0.0.1``
  (and ``no_proxy``). The readiness checklist's own probe bypasses the proxy.

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
   cp $HOME/flip-trust-kit/.env.<CODE>.prod trust/

The tarball holds the kit file ``.env.<CODE>.prod`` (``.stag`` for the staging hub) and an ``fl-kit/`` folder. Keep ``fl-kit/`` exactly as it is: the
stack mounts that layout into the FL client.

***********************************
Step 3 — Edit the kit for your host
***********************************

*Who: trust operator*

In ``trust/.env.<CODE>.prod``, change only the settings below. Leave the *Hub-shared* and *Kit credentials* blocks alone;
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
   * - ``FLIP_UID`` and ``FLIP_GID``
     - Your own ids (``id -u``, ``id -g``) to run the containers as you, so the directories you create need no root
       to prepare (:ref:`onprem-operator-access`). Leave unset to keep the images' own users.
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

Unless you run as root or on a host the playbook provisioned, the host owner first runs
``make -C trust prepare-host-dirs KIT=<CODE> HUB=prod`` as root (:ref:`onprem-operator-access`).

.. code-block:: shell

   make onboard-onprem-trust KIT=<CODE>   # readiness checklist
   make up-onprem-trust KIT=<CODE>        # starts only if every check passes

Both default to the production hub (``HUB=prod``); add ``HUB=stag`` for staging. The checklist prints a ✅ or ❌ for
each precondition: the kit file, Docker swarm, the hub-shared settings and credentials being filled in, the hub API
and the FL endpoint reachable from this host (the image registries too, as a warning), trust-local passwords,
``FL_KIT_DIR`` and the FL kit contents, and GPU capacity. Fix every ❌ row; ``up-onprem-trust`` runs the same checks
and will not start until they pass.

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
     - Is the stack running (``docker ps``)? Look for polling errors in ``docker logs trust<N>-trust-api-1``.
   * - ``Connection timed out`` (FL)
     - Is a host or network firewall blocking outbound traffic on the FL port (8002 for production, 8003 for staging)?
   * - Ansible ``Permission denied``
     - Is the SSH key correct? Does the user have ``sudo``? In local-host mode, is ``ANSIBLE_BECOME_PASS`` set?

.. seealso::

   :ref:`ts-trust-node-compose` for storage, XNAT, imaging and governance problems on a Compose node.
