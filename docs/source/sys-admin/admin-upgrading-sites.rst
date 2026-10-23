.. Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at
       http://www.apache.org/licenses/LICENSE-2.0
   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.

.. _admin-upgrading-sites:

##################
Upgrading a site
##################

A FLIP release is a git tag ``vX.Y.Z`` on ``main``. Every published image (the APIs, the FL server and client, omop-db,
Orthanc and the XNAT images) is built at that commit and tagged ``:vX.Y.Z``, so one tag names the whole platform. A
site pins that tag in its kit and moves between releases with one command.

Two rules apply throughout:

- **A site runs the release its hub runs.** The hub and its sites must agree on the FL framework version (a FLARE
  2.9 client cannot join a 2.8 server), the payload encryption and the task contract. The upgrade therefore defaults
  to the release the hub reports, and stops if the hub cannot say. It tells you when a newer release exists, but
  offers it only once the hub runs it.
- **The site operator triggers the upgrade.** Nothing on the hub pushes an upgrade. The release notes' *Site upgrade*
  section is the prompt; you run the command on your own host when you are ready.

.. warning::

   **Never use the first-install commands to move a live site.** ``make up-onprem-trust``, ``make -C trust
   up-trust``, ``restart-trust`` and ``make -C deploy/providers/AWS deploy-trust`` re-seed the mock data and **wipe
   the XNAT archive**. Use only the upgrade commands below.

.. list-table:: Upgrade command per deployment shape
   :header-rows: 1
   :widths: 20 15 65

   * - Shape
     - Who runs it
     - Command (from a FLIP checkout at the release tag)
   * - On-premises (Docker Compose)
     - Site operator
     - ``sudo -E make upgrade-onprem-trust KIT=<slot>``
   * - Kubernetes (Helm)
     - Site operator
     - ``make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env>``
   * - Cloud (EC2) trust
     - FLIP admin
     - ``make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env>``
   * - Central Hub
     - FLIP admin
     - ``make -C deploy/providers/AWS deploy-centralhub PROD=true TAG=vX.Y.Z`` (see :ref:`upgrade-hub`)

.. contents::
   :local:
   :depth: 1

**********************
The operator's runbook
**********************

This is the procedure for an on-premises (Docker Compose) site, the common case. The Kubernetes and EC2 variants
follow it.

Step 1 — Read the release
=========================

*Who: site operator*

Every GitHub Release has a *Site upgrade* section. It says:

- whether the upgrade is required;
- the order: hub first, sites first, or both in one maintenance window;
- whether you need a **refreshed kit**, because a hub-shared value changed (for example a rotated encryption key, a
  new FL kit date or a new hub URL).

If the hub goes first, wait for the FLIP admin to confirm the hub is on the release.

Step 2 — Replace the Hub-shared block (refreshed kit only)
==========================================================

*Who: site operator*

If you were sent a refreshed kit, open the new ``.env.<CODE>.production`` and copy **only** the block between
``# ── Hub-shared (managed …)`` and ``# ── Kit credentials (managed)`` into your own kit file. Keep your Host-local
profile (ports, data directories, ``FL_KIT_DIR``) and your Trust-local credentials. Do not copy the whole file over
yours.

.. important::

   **Put any agreed PACS retrieval window in the kit before you upgrade.** The upgrade sets XNAT's PACS availability
   schedule from the kit (``PACS_AVAILABILITY_*``, ``PACS_THREADS``, ``PACS_UTILIZATION_PERCENT``) and overwrites
   any window set in XNAT's DQR admin page. A window that crosses midnight (``19:00``–``07:00``) is refused: use
   ``19:00``–``24:00`` plus ``00:00``–``07:00``, or whole days.

Step 3 — Move your FLIP checkout to the release
===============================================

*Who: site operator*

The compose files, Makefiles, XNAT stack files and the upgrade command itself come from your git checkout, not from the
images. The checkout must be at the same tag as the images you are about to run.

.. code-block:: shell

   cd <your FLIP checkout>
   git fetch --tags origin
   git checkout vX.Y.Z    # the release named in the Site upgrade section

Your kit file, the FL kit and the data directories are untracked, so checking out a tag never touches them. The
upgrade refuses a release from a checkout at any other tag (exit code 6).

.. note::

   **Upgrading from v0.7.0 or earlier.** The ``upgrade-onprem-trust`` command does not exist in those checkouts; it
   arrives with the checkout in this step. A hub on v0.7.0 or earlier does not report its release either, so until
   the hub has moved past it, pass ``TAG=vX.Y.Z`` explicitly.

Step 4 — Run the upgrade
========================

*Who: site operator*

From the checkout:

.. code-block:: shell

   sudo -E make upgrade-onprem-trust KIT=<slot>                # e.g. KIT=Trust_2; targets the hub's release
   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=vX.Y.Z     # or pin the release explicitly

.. list-table:: Options
   :header-rows: 1
   :widths: 30 70

   * - Option
     - Effect
   * - ``KIT=<slot>``
     - Required. The trust's kit slot.
   * - ``TAG=vX.Y.Z``
     - The release to move to. Default: the release the hub reports.
   * - ``PROD=<env>``
     - Which kit file to use. Default ``true``: ``trust/.env.<KIT>.production``, else ``trust/.env.<KIT>``. Other
       values: ``stag``, ``lza``, ``lza-stag``.
   * - ``FORCE=1``
     - Allow a move to an older release (including a release's own release candidates).
   * - ``YES=1``
     - Skip the prompts, for a scripted run.
   * - ``FL_TAG=<tag>``
     - Pin the FL client apart from the rest (``sha-`` targets only).
   * - ``ALLOW_CHECKOUT_DRIFT=1``
     - Skip the checkout-at-tag check. Only for testing a branch that is not a release.

**What the upgrade does**, in order. Every check runs before anything changes; if one fails, nothing was changed.

#. **Readiness checklist**, the same gate as the first install. Its *Hub-shared block current* row compares your
   kit's encryption key with the hub's and warns when the kit pins a release behind the hub. A just-refreshed kit
   passes with a warning that trust-api still runs the old key; the upgrade recreates it.
#. **Pick the target**: ``TAG=``, else the hub's reported version, and print ``site <current> → target <release>``.
   A hub deployed by CI reports a ``sha-`` build; from a checkout at that commit's release tag, the release tag is
   targeted instead (same code).
#. **Look for a newer release** on GitHub. If the hub already runs it, you choose to continue or to stop (exit code 4)
   and check it out first. Otherwise it is only a warning: a site cannot run ahead of its hub.
#. **Check the checkout** is at the target release (not checked for a ``sha-`` target).
#. **Check the registry** for every image the site pulls (trust APIs, Orthanc, omop-db, XNAT, FL client) at the target
   tag, or at the kit's own ``OMOP_DB_TAG`` / ``ORTHANC_TAG`` / ``XNAT_TAG`` pin.
#. **Confirm**, then write the tag into the kit's ``DOCKER_TAG`` and ``DOCKER_FL_TAG``, so the kit records what is
   installed.
#. **Pull and recreate** only the containers whose image or configuration changed. OMOP, Orthanc and XNAT data stay on
   their bind mounts.
#. **Upgrade XNAT in place**:

   - dump ``xnat-db`` with ``pg_dumpall`` into ``$XNAT_DATA_DIR/backups/``. Without a complete dump the upgrade stops
     before touching XNAT;
   - redeploy the XNAT stack on the new image (XNAT migrates its own schema on boot);
   - re-apply XNAT's configuration: DICOM receiver, DQR lockdown, PACS registration, PACS availability schedule (from
     the kit, see step 2), Container Service backend and the dcm2niix command.

The upgrade never re-seeds data and never resets XNAT.

.. note::

   **Release tags versus** ``sha-`` **tags.** A release tag is built for every image. A ``sha-`` tag only exists for
   the images that commit changed, so ``TAG=sha-…`` is refused when any image was never built at it. To move to a
   ``sha-`` tag anyway, hold the missing images at their own build: ``FL_TAG=sha-…`` for the FL client, and
   ``OMOP_DB_TAG`` / ``ORTHANC_TAG`` / ``XNAT_TAG`` in the kit for the data services.

.. list-table:: Exit codes
   :header-rows: 1
   :widths: 10 50 40

   * - Code
     - Meaning
     - What to do
   * - 0
     - Upgrade completed.
     - Verify (step 5).
   * - 1
     - The readiness checklist failed.
     - Fix the rows marked as failing and re-run.
   * - 2
     - No usable target: the hub did not report a release, the kit has no hub URL, or ``FL_TAG`` is not an image tag.
     - Pass ``TAG=vX.Y.Z`` from the release page.
   * - 3
     - The target is older than the installed release.
     - Re-run with ``FORCE=1`` if the downgrade is intended (see :ref:`upgrade-rolling-back`).
   * - 4
     - Not confirmed: you declined the prompt, or chose to stop for a newer release.
     - Check out the release you want and re-run.
   * - 5
     - An image is not published at the target tag, or the registry could not be asked.
     - Pick a tag built for every image, or pin the odd image; for a registry fault check ``docker login`` and the
       network.
   * - 6
     - The checkout is not at the target tag, or git could not read the checkout.
     - Check out the tag (step 3), or apply the printed ``safe.directory`` fix.

Exit codes 2 to 6 all mean **nothing was changed**. Run through ``make``, the shell sees make's own exit status
(2); the code above is in make's first ``*** … Error <code>`` line.

Step 5 — Verify
===============

*Who: site operator*

On your host, once the upgrade has finished:

.. code-block:: shell

   curl -s http://127.0.0.1:${TRUST_API_PORT:-8020}/health

A healthy reply looks like
``{"status":"ok","version":"vX.Y.Z","hub_version":"vX.Y.Z","hub_key_match":true,"hub_key_fingerprint":"…","dead_tasks":[]}``.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Field
     - Meaning
   * - ``version``
     - The build the trust-api container runs.
   * - ``hub_version``
     - The release the hub reports.
   * - ``hub_key_match``
     - ``true`` when trust-api's encryption key matches the hub's.

The hub fields are ``null`` until the next accepted heartbeat (a few seconds), and again whenever the hub rejects one.
Run ``make onboard-onprem-trust KIT=<slot>`` to see every checklist row again.

On the hub, the FLIP admin sees the same versions in *Connection Status → trust drawer*. Any container still on another
build shows an amber ``≠ hub`` pill.

Kubernetes
==========

*Who: site operator.* Check out the release tag first (step 3); the chart templates come from the checkout and the
command refuses a release from any other checkout.

.. code-block:: shell

   make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> [TAG=vX.Y.Z] [KUBE_CONTEXT=<ctx>]

Every FLIP-built image follows one chart value, ``global.image.tag`` (the observability images and ``xnat-dcm2niix``
keep their upstream versions). The command pins the target in your kit as on Compose, regenerates the Helm override
and Secret from the kit, and runs ``helm upgrade``. PersistentVolumeClaims survive, the vocabulary load is skipped on
populated data, and the API deployments roll onto the new images.

``KUBE_CONTEXT`` defaults to kubectl's current context; name it on a workstation with several clusters.

.. warning::

   Unlike Compose, the chart's mock-data seed also runs on upgrade. While ``trustData.seed.enabled`` is on, every
   upgrade re-applies the listed mock projects (only those projects' rows are replaced). **Turn it off in the
   override for a site whose stores hold real data only.**

**Per-image pins.** The kit's opt-outs reach the chart as pins that override ``global.image.tag`` for one image:

.. list-table::
   :header-rows: 1

   * - Kit value
     - Chart value
   * - ``OMOP_DB_TAG``
     - ``omopDb.image.pin``
   * - ``ORTHANC_TAG``
     - ``orthanc.image.pin``
   * - ``XNAT_TAG``
     - ``xnat.image.pin``
   * - ``DOCKER_FL_TAG`` (when it names a release or ``sha-`` tag)
     - ``flClient.image.pin``

A release tag never needs a pin. They are the only way to move a Kubernetes site to a ``sha-`` tag that some images
were never built at: a StatefulSet rolled onto a tag that does not exist stays down.

.. note::

   ``xnat.image.pin`` also selects the XNAT configure script, which the ``xnat-init`` Job runs from the xnat-web image.
   Older xnat-web images (v0.12.0, for example) carry a Docker-only script, and the Job refuses them by name. Hold
   XNAT back only to an xnat-web image from the release that ships your chart, or later.

Cloud (EC2) trust
=================

*Who: FLIP admin*, from the admin workstation at the release tag. It uses the same SSH Docker context as
``make deploy-trust``.

.. code-block:: shell

   make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> [TAG=vX.Y.Z]

It takes the same options as the on-premises upgrade and never re-seeds data. ``deploy-trust`` remains the
first-install command: it re-seeds OMOP and Orthanc and resets the XNAT archive.

.. _upgrade-rolling-back:

Rolling back
============

Check out the previous release's tag, then run the upgrade with that tag and ``FORCE=1``:

.. code-block:: shell

   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=vX.Y.Z FORCE=1

**Rolling back to v0.7.0 or earlier.** Those releases have no ``:vX.Y.Z`` images (the registry check refuses
``TAG=v0.7.0``) and their checkouts have no upgrade command. Stay on your current checkout and use the images built from
that release's commit:

.. list-table::
   :header-rows: 1

   * - Release
     - Image tag
   * - v0.7.0
     - ``sha-23cf331``
   * - v0.6.0
     - ``sha-22d845f``

.. code-block:: shell

   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=sha-23cf331

A ``sha-`` tag has no release order, so the command does not see this as a downgrade and needs no ``FORCE=1``: check the
printed ``site <current> → target sha-…`` yourself. The old images then run under the newer checkout's compose files, a
pairing no release was tested in. Use it to get back, not to stay.

.. warning::

   **XNAT schema migrations are forward-only.** To roll back across an XNAT version change, stop the stack, restore the
   dump the upgrade took (``$XNAT_DATA_DIR/backups/xnat-db-<stamp>.sql.gz``) into a fresh ``xnat-db-data`` volume, and
   redeploy at the old tag.

****************
The admin's part
****************

Refreshing site kits
====================

*Who: FLIP admin*

Needed when a release changes the Hub-shared block, or a hub deploy changed a shared value under the sites. For
example, a hub Terraform apply writes the encryption key from its inputs; a site on its old kit then fails every task
until it is re-keyed. On the admin workstation:

#. **Reconcile your env file with the deployed hub.** The deployed inputs are the source of truth.

   .. code-block:: shell

      cd deploy/providers/AWS
      uv run --no-project --with boto3 python scripts/reconcile_ci_env.py \
          --env prod --profile prod --compare ../../../.env.production

#. **Refresh every local kit's Hub-shared block** from the reconciled env file.

   .. code-block:: shell

      make sync-trust-kits PROD=true

#. **Package each site's kit** and send it over the encrypted channel used at onboarding. The packaging reads the
   slot-named file, so copy the refreshed kit there first:

   .. code-block:: shell

      cp trust/.env.<CODE>.production trust/.env.<slot>
      make -C deploy/providers/AWS package-onprem-trust-kit KIT=<slot> PROD=true

Then mark *Refreshed kit needed: yes* in the release's Site upgrade section, with the keys that changed, so operators
replace the block before upgrading.

.. _upgrade-hub:

Upgrading the hub
=================

*Who: FLIP admin*. Enable Deployment Mode, wait until ``GET /fl/quiesce`` reports no FL work in flight, then deploy:

.. code-block:: shell

   make -C deploy/providers/AWS deploy-centralhub PROD=true TAG=vX.Y.Z

.. seealso::

   ``deploy/providers/AWS/README.md`` and *Cutting a release* in ``CONTRIBUTING.md`` for the full hub procedure.

Approved cohorts after an upgrade
=================================

*Who: project approver or FLIP admin*

A site releases row-level data only for a cohort membership **frozen** when the project was approved. A project approved
on an earlier release has no frozen membership, so every site refuses it (training and the imaging pull both stop) until
it is frozen. The project page shows those sites as *not frozen*.

To freeze them:

- **One project:** ``POST /api/projects/{project_id}/cohort-snapshots``.
- **Every approved project** (hub on Docker Compose):

  .. code-block:: shell

     make -C flip-api backfill_cohort_snapshots                       # add EXTRA_ARGS="--dry-run" to preview

Both skip sites whose membership is already frozen. To re-freeze those too (the recovery for a site that lost its
snapshot store), add ``?include_frozen=true`` to the endpoint or ``EXTRA_ARGS="--include-frozen"`` to the backfill. A
site that still holds its membership keeps it unchanged; one that lost it freezes afresh from its live database.

*****************
What can go wrong
*****************

``Could not read the hub's version … Pass the release explicitly: TAG=vX.Y.Z``
   The hub is unreachable from this host, or it is on v0.7.0 or earlier and reports a version number rather than an
   image tag. Pass ``TAG=`` from the release page.

``this kit's AES key differs from the hub's``
   Your Hub-shared block is stale. Ask the admin for a refreshed kit (see `Refreshing site kits`_) and replace the
   block. Once the kit carries the hub's key, the row becomes a warning that trust-api still runs the old key, and the
   upgrade recreates it.

``Could not ask the registry whether … is published``
   Not a missing image: the registry refused or did not answer. Check ``docker login`` for the registry and this host's
   route to it, then re-run. Nothing was changed.

``git could not say which commit … is at``
   git refused to read the checkout, usually "dubious ownership" because the checkout belongs to another user than the
   one running the upgrade (typically under ``sudo``). Run the printed ``git config --global --add safe.directory``
   command as that user and re-run.

``No running …_xnat-db to back up`` / ``The XNAT database dump failed or is incomplete``
   The upgrade never migrates XNAT without a complete dump. Check the stack with ``docker stack ps <stack>`` and re-run.
   Nothing was upgraded.

``XNAT bind-mount sources … are not all owned by 1001:1001``
   Sites installed before XNAT ran as non-root have a root-owned archive that the new image cannot read. Run the
   printed ``chown -R`` once and re-run the upgrade.

``cannot write /app/local/resources.json`` in the FL client's logs after an upgrade
   The old root-running FL client left root-owned files in the kit's ``local/`` directory. ``chown`` them to the kit
   directory's owner (uid 1000) and restart the client.

.. seealso::

   - Onboarding a site: :doc:`/deploy-flip/onboarding-a-trust`
   - Deployment mode and the FL quiesce gate: :ref:`deployment-mode`
