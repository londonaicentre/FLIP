.. _deploy-flip-node-snowflake:

########################################
Deploy a FLIP node on Snowflake Snowpark
########################################

A FLIP node on Snowflake runs the trust on Snowpark Container Services (SPCS): the same six
services as any other node (trust-api, imaging-api, data-access-api, the FL client, XNAT and
Orthanc), with the Trust's OMOP living in native Snowflake tables instead of a PostgreSQL
container. Two containers are absent and one is new: the OMOP database and the Loki/Grafana stack
are replaced by Snowflake's own tables and event table, and the NIfTI converter runs as a job
service instead of inside XNAT.

Everything is built from code in this repository, in three Terraform roots plus a node directory:

.. list-table::
   :header-rows: 1

   * - Path
     - What it is
   * - ``trust/deploy/terraform/snowflake-bootstrap``
     - The break-glass tier: account-level objects, applied by a person once, then rarely
   * - ``trust/deploy/terraform/snowflake-platform``
     - Roles, the deploy user, the event table and views, the egress integration: applied by CI
   * - ``trust/deploy/terraform/snowflake``
     - The workload tier: database, schemas, pools, stages, image repository, nightly task
   * - ``trust/deploy/snowflake``
     - The node itself: image mirroring, secrets, service specs, ``make up``, the self-test

This page is the operator's runbook: what to run, in what order, and what it should say. The
reasoning behind each shape lives in the ``README.md`` beside the code.

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
- **`snow` (the Snowflake CLI) and OpenTofu** on the machine that deploys. `snow` is the only thing
  that talks to SPCS; Terraform/OpenTofu only ever manages account and database objects.
- **A trust code**, a short upper-case name (``GSTT``, ``SFTEST``) from which every object name is
  derived. It is the only input the three roots share; they never read each other's state.
- **A kit file**, ``trust/.env.<CODE>.<HUB>``, from the hub admin (see :doc:`/deploy-flip/onboarding-a-trust`).

A **trial account cannot run a hub-connected node**: Snowflake excludes External Network Access
from trial accounts, so no service can reach a hub or an FL server. A trial account can still run
the whole node hubless, which is what the self-test in this page exercises. Business Critical is
the recommended edition for production: PrivateLink and Tri-Secret Secure are the controls an NHS
information-governance review usually asks for, and neither exists on lower editions.

***************
The three tiers
***************

Each tier is applied by a different identity, which is the point: a compromised deploy key cannot
mint an identity, open an egress path or change an account policy.

.. list-table::
   :header-rows: 1

   * - Tier
     - Who applies it
     - Role
     - How often
   * - Bootstrap
     - a person, in Snowsight, with MFA
     - ``ACCOUNTADMIN``
     - once per account
   * - Platform
     - CI, on merge
     - ``FLIP_PLATFORM_CI``
     - on change
   * - Workload
     - CI, on merge
     - ``FLIP_<CODE>_DEPLOY``
     - on change
   * - The node
     - CI, on merge
     - ``FLIP_<CODE>_DEPLOY``
     - every deploy

The workload and node tiers run as ``FLIP_<CODE>_DEPLOY``, a service user with a key pair whose
account privileges are exactly ``CREATE DATABASE``, ``CREATE COMPUTE POOL``, ``BIND SERVICE
ENDPOINT`` and ``EXECUTE TASK``, plus ownership of everything it created. It holds no ``CREATE
ROLE``, ``CREATE USER``, ``CREATE INTEGRATION``, ``MANAGE GRANTS``, no account policy and no system
role. Humans sign in with MFA; no automation ever holds ``ACCOUNTADMIN``.

*********
Bootstrap
*********

Once per account. Terraform cannot grant itself ``ACCOUNTADMIN``, so the operator grants it for the
length of one apply and takes it away again.

.. code-block:: bash

   cd trust/deploy/terraform/snowflake-bootstrap
   cp terraform.tfvars.example terraform.development.tfvars    # gitignored
   make init
   make jit-grant        # prints the GRANT to run in Snowsight, as a person user with MFA
   make plan             # read the Plan line: it creates the CI role, users and policies
   make apply
   make jit-revoke       # prints the REVOKE; run it, then check from your own session

Run each SQL statement on its own (place the cursor on the line, Ctrl/Cmd+Enter). Snowsight's
"Run all" executes only the first statement of a worksheet, which silently leaves the operator
holding a role the revoke was meant to remove.

What this leaves behind: ``FLIP_PLATFORM_CI`` and its read-only plan twin, the two OIDC service
users CI logs in as, the trust warehouse with its resource monitor, the ``EVENT_TABLE`` parameter,
the account policies, and the egress rule with its external access integration (off).

***************************
Platform and workload tiers
***************************

Both are ordinary OpenTofu roots, and both are applied by CI on merge once the account is
connected to GitHub (see `Continuous integration`_). To apply one by hand, use the same commands
with the same identity, from a checkout at the commit you mean to deploy:

.. code-block:: bash

   cd trust/deploy/terraform/snowflake-platform
   make plan && make apply     # as FLIP_PLATFORM_CI

   cd ../snowflake
   make plan && make apply     # as FLIP_<CODE>_DEPLOY, i.e. FLIP_<CODE>_DEPLOY_USER

Do not apply the platform root from a branch that is not the one the deploy runs from: it holds
the roles the deploy identity holds, and a stale apply can leave a deploy unable to name its own
service role.

From the node's directory the same two roots are reachable through one Makefile:

.. code-block:: bash

   make plan     KIT=GSTT HUB=stag                  # the workload root
   make plan     KIT=GSTT HUB=stag TF_ROOT=platform # the break-glass tier

The bootstrap root is deliberately not reachable this way.

***************
The node itself
***************

The node lives in ``trust/deploy/snowflake``, beside ``helm/``. Its targets are, in the order a
first deployment uses them:

.. code-block:: bash

   make render        KIT=GSTT HUB=stag   # one service spec per service, from the kit
   make mirror-images KIT=GSTT HUB=stag   # GHCR -> the account's image repository, by digest
   make sync-kit      KIT=GSTT HUB=stag   # SECRET objects, the FL kit, the governance document
   make up            KIT=GSTT HUB=stag   # CREATE (or ALTER) every service
   make status        KIT=GSTT HUB=stag   # services and compute pools
   make selftest      KIT=GSTT HUB=stag   # the node proves itself; see below

and the rest of the lifecycle:

.. code-block:: bash

   make down           KIT=GSTT HUB=stag  # drop every service (stages and volumes survive)
   make suspend        KIT=GSTT HUB=stag  # suspend every service and pool: stop paying
   make xnat-configure KIT=GSTT HUB=stag  # register XNAT's DICOM receiver and the PACS
   make seed-orthanc   KIT=GSTT HUB=stag  # first install only: push the DICOM set to Orthanc
   make smoke-cstore   KIT=GSTT HUB=stag  # a real C-STORE through the mock PACS
   make reconvert-nifti KIT=GSTT HUB=stag # re-run the converter over archived scans

Add ``DRY_RUN=true`` to any target that talks to Snowflake to print the SQL instead of running it.

**The self-test is the gate.** ``make selftest`` runs inside the account, as a job service, and
checks the node from the inside: every service reachable, the OMOP reader answering, XNAT
registered, the PACS reachable, the converter launching. It prints one ``SELFTEST`` line per check
and finishes with ``SELFTEST ALL PASS``, which is the only line a script should test for. CI
deploys are gated on exactly that line.

**Order matters on a first install.** ``render`` must run before ``up`` (``up`` deploys what was
rendered, not what the kit says now), ``xnat-configure`` must run before any retrieval (until it
has, XNAT has registered no DICOM receiver and rejects every C-STORE, so the trust answers HTTP and
retrieves nothing), and the converter's first job needs a real session in the archive.

``make render`` writes ``rendered/<CODE>.<env>/``, one YAML per service plus ``manifest.json``.
Both are gitignored: they contain the rendered specs, and the manifest is what ``up`` and the CI
deploy read to know what to create.

*******
Upgrade
*******

Moving a node to a new FLIP release is the same three steps as a first deploy, with the images
pinned to the release instead of the development build:

.. code-block:: bash

   make mirror-images KIT=GSTT HUB=stag DOCKER_TAG=v1.4.0
   make render        KIT=GSTT HUB=stag
   make up            KIT=GSTT HUB=stag

A deploy that would restart a running FL client with a different spec is refused rather than
killing a training round in flight: see the FL gate under `Continuous integration`_. An
infrastructure change is a ``plan`` and ``apply`` of the root that owns it, in the same order as
the first install.

*******
Destroy
*******

In this order, because a pool will not drop under a running service:

.. code-block:: bash

   make down          KIT=GSTT HUB=stag
   make suspend       KIT=GSTT HUB=stag
   cd trust/deploy/terraform/snowflake && make destroy
   cd ../snowflake-platform           && make destroy

The bootstrap root is destroyed last, by the operator with ``ACCOUNTADMIN``, and it is worth
pausing first: it owns the account policies and the CI identities, so destroying it while CI still
runs leaves CI unable to log in with an error that reads like a credential problem.

Stages and block volumes hold the trust's images, the FL kit and XNAT's archive. ``make down`` and
``make suspend`` leave them; ``terraform destroy`` on the workload root removes them.

**********************
Continuous integration
**********************

Four workflows drive the account from GitHub. None holds a Snowflake password or key: each logs in
with the GitHub OIDC token, which Snowflake maps to a service user through the environment name.

.. list-table::
   :header-rows: 1

   * - Workflow
     - When
     - Identity
     - What it does
   * - ``snowflake_trust_plan.yml``
     - pull request
     - ``FLIP_CI_PLAN_USER`` in ``sf-stag-plan``
     - ``plan -refresh=false`` against stored state
   * - ``snowflake_trust_apply.yml``
     - merge to develop
     - ``FLIP_CI_APPLY_USER`` in ``sf-stag``
     - ``apply`` of both roots
   * - ``snowflake_trust_drift.yml``
     - nightly
     - as the apply, on develop
     - a refreshed plan; one issue while it drifts
   * - ``snowflake_trust_deploy.yml``
     - merge to develop
     - ``FLIP_CI_APPLY_USER`` in ``sf-stag``
     - mirror, render, sync, ``up``, self-test

Three properties of that arrangement are worth knowing before changing it:

- **A pull-request plan does not read the account.** A refreshed plan needs grants that amount to
  write access (reading a task needs ``MANAGE GRANTS``; a read-only view of a user hides
  properties, so the plan proposes replacing it), and no code in a pull request should run with
  that. So the PR plan reports the change against the state the last apply left, and the drift
  workflow, which runs trusted code on the default branch, is the one that compares against the
  live account.
- **The deploy is gated on the self-test, and holds for FL.** It runs ``xnat-configure``,
  ``smoke-cstore`` and ``selftest``, and passes only on ``SELFTEST ALL PASS``. Before touching
  anything it checks whether a *running* FL client would get a different spec, and stops if so:
  each deploy stamps every service with the SHA-256 of the spec it was given, so the check is a
  comparison of two hashes rather than a parse of Snowflake's normalised spec, and a running client
  with no stamp counts as changed. Release a hold by re-dispatching with ``fl_quiesced=true``,
  which says that no round is in flight.
- **Every deploy ends by scheduling its own stop.** Whatever the outcome, it replaces a one-shot
  Snowflake task that suspends every service and pool ``SF_TEST_WINDOW_HOURS`` later (default two)
  and then suspends itself. Snowflake's scheduler fires it, not the runner, so a failed or
  abandoned run cannot leave compute billing.

Nothing runs until the repository variable ``SF_CI_ENABLED`` is ``true``. The ``sf-stag``
environment holds:

.. list-table::
   :header-rows: 0

   * - ``SNOWFLAKE_ORGANIZATION_NAME``, ``SNOWFLAKE_ACCOUNT_NAME`` (secrets)
     - The account identifier
   * - ``SF_KIT_FILE`` (secret)
     - The Trust's whole kit file, ``trust/.env.<CODE>.stag``
   * - ``SF_TRUST_CODE``, ``SF_WAREHOUSE`` (variables)
     - The trust code and its warehouse
   * - ``SF_TEST_WINDOW_HOURS`` (variable)
     - Hours the trust stays up after a deploy, default 2
   * - ``SF_NOTIFY_USERS`` (variable)
     - Space-separated GitHub users to mention in the report

********************
Network and security
********************

This section is for the Trust's network and security teams.

**Nothing is published.** Every service endpoint is internal to the account; there is no public
endpoint in any rendered spec, and no admin or bastion host. An operator reaches the trust through
Snowflake, which means signing in to the account with MFA.

**Egress is one closed list.** A trust reaches the hub API and the FL server, nothing else. The
list is a network rule with ``HOST_PORT`` entries, wrapped in a single external access integration,
``FLIP_<CODE>_HUB_EAI``, created by the platform tier and off by default. Two services attach it,
``trust-api`` (polls the hub) and each ``fl-client-net-N`` (reaches the FL server); no spec carries
a host or a rule of its own, so there is no second allowlist to keep in step. Turning it on is one
setting in the platform tier (``enable_hub_egress``, with ``hub_api_host`` and
``fl_server_endpoints``) and one at render time (``HUB_EGRESS=true``); ``up`` refuses before
creating anything if the integration is missing, and says which one it looked for.

**Secrets never touch Terraform, a spec, a log or git.** The kit file is the Trust's only source of
credentials. ``make sync-kit`` writes them into Snowflake ``SECRET`` objects, feeding the SQL to
``snow`` on stdin so no value appears in a command line, an argument list or a shell history;
services receive the objects, not the values. An existing secret is altered, never replaced:
replacing one drops every grant on it and the next deploy fails on a privilege error about an
object that plainly exists.

**The FL client is the service that runs someone else's code, and it is treated as such.** It
mounts its own net's slice of the images tree and no other, it holds no AES key (so it cannot pass
data-access-api's cohort-admin gate and cannot redefine a project's frozen cohort), and its
Snowflake token carries the service role rather than the deploy role. Services are created *as*
that role for exactly this reason: an SPCS token carries the role that owns the service. The
NIfTI converter, which needs to create a job, runs through a procedure the deploy role owns, called
by the service role, so no container ever holds the rights to create one.

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
pools. The guards are therefore at every level: pool auto-suspend from the workload root, ``make
suspend`` for a person, a nightly suspend task inside the account, and the CI deploy's own test
window. An idle trust that nobody suspended is the expensive failure mode, so the self-test
finishes by printing which pools are up.

***************
Troubleshooting
***************

**``up`` fails: the integration does not exist.** The specs attach
``FLIP_<CODE>_HUB_EAI`` but the platform tier was applied with ``enable_hub_egress = false``.
Either apply it with egress (and the two host settings), or render with ``HUB_EGRESS=false`` and
deploy a hubless trust.

**A service never leaves PENDING.** ``make status`` names the state; ``snow`` shows the container's
own log. The usual causes are an image that is not in the account repository yet (``make
mirror-images`` runs first and pushes by digest), a secret the service role cannot read, or a pool
still suspended.

**XNAT answers HTTP and retrieves nothing.** ``make xnat-configure`` has not run. It registers the
DICOM receiver and the PACS, and a retrieval cannot complete without it.

**A CI deploy stopped with a warning about a running FL client.** That is the FL gate holding the
deploy rather than killing a round. Re-dispatch with ``fl_quiesced=true`` once the round has
finished.

**``GRANT`` or ``REVOKE`` in Snowsight appears to do nothing.** Run the statement on its own:
"Run all" executes only the first statement in a worksheet. A ``USE ROLE`` at the top of the
worksheet is a common way to lose the statement that mattered.

********
See also
********

- ``trust/deploy/snowflake/README.md``: the node's shapes, and why each service looks as it does.
- ``trust/deploy/terraform/snowflake{,-platform,-bootstrap}/README.md``: each tier's objects and
  privileges.
- :doc:`/deploy-flip/onboarding-a-trust`: what the hub admin does before any of this.
