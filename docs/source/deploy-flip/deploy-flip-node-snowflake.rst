.. _deploy-flip-node-snowflake:

########################################
Deploy a FLIP node on Snowflake Snowpark
########################################

A FLIP node on Snowflake runs the trust on Snowpark Container Services (SPCS): the same six
services as any other node (trust-api, imaging-api, data-access-api, the FL client, XNAT and
Orthanc), with the Trust's OMOP living in native Snowflake tables instead of a PostgreSQL
container. Two containers are absent: the OMOP database and the Loki/Grafana stack are replaced by
Snowflake's own tables and event table.

Everything is built from code in this repository, in three Terraform roots plus a node directory:

.. list-table::
   :header-rows: 1

   * - Path
     - What it is
   * - ``trust/deploy/terraform/snowflake-identity``
     - The roles and the deploy user. Applied once, by a person who administers users
   * - ``trust/deploy/terraform/snowflake-platform``
     - The warehouse, the egress integration, the event table and its views. Applied rarely, by a person
   * - ``trust/deploy/terraform/snowflake``
     - The workload: database, schemas, pools, stages, image repository, grants. Applied with the deploy key
   * - ``trust/deploy/snowflake``
     - The node itself: image mirroring, secrets, service specs, ``make up``, the self-test

This page is the operator's runbook: what to run, in what order, and what it should say. The
reasoning behind each shape lives in the ``README.md`` beside the code. Every step is run by a
person from a checkout; FLIP's CI checks this code but deploys no Snowflake node.

For a node on a Trust's own host see :doc:`/deploy-flip/deploy-flip-node-on-prem`; the same trust can run
instead as the ``flip-trust`` Helm chart on Kubernetes (:doc:`/deploy-flip/deploy-flip-node-kubernetes`) or on an
Azure VM (:doc:`/deploy-flip/deploy-flip-node-azure`).

.. contents:: On this page
   :local:
   :depth: 2

*************
What you need
*************

- **A Snowflake account, Enterprise edition or above** (SPCS is not in Standard). The account's
  region is the node's region: clinical data never leaves it.
- **`snow` (the Snowflake CLI) and OpenTofu or Terraform** on the machine that deploys. `snow` is the
  only thing that talks to SPCS; Terraform only manages account and database objects.
- **Docker**, to copy the images into the account's image repository.
- **A trust code**, a short upper-case name (``GSTT``, ``SF1``) from which every object name is
  derived. It is the only input the three roots share; they never read each other's state.
- **A kit file**, ``trust/.env.<CODE>.<HUB>``, from the hub admin (see :doc:`/deploy-flip/onboarding-a-trust`).

A **trial account cannot run a hub-connected node**: Snowflake excludes External Network Access
from trial accounts, so no service can reach a hub or an FL server. A trial account can still run
the whole node hubless, which is what the self-test in this page exercises. Business Critical is
the recommended edition for production: PrivateLink and Tri-Secret Secure are the controls an NHS
information-governance review usually asks for, and neither exists on lower editions.

***************
The three roots
***************

Each root is applied by a different identity, which is the point: a compromised deploy key cannot
mint an identity, open an egress path or change an account setting.

.. list-table::
   :header-rows: 1

   * - Root
     - Who applies it
     - Role
     - How often
   * - Identity
     - a person, with MFA
     - the role that administers users (``USERADMIN``)
     - once per trust
   * - Platform
     - a person, with MFA
     - ``ACCOUNTADMIN``, or the role that owns objects (``SYSADMIN``) with ``account_admin = false``
     - rarely
   * - Workload
     - the deploy user, with its key pair
     - ``FLIP_<CODE>_DEPLOY``
     - on change
   * - The node
     - the deploy user, with its key pair
     - ``FLIP_<CODE>_DEPLOY``
     - every deploy

The workload root and the node run as ``FLIP_<CODE>_DEPLOY``, a service user with a key pair whose
account privileges are exactly ``CREATE DATABASE``, ``CREATE COMPUTE POOL`` and ``EXECUTE TASK``,
plus ownership of everything it created. It holds no ``CREATE ROLE``, ``CREATE USER``,
``CREATE INTEGRATION``, ``MANAGE GRANTS``, no account policy and no system role.

**A FLIP node changes nothing account-wide in an account that holds other things.** With
``account_admin = false`` the platform root applies only objects named for the trust. What it then
leaves out (the resource monitor, the account event table, the account password and session
policies) is listed in the platform root's README, with the consequence of each.

**************
Infrastructure
**************

Each root reads ``terraform.<env>.tfvars`` (``env`` follows ``HUB``: ``development`` when unset,
``stag``, ``prod``), copied from the ``terraform.tfvars.example`` beside it. The tfvars and the
state are gitignored. No target skips the confirmation prompt.

First generate the deploy user's key pair. Whoever will run the workload root keeps the private
half; only the public half is an input:

.. code-block:: bash

   openssl genrsa 2048 | openssl pkcs8 -topk8 -v2 aes-256-cbc -out flip_deploy.p8
   openssl rsa -in flip_deploy.p8 -pubout | grep -v -- '-----' | tr -d '\n'   # deploy_user_public_key

Then the three roots, in this order:

.. code-block:: bash

   # 1. Identity: the FLIP_<CODE>_* roles and the deploy user. As the role that administers users.
   make -C trust/deploy/terraform/snowflake-identity plan apply HUB=stag

   # 2. Platform: the warehouse, the egress integration, the event table views. As a person.
   make -C trust/deploy/terraform/snowflake-platform plan apply HUB=stag

   # 3. Workload: database, schemas, pools, stages, repository, grants. As the deploy user.
   make -C trust/deploy/terraform/snowflake plan apply HUB=stag

The identity and platform roots sign in as a person; how (a programmatic access token restricted to
one role, or a key pair) is in the platform root's README under *Credentials*, with the sign-in
errors a first run tends to hit under *Troubleshooting*. The platform apply prints a banner and asks
twice.

A change to a role or to the egress list is a re-apply of the root that owns it. Apply the three
roots from the same commit as the node: the node names roles the identity root creates, and a node
deployed ahead of them fails at ``make up`` naming the role that does not exist.

***************
The node itself
***************

The node lives in ``trust/deploy/snowflake``, beside ``helm/``. Add a `snow` connection for the
deploy user (``snow connection add``, key-pair authentication, role ``FLIP_<CODE>_DEPLOY``) and name
it in ``SNOW_CONNECTION``: every target that reaches Snowflake refuses to run without it, rather than
fall back to a default. A first deployment runs, in this order:

.. code-block:: bash

   export SNOW_CONNECTION=<name>
   make render         KIT=GSTT HUB=stag   # one service spec per service, from the kit
   make mirror-images  KIT=GSTT HUB=stag   # GHCR -> the account's image repository, by digest
   make sync-kit       KIT=GSTT HUB=stag   # SECRET objects, the FL kit, the governance document
   make up             KIT=GSTT HUB=stag   # CREATE (or ALTER and RESUME) every service
   make xnat-configure KIT=GSTT HUB=stag   # register XNAT's DICOM receiver and the PACS
   make job-status     KIT=GSTT HUB=stag JOB=xnat-configure
   make selftest       KIT=GSTT HUB=stag   # the node proves itself; see below

and the rest of the lifecycle:

.. code-block:: bash

   make status         KIT=GSTT HUB=stag  # services and compute pools
   make suspend        KIT=GSTT HUB=stag  # suspend every service and pool: stop paying
   make up             KIT=GSTT HUB=stag  # ...and bring a suspended trust back
   make down           KIT=GSTT HUB=stag  # drop every service (stages and volumes survive)
   make seed-orthanc   KIT=GSTT HUB=stag  # mocked PACS only: push the DICOM set on the SEED stage
   make smoke-cstore   KIT=GSTT HUB=stag  # a real C-STORE through the mocked PACS

Add ``DRY_RUN=true`` to any target that talks to Snowflake to print the SQL instead of running it.

**The OMOP tables** are loaded from the trust's own source, or for a test node seeded with
``make -C trust/omop-db seed-omop-snowflake`` (see ``trust/omop-db/README.md``). A re-seed reloads
inside one transaction and fails if the row counts it loaded do not match what it staged. The OMOP then
holds rows, so ``ensure-seeded-snowflake`` would skip it: re-run ``seed-omop-snowflake``.

**The self-test is the gate.** ``make selftest`` runs a job inside the account, waits for it, prints
its log and exits non-zero unless every check passed. It checks the node from the inside: every API
answers, data-access-api reaches OMOP on Snowflake, a cohort query returns statistics, a write is
refused by validation and by the database, and a cohort can be frozen and removed. It prints one
``SELFTEST PASS`` or ``SELFTEST FAIL`` line per check and ends with ``SELFTEST ALL PASS``. A
``SELFTEST WARN`` line is advice and does not fail it (see `Network and security`_).
``make smoke-cstore`` waits for its verdict in the same way.

**Order matters on a first install.** ``render`` runs before ``up`` (``up`` deploys what was
rendered, not what the kit says now), and ``xnat-configure`` runs before any retrieval: until it
has, XNAT has registered no DICOM receiver and rejects every C-STORE, so the trust answers HTTP and
retrieves nothing.

``make render`` writes ``rendered/<CODE>.<env>/``, one YAML per service plus ``manifest.json``.
Both are gitignored. The manifest is what ``up`` reads to know what to create, and as which role.

**Images from a branch.** ``mirror-images`` pulls the kit's tags from GHCR. To deploy code that has
no published image, build the image locally for ``linux/amd64``, tag it as GHCR would, and run
``make mirror-images MIRROR_PULL=false``, which pushes what the local Docker daemon holds.

*******************
Connecting to a hub
*******************

A hubless node needs nothing here. To join a hub:

1. The hub admin registers the trust and hands over the kit and the FL kit
   (:doc:`/deploy-flip/onboarding-a-trust`).
2. In the platform root set ``enable_hub_egress = true`` with ``hub_api_host`` and
   ``fl_server_endpoints``, and apply it. This creates ``FLIP_<CODE>_HUB_EAI``, the only way out of
   the account.
3. Set ``HUB_EGRESS=true`` in the kit file, then ``make render`` and ``make up``. The kit is the
   only place that holds it: every ``up`` sets or clears the integration from the kit, and
   ``HUB_EGRESS=`` on the make line is refused.
4. Give the hub's operators the address the node dials out from. Snowflake publishes the egress
   ranges of each region, and they are shared by every account in that region, so the hub's FL
   listener allows a range, not one address.

``trust-api`` then logs a heartbeat answered with 200, and the FL client logs that it registered.

*******
Upgrade
*******

Moving a node to a new FLIP release is the same three steps as a first deploy, with the kit's
``DOCKER_TAG`` (and ``DOCKER_FL_TAG`` for the FL client) set to the release:

.. code-block:: bash

   make mirror-images KIT=GSTT HUB=stag
   make render        KIT=GSTT HUB=stag
   make up            KIT=GSTT HUB=stag

``up`` alters each service in place, so xnat-db and Orthanc keep their volumes. It restarts the FL
client: run it when no training round is in flight. An infrastructure change is a ``plan`` and
``apply`` of the root that owns it, in the same order as the first install.

*******
Destroy
*******

In this order, because a pool will not drop under a running service:

.. code-block:: bash

   make down          KIT=GSTT HUB=stag
   make suspend       KIT=GSTT HUB=stag
   make -C trust/deploy/terraform/snowflake          destroy HUB=stag
   make -C trust/deploy/terraform/snowflake-platform destroy HUB=stag
   make -C trust/deploy/terraform/snowflake-identity destroy HUB=stag

Stages and block volumes hold the trust's images, the FL kit and XNAT's archive. ``make down`` and
``make suspend`` leave them; ``destroy`` on the workload root removes them.

********************
Network and security
********************

This section is for the Trust's network and security teams.

**Nothing is published.** Every service endpoint is internal to the account; there is no public
endpoint in any rendered spec, and no admin or bastion host. An operator reaches the trust through
Snowflake, which means signing in to the account with MFA.

**Egress is one closed list.** A trust reaches the hub API and the FL server, nothing else. The
list is a network rule with ``HOST_PORT`` entries, wrapped in a single external access integration,
``FLIP_<CODE>_HUB_EAI``, created by the platform root and off by default. Two services attach it,
``trust-api`` (polls the hub) and each ``fl-client-net-N`` (reaches the FL server); no spec carries
a host or a rule of its own, so there is no second allowlist to keep in step. ``up`` refuses before
creating anything if the integration is missing, and says which one it looked for.

**Secrets never touch Terraform, a spec, a log or git.** The kit file is the Trust's only source of
credentials. ``make sync-kit`` writes them into Snowflake ``SECRET`` objects, feeding the SQL to
``snow`` on stdin so no value appears in a command line, an argument list or a shell history;
services receive the objects, not the values. An existing secret is altered, never replaced:
replacing one drops every grant on it and the next deploy fails on a privilege error about an
object that plainly exists.

**The FL client is the service that runs someone else's code, and it is treated as such.** SPCS
gives every container a session token for the role that owns its service, so the FL client's
services are created as a role of their own, ``FLIP_<CODE>_FL``. That role can read and write the
images stage, read the FL kit and read the trust-internal key. It has no OMOP role, no warehouse and
no other stage or secret, so researcher code cannot query OMOP past data-access-api, and cannot
write the governance document or a frozen cohort. The client mounts its own net's slice of the
images tree and holds no AES key. One limit remains: a stage grant covers the whole stage, so
through SQL that role can still list the file names of another FL net's images. A trust that runs
more than one FL net should treat that as open until each net has its own stage and role.

**A site privacy filter is refused, not dropped.** The FL site privacy policy (``FL_SITE_PRIVACY_*``
or the governance document's ``[fl_privacy]`` section) is not applied on Snowflake yet, so
``make render`` refuses a kit that sets one instead of deploying a client without it.

**A cohort query is checked before it runs, for Snowflake's own syntax too.** data-access-api
passes a single ``SELECT`` over the trust's OMOP schema. On Snowflake it also refuses time travel
(``AT``, ``BEFORE``, ``CHANGES``), which would keep serving a withdrawn patient, the AI and Cortex
functions, which would send patient values to a model, and any read from a stage.

**An account setting FLIP recommends and does not set.** With ``PREVENT_UNLOAD_TO_INLINE_URL``
off, a session that can read OMOP and use a warehouse can export rows to a bucket named in the
statement, and the node having no internet route does not stop that, because Snowflake's own
compute does the export. A researcher's query and the FL client's role cannot; the deploy key and
data-access-api's own session can. The setting is account-wide, so it is the account
administrator's to turn on (``ALTER ACCOUNT SET PREVENT_UNLOAD_TO_INLINE_URL = TRUE``). The
self-test prints a ``SELFTEST WARN`` line while it is off.

**Clinical data stays in the account's region.** The account's region is the node's region: there
is no copy of OMOP or of the archive anywhere else, and no telemetry leaves the account (logs go to
the account's own event table). Data residency is therefore the account's, which is why the region
is chosen when the account is created and cannot be changed afterwards.

**Which edition.** Enterprise is the licence SPCS needs. Business Critical is the production
recommendation, for PrivateLink (a private network path to the account) and Tri-Secret Secure (a
customer-held key to decrypt it), and for the compliance posture an NHS IG review looks for.

****
Cost
****

SPCS bills each compute-pool node while it runs, and a resource monitor does **not** cover compute
pools. The guards are pool auto-suspend from the workload root, ``make suspend`` for a person, and
a nightly suspend task inside the account. An idle trust that nobody suspended is the expensive
failure mode: suspend it when a test is over, and bring it back with ``make up``.

***************
Troubleshooting
***************

**``up`` fails: the integration does not exist.** The specs attach
``FLIP_<CODE>_HUB_EAI`` but the platform root was applied with ``enable_hub_egress = false``.
Either apply it with egress (and the two host settings), or set ``HUB_EGRESS=false`` in the kit and
deploy a hubless trust.

**``render`` refuses the kit: it asks for an FL site privacy filter.** ``FL_SITE_PRIVACY_*`` in the
kit, or an ``[fl_privacy]`` section in its governance document, is not applied on Snowflake yet:
nothing there renders the FL client's ``privacy.json``. Rather than start the client without the
filter the kit asks for, ``render`` (and so ``up``) stops and names the setting. Remove it to deploy
with no site filter, or run the trust on Compose or Kubernetes.

**``up`` fails: role ``FLIP_<CODE>_FL`` does not exist.** The identity root was applied from an
older commit. Re-apply the identity, platform and workload roots, then ``make sync-kit`` (it grants
that role its one secret) and ``make up``.

**A service never leaves PENDING.** ``make status`` names the state; ``make job-status`` or the
event table shows the container's own log. The usual causes are an image that is not in the
account repository yet (``make mirror-images`` runs first and pushes by digest), a secret the
service's role cannot read, a pool still suspended, or a pool too small for the service's memory
request (``pool_instance_family`` and ``pool_max_nodes`` in the workload root).

**The FL client asks for a pool that does not exist.** The kit's ``NUM_AVAILABLE_GPUS`` is above
zero, which sends the client to the GPU pool. Set it to ``0``, or apply the workload root with
``enable_gpu_pool = true``.

**XNAT answers HTTP and retrieves nothing.** ``make xnat-configure`` has not run. It registers the
DICOM receiver and the PACS, and a retrieval cannot complete without it.

**The hub shows the FL client as disconnected.** The egress integration is attached but the FL
server does not admit the node's address. Send the hub's operators the Snowflake egress ranges of
the account's region.

**``GRANT`` or ``REVOKE`` in Snowsight appears to do nothing.** Run the statement on its own:
"Run all" executes only the first statement in a worksheet. A ``USE ROLE`` at the top of the
worksheet is a common way to lose the statement that mattered.

********
See also
********

- ``trust/deploy/snowflake/README.md``: the node's shapes, and why each service looks as it does.
- ``trust/deploy/terraform/snowflake{,-identity,-platform}/README.md``: each root's objects and
  privileges.
- :doc:`/deploy-flip/onboarding-a-trust`: what the hub admin does before any of this.
