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

.. _ts-trust-node-compose:

##############################
Trust node on Docker Compose
##############################

Problems on a trust node that runs the Docker Compose stack: an on-prem host
(:doc:`../deploy-flip/deploy-flip-node-on-prem`) or the trust EC2 the AWS deployment creates.

**Where commands run.** ``make -C …`` commands run from the repo root. ``docker`` commands run on the trust host,
with ``sudo`` on an on-prem host. The AWS-hosted trust is reached with ``ssh flip-trust``, which
``make -C deploy/providers/AWS ssh-config PROD=<env>`` sets up.

**Kit and environment.** On an on-prem host the kit file is ``trust/.env.<slot>``, so ``make -C trust …`` commands
take ``KIT=<slot> PROD=true`` and, when they touch Docker, run with ``sudo -E``. Without ``PROD`` they fall back to
the development setup. For the AWS-hosted trust they run on the workstation with ``KIT=<CODE> PROD=<env>``.

**Container names.** A trust's containers are named ``trust<N>-<service>-1`` and its XNAT stack ``xnat<N>``,
where ``<N>`` is the FL kit slot the hub assigned (``FL_KIT_SLOT_NUMBER`` in the kit file). ``sudo docker ps``
lists them.

.. contents:: On this page
   :local:
   :depth: 2

.. warning::

   Several fixes below mention the first-install commands (``make up-onprem-trust``,
   ``make -C deploy/providers/AWS deploy-trust``, ``make -C trust/xnat up-xnat``). They **wipe the XNAT archive**,
   and the first two re-seed the trust's data. Never use them to move a live site to a new release; see
   :ref:`admin-upgrading-sites`.

***********************
Bring-up and deployment
***********************

.. _ts-compose-orthanc-mount:

``invalid spec: :/var/lib/orthanc/db: empty section between colons``
====================================================================

**Cause.** ``ORTHANC_STORAGE_DIR`` is missing from the trust's kit file, so the Orthanc data mount has no host
path.

**Fix.** Add it to the kit file's host-local section, with a path on the trust host, then bring the stack up
again:

.. code-block:: text

   ORTHANC_STORAGE_DIR=/opt/flip/orthanc/orthanc-storage

A kit created from an older template may lack the key: copy it from ``trust/.env.example``.

.. _ts-compose-disk-full:

``no space left on device`` while the AWS-hosted trust pulls images
===================================================================

**Cause.** The trust image set (the FL client with its PyTorch dependencies, XNAT, Orthanc, OMOP and the APIs)
needs more room than an older 50 GB root volume. Terraform now provisions 100 GB, but growing an existing volume
leaves the partition and filesystem at the old size.

**Fix.** After ``terraform apply`` has grown the volume, resize once on the host and retry the deployment:

.. code-block:: shell

   ssh flip-trust 'sudo growpart /dev/nvme0n1 1 && sudo resize2fs /dev/nvme0n1p1'
   ssh flip-trust 'df -h /'     # should show about 100 GB

.. _ts-compose-images-permission:

imaging-api returns 500 with ``Permission denied`` on ``/app/data/images/net-<N>``
==================================================================================

Training later fails with ``num_samples=0`` (see :ref:`ts-num-samples-zero`).

**Cause.** The host directory behind the images mount is owned by root, usually because Docker created it. Each
FL client mounts only its own ``net-<N>`` folder and writes to it too, so the folder must be writable by both
imaging-api and the FL client:

- **NVIDIA FLARE:** the client runs as the same user as imaging-api, so the owner of the images directory with mode
  ``0755`` is enough.
- **Flower:** the client runs as uid/gid ``49999``, so each ``net-<N>`` folder needs group ``49999`` and mode
  ``0775``.

**Fix.** On an on-prem host, the bring-up checks these folders and stops with a message naming the exact
``chown`` and ``chmod`` to run. On the AWS-hosted trust, where the directories are ``/opt/flip/data/trust-<N>``:

.. code-block:: shell

   # NVIDIA FLARE
   ssh flip-trust 'sudo chown -R 1000:1000 /opt/flip/data/trust-<N>'
   # Flower: also open each net folder to the client's group
   ssh flip-trust 'sudo chown 1000:49999 /opt/flip/data/trust-<N>/net-{1,2} && \
                   sudo chmod 0775 /opt/flip/data/trust-<N>/net-{1,2}'

.. tip::

   Wherever a non-root container bind-mounts a host path, create that path with the right owner before the
   container starts. A path Docker creates is owned by root.

*****************
Imaging and XNAT
*****************

.. _ts-compose-orthanc-empty:

Every study goes straight to QueueFailed on the AWS-hosted trust
================================================================

imaging-api logs ``POST /xapi/dqr/query/studies`` answered with ``204`` for every accession, and XNAT's
``dqr.log`` shows nothing queued.

**Cause.** The mock Orthanc is empty. The deployment evaluates the Compose files on the workstation that drives
it, so a relative or workstation path in the kit file (such as the template's ``./orthanc/...``) is used as-is on
the EC2. Docker creates that directory empty and Orthanc starts with no studies.

**Fix.**

#. Check where Orthanc's data actually lives:

   .. code-block:: shell

      ssh flip-trust 'docker inspect trust<N>-orthanc-1 --format "{{json .Mounts}}"'

#. Set host paths (``/opt/flip/...``) for ``ORTHANC_STORAGE_DIR`` and the other host-local entries in the kit file.
   Check the other containers' mounts the same way.
#. Redeploy the trust, which stops the stack, seeds OMOP and Orthanc on the host and starts it again:

   .. code-block:: shell

      make -C deploy/providers/AWS deploy-trust KIT=<CODE> PROD=<env>

#. Re-trigger the import from the project page.

.. _ts-compose-xnat-setup-page:

Image import fails with HTTP 500 and ``<title>XNAT Setup</title>``
==================================================================

**Cause.** XNAT was configured but its web container is still serving the first-run setup page. It needs a
restart to pick up the saved configuration.

**Fix.** Restart the XNAT web service and wait about 90 seconds for Tomcat to start:

.. code-block:: shell

   sudo docker service update --force xnat<N>_xnat-web

.. _ts-compose-imaging-api-hung:

Image import tasks fail with ``ReadTimeout`` and imaging-api stops answering
============================================================================

**Cause.** imaging-api is stuck on a call to XNAT, for example while XNAT's container service hangs. Requests
queue behind it.

**Fix.** Restart imaging-api:

.. code-block:: shell

   sudo docker restart trust<N>-imaging-api-1

If it hangs again, check that XNAT itself answers (:ref:`ts-compose-xnat-401`).

.. _ts-compose-xnat-401:

``XNAT API returned 401 — check XNAT_SERVICE_USER/PASSWORD``
============================================================

``make -C deploy/providers/AWS status`` reports this for the AWS-hosted trust; on any trust, imaging-api's calls to
XNAT fail with 401. The XNAT web UI itself still loads.

**Cause.** imaging-api's password for the XNAT service account does not match the one XNAT holds. The usual
reason is a ``#`` or ``$`` in ``XNAT_SERVICE_PASSWORD``: the kit file is also read by ``make``, which treats ``#``
as the start of a comment and ``$`` as a variable. XNAT was configured with the truncated value, while imaging-api
got the whole one.

**Fix.**

#. Check what ``make`` sees. Run from the repo root, with the kit file's path:

   .. code-block:: shell

      printf 'include trust/.env.<slot>\nall:\n\t@echo "[$(XNAT_SERVICE_PASSWORD)]"\n' | make -f -

   If the output stops at the first ``#``, this is the cause.
#. Choose a password of letters, digits and ``-_=+!@`` only, and set it as ``XNAT_SERVICE_PASSWORD`` in the kit
   file.
#. Give XNAT's service account the same password. Either set it through XNAT's user API as the XNAT admin (the
   same call as in :ref:`ts-k8s-xnat-401`), or, as a last resort, reset XNAT:

   .. code-block:: shell

      sudo -E make -C trust/xnat up-xnat KIT=<slot> PROD=true

   ``up-xnat`` deletes the XNAT archive and database and configures a new XNAT from the kit. For the AWS-hosted
   trust, run it from the workstation with ``KIT=<CODE> PROD=<env>`` and ``DOCKER_CONTEXT=flip-trust`` in front, as
   the deployment does.
#. Recreate imaging-api so it reads the new value. A plain ``docker restart`` keeps the old environment; see
   "Changing the log level" on :doc:`../components/component-logging-stack` for how to recreate one container.

.. warning::

   Never change the password with SQL on XNAT's ``xdat_user`` table. XNAT rejects hashes written that way.

.. _ts-compose-dcm2niix:

Images import, but no NIfTI is ever produced
============================================

The configure log says ``does not hold the ContainerManager role``, or creating a project fails with
``XNAT has 0 'dcm2niix-scan' wrappers on the pinned ...``.

**Cause.** XNAT's DICOM-to-NIfTI command (dcm2niix) is not registered, or is registered against an older image
than the one this release pins. The causes are the same as on Kubernetes: :ref:`ts-k8s-dcm2niix-not-registered`
and :ref:`ts-k8s-dcm2niix-stale-image`.

**Fix.** Re-run XNAT's configuration against the live instance. It keeps all data, grants the admin the role and
moves the command to the pinned image in place:

.. code-block:: shell

   make -C trust/xnat xnat-configure XNAT_PROJECT=xnat<N>

Scans archived before the fix still have no NIfTI: convert them as in :ref:`ts-compose-nifti-backlog`.

.. _ts-compose-nifti-backlog:

Some archived scans have no NIfTI
=================================

**Cause.** Conversion runs once, when a scan is archived. Scans archived while the conversion trigger was missing
or broken are never converted afterwards.

**Fix.** List them, then convert one project and follow the conversions. Run this on an idle trust:

.. code-block:: shell

   sudo -E make -C trust reconvert-nifti KIT=<slot> PROD=true                           # report only
   sudo -E make -C trust reconvert-nifti KIT=<slot> PROD=true PROJECT="<id>" LAUNCH=1   # convert and follow

``WAIT=<minutes>``, ``MAX_ATTEMPTS=<n>`` and ``RELAUNCH_UNFINISHED=1`` tune it. What it selects and its exit codes
are explained in :ref:`ts-k8s-nifti-backlog`.

**********
Governance
**********

.. _ts-compose-governance:

data-access-api or the FL client will not start after a governance edit
=======================================================================

**Cause.** The trust's governance document is invalid. Both services refuse to start rather than ignore a rule,
and say why in their logs: the FL client half prints ``[site-privacy] FATAL: <what is wrong, and where>``.

**Fix.** Validate the document, then apply it without touching data:

.. code-block:: shell

   make -C trust check-governance KIT=<slot> PROD=true
   sudo -E make -C trust reload-governance KIT=<slot> PROD=true

``reload-governance`` recreates data-access-api (and, on NVIDIA FLARE, the FL clients), so apply it between
training runs. The usual mistakes are listed in :ref:`ts-k8s-governance`.

.. seealso::

   "Trust governance document (per trust)" on :ref:`fl-update-privacy`.
