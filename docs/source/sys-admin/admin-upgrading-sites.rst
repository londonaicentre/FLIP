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

A FLIP release is a git tag ``v<X.Y.Z>`` on ``main``. Since FLIP#1204 every published image —
the four APIs, the FL server/client sets, omop-db, orthanc and the XNAT images — is rebuilt at
that commit and tagged ``:v<X.Y.Z>``, so one tag names the whole platform. A site *pins* that tag
(``DOCKER_TAG`` / ``DOCKER_FL_TAG`` in its kit's Hub-shared block, or ``global.image.tag`` for a
Kubernetes site) and moves between releases with one command.

Two rules shape everything below:

- **A site runs the release its hub runs.** Hub↔site coupling is real — the FL framework pin
  (an NVFLARE 2.9 client cannot join a 2.8 server), the payload cipher, the task contract — so
  the upgrade command defaults to the hub's release, read from the hub's ``/api/health``, and
  refuses to guess when the hub cannot say. "Latest on GitHub" is not the target; the hub is.
- **The operator triggers it.** Nothing on the hub pushes an upgrade. The prompt is the
  release's *Site upgrade* section (below); the site's operator runs the command when they are
  ready, on their own host, with the same readiness checklist that gated the first install.

.. contents::
   :local:
   :depth: 1

**********************
The operator's runbook
**********************

For an on-premises (Docker Compose) site — the common case. Kubernetes and cloud EC2 variants
follow.

1. **Read the release.** Every GitHub Release carries a *Site upgrade* section saying whether
   the upgrade is required, in what order (hub first, sites first, or both in one
   maintenance window), and whether you need a **refreshed kit** because the Hub-shared block
   changed (a rotated AES key, a new FL kit date, a hub URL). If it says the hub goes first,
   wait for the FLIP admin to confirm the hub is on the release.

2. **Replace the Hub-shared block if you were sent a refreshed kit.** Copy only the block
   between ``# ── Hub-shared (managed …)`` and ``# ── Kit credentials (managed)`` from the new
   ``.env.<CODE>.production`` into yours. Your Host-local profile (ports, data directories,
   ``FL_KIT_DIR``) and Trust-local credentials stay as they are — do not copy the whole file
   over yours.

3. **Run the upgrade.** From your FLIP checkout:

   .. code-block:: bash

      sudo -E make upgrade-onprem-trust KIT=<slot>            # e.g. KIT=Trust_2
      # or pin explicitly:
      sudo -E make upgrade-onprem-trust KIT=<slot> TAG=v0.7.0

   What it does, in order:

   - runs the readiness checklist (``make onboard-onprem-trust``) — the same gate as the first
     install, now with a *Hub-shared block current* row that asks your running trust-api whether
     its AES key still matches the hub's, and warns when the kit pins a release behind the hub;
   - resolves the target: ``TAG=`` if given, else the hub's ``/api/health`` ``version``. It
     prints ``site <current> → target <release>`` and asks you to confirm (``YES=1`` skips the
     prompt for a scripted run). A move to an older release is refused unless ``FORCE=1``;
   - writes the tag into your kit (``DOCKER_TAG`` and ``DOCKER_FL_TAG``), so the kit always
     records what is installed;
   - pulls the images and recreates only the containers whose image or configuration changed
     (``docker compose up -d``); OMOP, Orthanc and XNAT data stay on their bind mounts;
   - upgrades XNAT **in place**: a ``pg_dumpall`` of ``xnat-db`` into ``$XNAT_DATA_DIR/backups/``,
     the Swarm stack redeployed onto the new image (XNAT migrates its own schema on boot), then
     the configure pass re-applied — DICOM receiver, DQR lockdown, PACS registration,
     Container Service backend, dcm2niix command — against the live instance.

   It never runs ``update-omop-data``, ``update-orthanc-data`` or ``xnat-reset``. Those belong to
   ``make up-onprem-trust`` / ``make -C trust up-trust``, the **first-install** verbs, which
   re-fetch the mock data and wipe the XNAT archive. Do not use them, or ``restart-trust``, to
   move a live site.

4. **Verify.** The checklist re-runs at the end of the upgrade. On your host:

   .. code-block:: bash

      curl -s http://127.0.0.1:${TRUST_API_PORT:-8020}/health
      # {"status":"ok","version":"v0.7.0","hub_version":"v0.7.0","hub_key_match":true,"dead_tasks":[]}

   ``version`` is the build the container was made from; ``hub_version`` is what the hub says
   about itself; ``hub_key_match: true`` means your kit's AES key is the hub's. On the hub, the
   FLIP admin sees the same versions in *Connection Status → trust drawer*, with an amber
   ``≠ hub`` pill on any container still on another build.

Kubernetes
==========

The chart pins the release with one value, ``global.image.tag``, which every FLIP-built image
follows (observability images and ``xnat-dcm2niix`` keep their upstream versions). ``sync-kit``
fills it from the kit's ``DOCKER_TAG``; the upgrade verb sets it explicitly:

.. code-block:: bash

   make -C deploy/providers/kubernetes upgrade-trust-k8s KIT=<CODE> PROD=<env> [TAG=vX.Y.Z] [KUBE_CONTEXT=<ctx>]

This is a plain ``helm upgrade`` at the new tag: PersistentVolumeClaims survive it, the init
hooks short-circuit on populated data, and the API deployments roll onto the new images. The
resolver pins your kit too, so a later ``sync-kit`` regenerates the same tag rather than
reverting it.

Cloud (EC2) trust
=================

From the admin workstation, over the same SSH docker context ``make deploy-trust`` uses:

.. code-block:: bash

   make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> [TAG=vX.Y.Z]

``deploy-trust`` remains the first-install verb: its ``seed-trust-data`` prerequisite re-seeds
OMOP and Orthanc on the host and its XNAT step resets the archive.

Rolling back
============

Run the same verb with the previous release and ``FORCE=1`` (a release→release move backwards
is otherwise refused):

.. code-block:: bash

   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=v0.6.0 FORCE=1

XNAT is the exception: its schema migrations are forward-only. The upgrade took a dump before
changing the XNAT tag — ``$XNAT_DATA_DIR/backups/xnat-db-<stamp>.sql.gz`` — so a rollback across
an XNAT version change means stopping the stack, restoring that dump into a fresh
``xnat-db-data`` and redeploying at the old tag.

********************
The admin's part
********************

The FLIP admin is involved only when the release changes something in the Hub-shared block, or
when a hub deploy has rotated a shared value under the sites — a hub Terraform apply writes the
AES key from the deployment's inputs into Secrets Manager, and on 2026-09-01 that happened
while a site kept its old kit: every task failed to decrypt until the kit was re-keyed. The
sequence, all on the admin workstation:

.. code-block:: bash

   # 1. The hub's deployed inputs are the truth; the laptop env file follows them.
   cd deploy/providers/AWS && uv run --no-project --with boto3 python scripts/reconcile_ci_env.py \
       --env prod --profile prod --compare ../../../.env.production
   # 2. Refresh every local kit's Hub-shared block from the reconciled env file.
   make sync-trust-kits PROD=true
   # 3. Package each site's kit and send it over the encrypted channel used at onboarding.
   make -C deploy/providers/AWS package-onprem-trust-kit KIT=<CODE> PROD=true

Then note *Refreshed kit needed: yes* in the release's Site upgrade section, and which keys
changed, so operators know to replace the block before upgrading.

Deploying the hub itself — ``make deploy-centralhub PROD=true TAG=v<X.Y.Z>`` after enabling
Deployment Mode and waiting for ``GET /fl/quiesce`` — is described in ``deploy/providers/AWS/README.md``
and in *Cutting a release* in CONTRIBUTING.md.

*****************
What can go wrong
*****************

``Could not read the hub's version … Pass the release explicitly: TAG=vX.Y.Z``
   The hub is unreachable from this host, or it was built before FLIP#1204 and reports a version
   number rather than an image tag. Pass ``TAG=`` from the release page.

``the hub's AES key differs from this kit's``
   The Hub-shared block is stale. Ask the admin for a refreshed kit (the sequence above) and
   replace the block; the upgrade verb runs the checklist first, so it will not proceed until
   this is fixed.

``XNAT bind-mount sources … are not all owned by 1001:1001``
   A site installed before the XNAT hardening ran ``xnat-web`` as root, so its archive is
   root-owned and the non-root 1.10 image cannot read it. Run the printed ``chown -R`` once
   and re-run the upgrade.

``cannot write /app/local/resources.json`` in the FL client's logs after an upgrade
   Same cause on the FL side: the old root-running client left root-owned files in the kit's
   ``local/`` directory. ``chown`` them to the kit directory's owner (uid 1000) and restart the
   client.

.. seealso::

   - Onboarding an on-premises site: :doc:`/deploy-flip/deploy-flip-node-on-prem`
   - Deployment Mode and the FL quiesce gate: :doc:`/sys-admin/admin-platform-support`
