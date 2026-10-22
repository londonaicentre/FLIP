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

.. _admin-backup-and-restore:

##################
Backup and restore
##################

This page lists, for each way of deploying FLIP, where the state that matters lives, what protects it today, and how
to get it back. In short:

- **Central Hub on AWS.** Terraform turns on some protection: database backups kept for 7 days, versioned S3
  buckets, and guards against deleting the user pool, the secret and the buckets. Nothing is copied out of the AWS
  account.
- **Trust nodes.** FLIP schedules no backups. The only backup FLIP takes is the XNAT database dump that an upgrade of a
  Docker Compose site makes. Backing up a trust is the site's job, with its own tooling.
- **Some state cannot be recreated from anywhere**: a trust's kit credentials, the hub database, the user pool, and
  anything a site added to XNAT. Back these up first.

.. contents::
   :local:
   :depth: 1

******************
Central Hub on AWS
******************

*Who: FLIP admin*

What holds state
================

.. list-table::
   :header-rows: 1
   :widths: 18 30 30 22

   * - Store
     - What is in it
     - Protection FLIP configures
     - Can it be recreated?
   * - Hub database (RDS PostgreSQL, ``flip-database``)
     - Projects, cohort queries, models, FL jobs with their metrics and logs, registered trusts (hashes of their
       credentials) and FL kit-slot assignments, user roles and profiles, access requests, audit records.
     - Automated backups kept for **7 days**, on every estate. Production-grade estates only: deletion protection,
       and a final snapshot (``flip-database-final-…``) when Terraform deletes the instance.
     - No.
   * - Model files bucket (``…-model-files-uploads``)
     - Model files uploaded by researchers, before and after the malware scan.
     - Versioning; previous versions expire after 30 days. Deleting an object version requires MFA. Terraform
       refuses to destroy the bucket.
     - No. Researchers would have to upload them again.
   * - FL results bucket (``…-fl-results``)
     - Training outputs that researchers download.
     - Versioning, with no expiry. Deleting an object version requires MFA. Terraform refuses to destroy the bucket.
     - No.
   * - App bundles bucket (``…-app-bundles``)
     - The FL application bundle the hub assembles for each model.
     - Versioning. Terraform refuses to destroy the bucket.
     - Its inputs live elsewhere: the model's files, and the templates inside the flip-api image.
   * - AI Centre bucket (``…-aicentre``)
     - The FL participant kits for every net and slot and, for FLARE, each net's root CA state.
     - Versioning. Terraform refuses to destroy the bucket.
     - No. Without the CA state the net must be provisioned again, which issues new kits to **every** trust.
   * - UI bucket
     - The built web UI.
     - Versioning. Terraform refuses to destroy the bucket.
     - Yes: ``make -C deploy/providers/AWS deploy-ui`` rebuilds and uploads it.
   * - Secrets Manager secret ``FLIP_API``
     - The AES key shared with every trust, and the FL server's internal service key and its hash.
     - A deleted secret can be restored for 30 days.
     - Its values also live in the admin's env file and, for CI, in the GitHub environment (see
       `Restoring the hub`_).
   * - Cognito user pool
     - User accounts, passwords and MFA enrolment.
     - Deletion protection. Terraform refuses to destroy the pool.
     - No. A new pool issues new user IDs, so the roles held in the hub database no longer match anyone.
   * - EFS file system
     - The FL server's and FL API's copy of their FL kits, plus the FL server's working files for runs.
     - None.
     - The kits are copies from the AI Centre bucket. Work in progress is lost.
   * - Admin workstation
     - The hub env file ``.env.<env>`` and every trust kit file ``trust/.env.<CODE>.<env>``.
     - None. Both are gitignored.
     - Partly: Terraform inputs can be recovered from AWS (see `Restoring the hub`_). Trust kit credentials cannot.

**Production-grade** means ``PROD=true`` or ``PROD=lza``. Staging (``stag``, ``lza-stag``) keeps the 7-day backups but
has no deletion protection and no final snapshot, so the database can be torn down freely.

.. note::

   CloudWatch logs are kept for 365 days on production-grade estates and 90 days elsewhere. They are not backed up.

What ``make destroy`` keeps
===========================

.. code-block:: shell

   make -C deploy/providers/AWS destroy PROD=<env>

It lists what it will remove and asks you to type ``yes``. It **keeps** the Cognito user pool, the ``FLIP_API``
secret, every S3 bucket, the ACM certificates and Route 53 records, and the VPC unless you pass
``PRESERVE_VPC=false`` (``PROD=true`` always keeps it). It removes only what it targets: the database (unless
deletion protection stops it) with its parameter and subnet groups, the cloud trust's EC2 instance, the bastion, the
security groups, the ALB, the EC2 IAM role, the key pairs and the SES email templates, plus any resources Terraform
finds depend on them. Everything else, including the CloudWatch logs, stays.

.. warning::

   **The destroy command never takes a final snapshot of the database.** It deletes the instance directly with
   ``--skip-final-snapshot`` rather than through Terraform, and does not ask AWS to keep its automated backups. On
   staging the database is gone at once. On production, deletion protection makes the delete fail; if you remove the
   protection first, take a manual snapshot (below) before you run ``make destroy``.

.. warning::

   **The destroy command deletes a cloud trust's data.** The trust EC2 instance keeps OMOP, Orthanc and XNAT on its
   root disk, which is deleted with the instance. FLIP takes no snapshots of it.

Recommended backups
===================

#. **Take a manual database snapshot** before risky changes, or whenever you need more than 7 days:

   .. code-block:: shell

      aws rds create-db-snapshot --db-instance-identifier flip-database \
          --db-snapshot-identifier flip-database-<yyyymmdd>

   Manual snapshots are kept until you delete them.

#. **Keep the admin workstation's files in a secure store**: the hub env file and every trust kit file. They hold
   secrets, and the kit credentials exist nowhere else (for an EC2 trust, not even on the trust host).

#. **Copy data out of the account if you need to.** FLIP configures no S3 replication, no AWS Backup plan and no
   export of the Cognito user pool. Use your own AWS tooling if your policy requires an off-account copy.

Restoring the hub
=================

The hub database
   AWS restores a snapshot, or a point in time within the last 7 days, into a **new** DB instance. FLIP has no
   runbook for putting that instance behind the hub (flip-api reaches the database through RDS Proxy, and Terraform
   manages the instance by its name), so plan the cut-over with the AWS documentation. Restore onto the release that
   wrote the backup or a later one: flip-api upgrades the schema when it starts, but cannot move it backward.

   A trust registered after the restore point is unknown to the restored database, so the hub rejects its
   credentials. Register it again (``make -C deploy/providers/AWS register-trusts KIT=<CODE> PROD=<env>``), which
   issues new credentials, and send the site its new kit.

A deleted or overwritten S3 object
   Restore the previous version with S3's versioning. In the model files bucket, previous versions last only 30 days.

The ``FLIP_API`` secret
   Within 30 days of deletion, restore it with ``aws secretsmanager restore-secret --secret-id FLIP_API``. After that,
   its values survive only in the admin's env file and the GitHub environment. If the AES key changes, every site needs
   a refreshed kit: see :ref:`admin-upgrading-sites`, "Refreshing site kits".

A lost hub env file
   Rebuild the Terraform inputs from the deployed environment with ``scripts/reconcile_ci_env.py`` (see the
   :gh:`AWS deployment README <deploy/providers/AWS/README.md>`, "Recovering an environment's true values"). It
   recovers Terraform inputs only, not trust, XNAT, Orthanc or OMOP settings.

The Cognito user pool
   FLIP has no way to restore it.

*************************************************
Trust node on Docker Compose (on-premises or EC2)
*************************************************

*Who: site operator* (for a cloud trust on EC2: FLIP admin)

What holds state
================

Every trust store is a directory on the host, bind-mounted into the containers. Your kit file's Host-local profile
names each directory; the table gives the default the production stack uses when the kit leaves it unset.

.. list-table::
   :header-rows: 1
   :widths: 17 25 30 28

   * - Store
     - Kit setting (default)
     - What is in it
     - Can it be recreated?
   * - OMOP database
     - ``OMOP_DATA_DIR`` (``/opt/flip/omop/db_data``)
     - The published mock data, seeded at first install; the OMOP core vocabulary you loaded; anything else your
       site loaded.
     - Mock data: yes, by re-seeding. Vocabulary: reload it from your own export (see
       :ref:`onprem-core-vocabulary`). Anything else: no.
   * - Orthanc (the mock PACS)
     - ``ORTHANC_STORAGE_DIR`` (``/opt/flip/orthanc/orthanc-storage``)
     - The published mock DICOM studies.
     - Yes, by re-seeding.
   * - XNAT database and archive
     - ``XNAT_DATA_DIR`` (``/opt/flip/xnat-trust<N>``)
     - ``xnat-db-data/`` (the database), ``xnat-data/archive/`` (the images) and XNAT's working folders. Holds the
       studies pulled from the PACS for approved projects and anything added at the site, such as labels and
       segmentations. Also ``backups/``, where upgrades write database dumps.
     - Pulled images could be fetched from the PACS again; local additions could not. FLIP has no command to rebuild
       a lost archive.
   * - Cohort snapshot store
     - ``COHORT_SNAPSHOT_STORAGE_DIR`` (``/opt/flip/cohort-snapshots/trust-<N>``)
     - The frozen cohort of each approved project: person and accession IDs, no clinical values.
     - Partly. Re-freezing rebuilds it from the live database, which may no longer match the original cohort.
   * - Kit file
     - ``trust/.env.<CODE>.production`` in the checkout. For an EC2 trust it stays on the admin's workstation.
     - The trust's credentials, the hub-shared settings, your host profile and your local passwords.
     - Hub-shared settings: yes, the admin can refresh them. **Credentials: no** (see below).
   * - Governance document
     - ``ACCESS_POLICY_FILE``
     - Your site's access and disclosure rules, if you set any.
     - Only from your own copy.
   * - FL kit
     - ``FL_KIT_DIR`` (``/opt/flip/fl-kit`` in the kit template)
     - The FL client's certificates and keys.
     - Yes: the admin can package it again.
   * - Images cache
     - ``BASE_IMAGES_DOWNLOAD_DIR`` (``/opt/flip/data/trust-<N>``)
     - Images downloaded from XNAT for FL runs.
     - Yes: it is a cache.
   * - Logs and dashboards
     - Docker volumes ``trust-local-loki-data`` and ``trust-local-grafana-data`` (on EC2: ``/opt/flip/volumes``)
     - Loki logs and Grafana state.
     - Not needed to run the trust.

``<N>`` is ``FL_KIT_SLOT_NUMBER`` from the kit file. To see the exact host paths a running trust uses, inspect its
containers on the trust host:

.. code-block:: shell

   for c in omop-db orthanc data-access-api imaging-api; do
     sudo docker inspect --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' trust<N>-$c-1
   done

**Mock data is re-seedable; site data is not.** The mock OMOP rows and Orthanc studies come from the published trust
dataset, at the version pinned in ``trust/.data_version``, so seeding can always rebuild them. Nothing your site added
on top (the core vocabulary, other OMOP data, XNAT enrichment) can be rebuilt by FLIP.

.. important::

   **Back up the kit file.** The hub keeps only hashes of the trust's credentials and cannot show them again.
   Registering a trust that already exists returns no credentials, so a lost kit file cannot be re-issued as it was
   (see `Restoring a trust node`_).

Recommended backups
===================

*Who: site operator, on the trust host*

FLIP schedules none of these. Take them with the site running (see the warning under `Restoring a trust node`_).

#. **Copy the kit file and the governance document** to a secure store. The kit file holds secrets.

#. **Dump the XNAT database.** This is the dump the upgrade takes, run on its own:

   .. code-block:: shell

      sudo -E make -C trust/xnat xnat-backup KIT=<slot> PROD=true

   It writes ``$XNAT_DATA_DIR/backups/xnat-db-<timestamp>.sql.gz`` and keeps it only if the dump is complete. Copy it
   off the host: the first-install commands delete the whole ``XNAT_DATA_DIR``, dumps included.

#. **Copy the XNAT archive** (``$XNAT_DATA_DIR/xnat-data/archive/``) with your backup tooling, at the same time as the
   dump. A database restored without its archive files, or the other way round, points at images that do not exist.

#. **Copy the cohort snapshot store.** It is small: one record per approved project.

#. **Dump the OMOP database** if your site loaded anything beyond the mock data, with PostgreSQL's ``pg_dump`` against
   ``127.0.0.1:<OMOP_DB_PORT>`` and the ``OMOP_*`` credentials from your kit file.

Instead of steps 2 to 5, you can snapshot the whole data volume (``/opt/flip`` by default) with your platform's volume
or snapshot tooling.

.. note::

   For a cloud trust on EC2, FLIP's Terraform takes no snapshots of the instance's disk. Use AWS's own EBS snapshot
   tooling if the trust holds data you need to keep.

Restoring a trust node
======================

*Who: site operator*

FLIP has no restore command for a trust. What it does tell you:

.. warning::

   **Never run a first-install command on a restored site.** ``make up-onprem-trust``, ``make -C trust up-trust``,
   ``restart-trust`` and ``make -C deploy/providers/AWS deploy-trust`` delete the whole ``XNAT_DATA_DIR`` (archive,
   database and dumps) and may re-seed OMOP and Orthanc. Starting a stopped site with them has the same effect.

- **The upgrade command touches no data.** It recreates the trust containers on their existing directories (see
  :ref:`admin-upgrading-sites`). It does not start a stopped XNAT stack, though: its XNAT step dumps the running XNAT
  database first and stops if there is none. FLIP has no command that starts a stopped XNAT stack without wiping it.
- **XNAT database.** The rollback note in :ref:`upgrade-rolling-back` restores a dump into a fresh ``xnat-db-data``
  directory with the stack stopped. Restore the archive from the same point in time.
- **Directory owners.** The containers need their directories owned as they expect: XNAT's tree by ``1001:1001``,
  Orthanc's by ``999``, the cohort snapshot store by ``1000:1000``. The upgrade fixes the snapshot store's owner
  itself, and checks XNAT's and prints the ``chown`` to run.
- **Lost cohort snapshot store.** Ask the FLIP admin to re-freeze the site's projects with
  ``POST /api/projects/{project_id}/cohort-snapshots?include_frozen=true``. Sites that still hold their cohort keep it;
  yours freezes afresh from its live database. Until then your site refuses the project's training and imaging pulls.
- **Lost OMOP core vocabulary.** Load it again from your own export: :ref:`onprem-core-vocabulary`.
- **Lost kit file.** The admin can rebuild the hub-shared settings (``make sync-trust-kit KIT=<CODE> PROD=<env>`` from
  the repo root on the admin workstation), but not the credentials.

.. warning::

   **The only way FLIP gives a trust new credentials is to delete it on the hub and register it again**
   (``make -C deploy/providers/AWS delete-trust NAME=<trust-name> PROD=<env>``, then ``register-trusts``). Deleting a
   trust also deletes its records on the hub: its task queue, project and model links, FL job participation, metrics
   and logs, cohort results, cohort snapshot and imaging status, and user roles scoped to it.

************************
Trust node on Kubernetes
************************

*Who: site operator*

What holds state
================

The Helm chart keeps each store on a PersistentVolumeClaim. Whether it survives
``make -C trust/deploy/helm undeploy`` (``helm uninstall``) depends on how the chart creates it.

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - Volume
     - What is in it
     - After ``undeploy``
   * - ``omop-db`` (StatefulSet)
     - The OMOP database.
     - Kept.
   * - ``xnat-db`` (StatefulSet)
     - The XNAT database.
     - Kept.
   * - ``loki`` (StatefulSet)
     - Logs.
     - Kept.
   * - ``<release>-orthanc``
     - The mock PACS store.
     - **Deleted.**
   * - ``<release>-xnat-web``
     - The XNAT archive and working folders.
     - **Deleted.**
   * - ``<release>-cohort-snapshots``
     - The frozen cohort of each approved project.
     - **Deleted.**
   * - ``<release>-shared-images``
     - The images cache.
     - **Deleted.**
   * - ``<release>-grafana``
     - Grafana state.
     - **Deleted.**

Outside the cluster's volumes, the FL kit stays on the node where ``stage-kit`` put it (``flClient.kitHostPath``),
and the kit file stays on your workstation; the chart's Secret is generated from it.

.. warning::

   **Undeploying deletes the PACS store, the XNAT archive and the cohort snapshots.** A later ``deploy`` comes back
   with the OMOP and XNAT databases but an empty archive, and XNAT then points at images that no longer exist.
   Snapshot those volumes first if the data matters.

Recommended backups
===================

FLIP provides none. Use your cluster's volume snapshot or backup tooling for the PersistentVolumes, and keep the kit
file in a secure store as for Compose.

Restoring a Kubernetes node
===========================

- **Restore the XNAT database and archive volumes from the same point in time.**
- **A restored** ``xnat-db`` **keeps its old superuser password.** The ``xnat`` application role's password is
  re-applied at every pod start, but the ``postgres`` superuser's is set only when the database is first created. See
  the :gh:`Helm chart README <trust/deploy/helm/README.md>` for rotating it by hand.
- **Mock-data seeding runs on every upgrade** while ``trustData.seed.enabled`` is on, replacing the listed mock
  projects' rows. Turn it off for a site whose stores hold real data only.

***********************
Local development stack
***********************

Nothing in the development stack is backed up. What each clean-up command removes:

.. list-table::
   :header-rows: 1
   :widths: 34 22 22 22

   * - State
     - ``make down``
     - ``make clean``
     - ``make clean-object-store``
   * - Hub database (``flip-db``)
     - **Lost**
     - **Lost**
     - Kept
   * - Object store (``./object-store/``)
     - Kept
     - Kept
     - **Deleted**
   * - Trust OMOP and Orthanc directories
     - Kept
     - Kept
     - Kept
   * - Kit files (``trust/.env.<CODE>.development``)
     - Kept
     - Kept
     - Kept

All three run from the repo root. Things to know:

- **The hub database lives inside its container.** ``flip-db`` has no named volume, so removing the container
  (``make down`` does) loses it; the next ``make up`` starts an empty hub database and registers the trusts again.
- **Every** ``make up`` **resets XNAT.** It deletes each trust's XNAT data directory and starts it empty.
- ``make clean`` also removes locally built images and runs ``docker system prune -f``, which leaves volumes alone.
- **Keycloak** keeps nothing: it re-imports ``deploy/keycloak/flip-realm.json`` at every start.

.. seealso::

   - Upgrading a site, and the first-install commands to avoid: :doc:`admin-upgrading-sites`
   - Deploying the hub on AWS: :doc:`/deploy-flip/deploy-central-hub-aws`
   - Onboarding a site: :doc:`/deploy-flip/onboarding-a-trust`
