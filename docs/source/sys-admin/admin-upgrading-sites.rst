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

3. **Move your FLIP checkout to the release.** The images are only half of a release: the
   compose files, the Makefiles, the XNAT stack files and the upgrade command itself all come
   from the git checkout on your host, not from the images, and they change between releases.
   So the checkout has to be at the same tag as the images you are about to run:

   .. code-block:: bash

      cd <your FLIP checkout>
      git fetch --tags origin
      git checkout vX.Y.Z          # the release named in the Site upgrade section

   Your kit (``trust/.env.<CODE>.production``), the FL kit and the data directories are
   untracked, so checking out a tag never touches them. The upgrade verb refuses a release
   target from a checkout that is not at that tag (exit code 6, naming the fix) — the one
   reason to override it, ``ALLOW_CHECKOUT_DRIFT=1``, is testing a branch that is not a
   release.

   .. note::

      On the first upgrade from a site installed before this runbook existed (v0.7.0 or
      earlier) the ``upgrade-onprem-trust`` command is not in your checkout yet — it arrived
      with the release you are moving to. The checkout step above is what makes it appear;
      run it first, then continue. A hub on v0.7.0 or earlier does not report its release
      either, so until the hub has moved past it the command stops and asks for ``TAG=``.

4. **Run the upgrade.** From that checkout:

   .. code-block:: bash

      sudo -E make upgrade-onprem-trust KIT=<slot>            # e.g. KIT=Trust_2
      # or pin explicitly:
      sudo -E make upgrade-onprem-trust KIT=<slot> TAG=vX.Y.Z

   What it does, in order:

   - runs the readiness checklist (``make onboard-onprem-trust``) — the same gate as the first
     install, now with a *Hub-shared block current* row that compares your kit's AES key with
     the hub's (as your running trust-api last heard it), and warns when the kit pins a release
     behind the hub. A kit you have just refreshed passes with a warning that trust-api still
     runs the old key: this upgrade is what recreates it;
   - resolves the target: ``TAG=`` if given, else the hub's ``/api/health`` ``version``. A hub
     deployed by the CI Terraform apply reports the ``sha-`` build of its commit; from a
     checkout at that commit's release tag (step 3) the upgrade targets the release tag
     instead — the same code, and the tag every image is published at. It prints
     ``site <current> → target <release>``. A move to an older release — a release's own
     release candidates included — is refused unless ``FORCE=1``;
   - confirms this checkout is at the target release (step 3) — a ``sha-`` target is never
     checked, since a CI build has no git tag to be at. If git cannot read the checkout (for
     example "dubious ownership" when it belongs to another user and the upgrade runs under
     ``sudo``), it stops and prints the ``git config --global --add safe.directory`` fix;
   - checks the registry (``docker manifest inspect``) for every image the site pulls — the
     three APIs, Orthanc, ``omop-db``, the three XNAT images and the FL client — at that tag,
     or at the kit's own pin for the ones that have one (``OMOP_DB_TAG``, ``ORTHANC_TAG``,
     ``XNAT_TAG``), and stops, naming the missing references, before anything is written. A
     registry it cannot ask (a login, network or rate-limit fault) is reported as that, not
     as a missing image. Release tags build every image; a ``sha-`` tag only carries the
     images that commit changed, so ``TAG=sha-…`` is refused whenever one of them was never
     built at it — hold that one image at its own build if you must move to a ``sha-`` tag:
     ``FL_TAG=sha-…`` for the FL client (it becomes the kit's ``DOCKER_FL_TAG``),
     ``OMOP_DB_TAG`` / ``ORTHANC_TAG`` / ``XNAT_TAG`` in the kit for the data services;
   - asks you to confirm (``YES=1`` skips the prompt for a scripted run), then writes the tag
     into your kit (``DOCKER_TAG`` and ``DOCKER_FL_TAG``), so the kit always records what is
     installed;
   - pulls the images and recreates only the containers whose image or configuration changed
     (``docker compose up -d``); OMOP, Orthanc and XNAT data stay on their bind mounts;
   - upgrades XNAT **in place**: a ``pg_dumpall`` of ``xnat-db`` into ``$XNAT_DATA_DIR/backups/``
     — only a complete dump is kept, and without one (a failed dump, or no running ``xnat-db``
     to take it from) the upgrade stops before XNAT is touched — then the Swarm stack
     redeployed onto the new image (XNAT migrates its own schema on boot), then the configure
     pass re-applied — DICOM receiver, DQR lockdown, PACS registration, Container Service
     backend, dcm2niix command — against the live instance.

   It never runs ``ensure-seeded`` or ``xnat-reset``. Those belong to
   ``make up-onprem-trust`` / ``make -C trust up-trust``, the **first-install** verbs, which
   re-seed the mock projects whenever the seed markers differ from the kit and wipe the XNAT
   archive. Do not use them, or ``restart-trust``, to
   move a live site.

5. **Verify.** Once the upgrade has finished, on your host:

   .. code-block:: bash

      curl -s http://127.0.0.1:${TRUST_API_PORT:-8020}/health
      # {"status":"ok","version":"vX.Y.Z","hub_version":"vX.Y.Z","hub_key_match":true,
      #  "hub_key_fingerprint":"…","dead_tasks":[]}

   ``version`` is the build the container was made from; ``hub_version`` is what the hub says
   about itself; ``hub_key_match: true`` means the AES key trust-api runs with is the hub's.
   The hub fields are ``null`` until the next accepted heartbeat (a few seconds), and again
   whenever the hub rejects one. ``make onboard-onprem-trust KIT=<slot>`` re-runs the checklist
   if you want every row again. On the hub, the
   FLIP admin sees the same versions in *Connection Status → trust drawer*, with an amber
   ``≠ hub`` pill on any container still on another build.

Kubernetes
==========

The chart pins the release with one value, ``global.image.tag``, which every FLIP-built image
follows (observability images and ``xnat-dcm2niix`` keep their upstream versions). ``sync-kit``
fills it from the kit's ``DOCKER_TAG``; the upgrade verb sets it explicitly. The chart templates
come from the checkout you run it from, so the same rule applies: ``git fetch --tags origin &&
git checkout vX.Y.Z`` first (the verb refuses a release tag from any other checkout).

.. code-block:: bash

   make -C trust/deploy/helm upgrade-trust-k8s KIT=<CODE> PROD=<env> [TAG=vX.Y.Z] [KUBE_CONTEXT=<ctx>]

The resolver pins your kit exactly as on Compose; ``sync-kit`` then regenerates the override
from it (``global.image.tag``, and the per-image pins below) and re-patches the Secret; then a
plain ``helm upgrade`` at the new tag. PersistentVolumeClaims survive it, the vocabulary hook
short-circuits on populated data, and the API deployments roll onto the new images. Unlike
Compose, the chart's ``trust-seed`` hook is ``post-upgrade`` too: while
``trustData.seed.enabled`` is on it re-applies the listed mock projects on every upgrade
(idempotent — only those projects' rows are replaced, Orthanc dedupes), so turn it off in the
override for a site whose stores hold real data only.
``KUBE_CONTEXT`` names the cluster for every ``helm`` / ``kubectl`` call the verb makes
(default: kubectl's current context — on a workstation with several clusters, name it).

The kit's ``OMOP_DB_TAG`` / ``ORTHANC_TAG`` / ``XNAT_TAG`` opt-outs reach the chart as
``omopDb.image.pin`` / ``orthanc.image.pin`` / ``xnat.image.pin``, and ``DOCKER_FL_TAG`` as
``flClient.image.pin`` when it names a release or ``sha-`` tag; each beats ``global.image.tag``
for that one image. That is the only way to move a Kubernetes site to a ``sha-`` tag one of
them was never built at — a StatefulSet rolled onto a tag that does not exist stays down — and
a release tag never needs one.

Cloud (EC2) trust
=================

From the admin workstation — at the release's tag, as for a site — over the same SSH docker
context ``make deploy-trust`` uses:

.. code-block:: bash

   make -C deploy/providers/AWS upgrade-trust-ec2 KIT=<CODE> PROD=<env> [TAG=vX.Y.Z]

``deploy-trust`` remains the first-install verb: its ``seed-trust-data`` step re-seeds
OMOP and Orthanc on the host and its XNAT step resets the archive.

Rolling back
============

Check out the previous release's tag, then run the same verb with it and ``FORCE=1`` (a
release→release move backwards is otherwise refused):

.. code-block:: bash

   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=vX.Y.Z FORCE=1

Releases up to and including v0.7.0 predate this runbook, which changes two things when the
previous release is one of them. Its images were never tagged ``:v<X.Y.Z>`` — the registry
check refuses ``TAG=v0.7.0`` — and its checkout has no upgrade command. Stay on your current
checkout and roll back to the images built from that release's commit instead:

.. list-table::
   :header-rows: 1

   * - Release
     - Image tag
   * - v0.7.0
     - ``sha-23cf331``
   * - v0.6.0
     - ``sha-22d845f``

.. code-block:: bash

   sudo -E make upgrade-onprem-trust KIT=<slot> TAG=sha-23cf331

A ``sha-`` tag carries no release order, so the command does not recognise this as a
downgrade and needs no ``FORCE=1`` — confirm the printed ``site <current> → target sha-…``
yourself. The images then run under the newer checkout's compose files, a pairing no
release was tested in; it is the way back, not a place to stay.

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

``this kit's AES key differs from the hub's``
   The Hub-shared block is stale. Ask the admin for a refreshed kit (the sequence above) and
   replace the block; the upgrade verb runs the checklist first, so it will not proceed until
   the kit carries the hub's key. Once it does, the row turns into a warning that trust-api
   still runs the old key, and the upgrade recreates it.

``Could not ask the registry whether … is published``
   Not a missing image: the registry refused or did not answer — check ``docker login`` for the
   registry and this host's route to it, then re-run. Nothing was changed.

``git could not say which commit … is at``
   git refused to read the checkout — usually "dubious ownership", a checkout owned by another
   user than the one running the upgrade (typically under ``sudo``). Run the printed
   ``git config --global --add safe.directory`` as that user and re-run.

``No running …_xnat-db to back up`` / ``The XNAT database dump failed or is incomplete``
   The upgrade never migrates XNAT without a complete dump. Check the stack
   (``docker stack ps <stack>``) and re-run; nothing was upgraded.

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
