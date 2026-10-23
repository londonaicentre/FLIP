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

**************************
For Trust staff
**************************

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

How is a Trust upgraded to a new release?
   The Trust's operator runs one upgrade command when ready; nothing on the hub pushes an upgrade. A site runs the
   release its hub runs, and the command defaults to it. Never use the first-install commands on a live site: they
   wipe the XNAT archive. See :ref:`admin-upgrading-sites`.

**************************
For FLIP admins
**************************

Can a user have more than 1 role assigned to them?
   One platform role: ``admin``, ``researcher`` or ``viewer``. Changing it in the Admin Area replaces the old one, and
   there is no need to stack roles, since ``admin`` already includes everything a ``researcher`` can do. The exception
   is **Trust Admin**: it is held for one Trust, on top of the ``researcher`` role. See
   :ref:`admin-project-and-user-management`.
