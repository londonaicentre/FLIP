#####
FAQs
#####

Short answers, each with a link to the page that has the detail. For error messages and stuck runs, see
:ref:`troubleshooting`.

.. contents:: On this page
   :local:
   :depth: 1

**************************
For researchers
**************************

Can patient data leave the Trust?
   No. Images are held in the Trust's own XNAT and cohort queries run against the Trust's own OMOP database; neither
   is copied to the Central Hub. What leaves a Trust is aggregate cohort statistics, model updates (which pass
   through a privacy filter first) and task status. A Trust that approves a project is also approving the trained
   model's release to the research team. See :ref:`governance-and-compliance`.

Why does a Trust show no count for my cohort?
   Each Trust suppresses results below its own disclosure threshold (``COHORT_QUERY_THRESHOLD``, default 10). A
   genuine zero looks the same as a small suppressed count, on purpose, so a response cannot reveal that a handful
   of patients matched. The hub cannot lower a Trust's threshold. See :ref:`security` and
   :doc:`components/component-trust-apis`.

Which FL framework should I write my app for?
   The one your deployment runs: each FL net runs one framework, and an app written for the other will not run there.
   A model's **Model Files** panel shows the backend. If you do have a choice, the decision table in
   :ref:`choosing-an-fl-framework` compares NVIDIA FLARE and Flower.

Can my app download pretrained weights while it trains?
   No. The FL server and Trust hosts have no internet route, so the download hangs and the run fails, and a fetched
   file bypasses the checks that uploaded files go through. Upload the weights as one of the model's files and load
   them from beside your code. See :ref:`model-files`.

Why is my training job stuck?
   ``Model Queued (N)`` means other jobs hold the net: each net trains one job at a time, first come, first served.
   *Waiting for resources* means a Trust cannot provide the GPUs the job asked for. A run that never starts a round
   usually means the app tries to download something. :ref:`ts-fl-training-and-projects` covers each case.

Can my app install Python packages at run time?
   No. Nothing is installed per run on either backend: the app imports only what the FL image already carries
   (``flip-utils``, the framework, ``torch`` and ``monai`` among them), and an uploaded ``pyproject.toml`` or
   requirements file does not change that. If you need another package, ask the platform operators. See the
   dependency notes on :doc:`working-with-flip-apps/create-flip-app-from-flare` and
   :doc:`working-with-flip-apps/create-flip-app-from-flower`.

Can I test my app before uploading it?
   Yes. NVIDIA FLARE apps run in FLARE's simulator and Flower apps in a local ``flwr run``, both with the same code
   the platform runs; the tutorials are run the same way. See :ref:`flare-local-testing` and "Local testing before
   upload" on :doc:`working-with-flip-apps/create-flip-app-from-flower`.

Where do training results go, and who can download them?
   At the end of training the FL server uploads the results to the Central Hub, and **Download Results** on the
   model page gives them as a ``.zip``. The project's members can download them, viewers included; nothing is
   written back to a Trust. See :ref:`view-results` and :doc:`sys-admin/admin-user-roles`.

**************************
For Trust staff
**************************

What does a Trust need before it can join?
   Clinical records in the OMOP Common Data Model with imaging linked by accession number, a PACS that will serve
   FLIP's node, a few outbound firewall rules, a server sized for the projects it wants to run, and the data
   protection decisions for the federated flow. :doc:`deploy-flip/trust-readiness-checklist` lists every item by
   owner, in order, in plain language.

Can a Trust decline a project?
   Yes. Each staged Trust decides in its own time; the decision is recorded by the Trust's own Trust Admin or, if it
   has none, by the FLIP admin. A Trust that declines is left out of the image import and of training. Once the
   project is approved, the decision is final. See :ref:`trust-admin-guide` and :ref:`project-approval`.

Can a Trust apply stricter privacy rules than the platform defaults?
   Yes. The Trust sets its disclosure threshold in its own kit file, and an optional governance document can add
   access rules and an NVIDIA FLARE privacy filter. Both are enforced on the Trust's own deployment, and the document
   can only tighten its posture. See "Trust governance document (per trust)" on :ref:`fl-update-privacy`.

Does the hub need inbound access to the Trust?
   No. The Trust polls the Central Hub over HTTPS, so all traffic to the hub is outbound and no inbound port is
   opened to the internet. The one exception is retrieval from the Trust's own PACS, which needs a rule inside the
   Trust's network. See :ref:`deploy-trust-node-network`.

Why is the federated-learning connection not on port 443 like everything else?
   The FL client connects out to the hub's FL endpoint over mutual TLS on its own port (8002 on the production hub).
   NVIDIA, whose framework carries that traffic, advises against 443 because the protocol is gRPC rather than plain
   HTTPS and some inspection appliances reject it there. What the Trust needs depends on how its firewall decides:

   - a firewall that filters by port needs one rule;
   - one that classifies by application must allow ``ssl`` on that port;
   - one that decrypts outbound TLS must exempt the endpoint.

   :ref:`readiness-network` has the questions to ask.

Does a Trust need a site-to-site VPN?
   Only when it cannot allow the outbound federated-learning connection, on its own port, over the internet, by policy
   or because its network team will not write a per-port rule. Everything else FLIP does is ordinary outbound HTTPS. See
   :ref:`site-to-site-vpn`.

Can the node sit behind the Trust's web proxy?
   Not yet. The node needs a direct route, through a firewall allow-list, to the destinations on
   :ref:`deploy-trust-node-network`. A site whose only route is an explicit proxy should say so early.

Does the node need a fixed public address?
   No. The hub identifies a Trust by its certificates and API key. The FLIP team does ask for the address the node's
   traffic leaves the Trust from, so the hub's federated-learning edge can admit declared Trusts only, and needs to
   hear when it changes. See :ref:`readiness-network`.

Can a Trust run both FL frameworks?
   One at a time. The hub chooses the framework, the Trust's kit carries that choice, and the node runs the matching
   client. A hub that switches framework issues every Trust a refreshed kit. See :doc:`components/component-fl-nets`.

What happens if the node goes offline during training?
   It depends on the framework. NVIDIA FLARE needs every selected Trust: the hub refuses to submit while one is
   disconnected, and a Trust that drops out after submission but before the job starts leaves the model waiting until
   someone aborts it. Flower trains on whichever Trusts' clients (Flower calls them SuperNodes) are connected when the
   run starts. A Trust that reconnects joins the next job. See :doc:`components/component-fl-nets`.

How is a Trust upgraded to a new release?
   The Trust's operator runs one upgrade command when ready; nothing on the hub pushes an upgrade. A site runs the
   release its hub runs, and the command defaults to it. Never use the first-install commands on a live site: they
   wipe the XNAT archive. See :ref:`admin-upgrading-sites`.

**************************
For FLIP admins
**************************

Can a user have more than one role?
   One platform role: ``admin``, ``researcher`` or ``viewer``. Changing it in the Admin Area replaces the old one, and
   there is no need to stack roles, since ``admin`` already includes everything a ``researcher`` can do. The exception
   is **Trust Admin**: it is held for one Trust, on top of the ``researcher`` role. See
   :ref:`admin-project-and-user-management`.

What does a mismatched encryption key look like?
   The platform key (``AES_KEY_BASE64``) must be identical on the hub and every Trust. A mismatch fails closed: the
   Trust reports every task as ``Invalid payload: failed authentication`` and its imaging and data-access services
   answer the FL client with a 400. A 500 from either is a different fault. On staging and production the hub's copy
   is the one CI wrote, so recover it from the deployed state before refreshing a kit. See "Registering trusts" on
   :doc:`deploy-flip/deploy-central-hub`.

How are a Trust's keys rotated?
   One command per Trust reissues its API key and internal service key in place; the Trust keeps its id, code and FL
   kit slot. The refreshed kit then goes to the Trust's operator, whose upgrade command restarts every trust-internal
   container together. XNAT's credentials do not rotate with it. See :ref:`deploy-central-hub-rotate-trust-keys`.
