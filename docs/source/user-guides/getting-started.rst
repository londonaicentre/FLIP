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

.. _getting-started:

#######################
Your first FLIP project
#######################

This page takes a new researcher from no account to a trained model, one step at a time. Each step is short and links
to the full instructions. The X-ray classification tutorial is used as the example app, because it ships for both FL
frameworks and needs no data enrichment.

.. contents:: Steps
   :local:
   :depth: 1

**********************************
Step 1 — Get an account and a role
**********************************

Click **Request access** on the FLIP login page and say why you need access. An administrator enrols you and emails
you a one-time password. Sign in with it, set a new password and set up multi-factor authentication.

You need the ``researcher`` role to create projects and train models; a ``viewer`` can only look.

.. seealso::

   :ref:`request-access`, :ref:`initial-login` and :ref:`rbac-roles`.

*************************
Step 2 — Create a project
*************************

On the Projects page, click **Create project** and give it a name and description. Add the colleagues who will work
with you.

Decide now whether the project **includes imaging data**: you cannot change it later. The X-ray tutorial uses images,
so leave it on, and turn **Convert DICOMs to NIfTI** off, because the tutorial app reads DICOM only.

.. seealso::

   :ref:`create-project`.

*************************************
Step 3 — Write and run a cohort query
*************************************

The cohort query is the SQL that picks your patients and data at each Trust, from its OMOP database. Open the project
and click **Create Cohort Query**, enter your SQL and click **Run on all trusts**. Each Trust runs it on its own
data and reports back.

- An imaging project's query must select ``accession_id``, which FLIP uses to fetch each study's images.
- Select your labels as columns where they exist in OMOP. The X-ray tutorial's ``query.sql`` selects its labels this
  way, so it needs no enrichment step.

.. seealso::

   :ref:`create-cohort-query`, :ref:`the OMOP schema reference <omop-schema>` and :ref:`omop-sample-queries`.

***************************************
Step 4 — Stage the project for approval
***************************************

Choose the Trusts you want to take part, then click **Stage Project**. Staging locks the project details and query.

Each Trust approves or declines in its own time, and the project is approved as soon as one Trust approves it. For an
imaging project, each approving Trust then imports the cohort's images into its XNAT; follow the progress under
**Imaging project status**.

.. seealso::

   :ref:`stage-project`, :ref:`project-approval` and :ref:`imaging-status`.

***************************************************
Step 5 — Pick a framework and start from a tutorial
***************************************************

Your FLIP deployment runs either :term:`NVIDIA FLARE` or the :term:`Flower Framework`, and your app must be written for
that one. :ref:`choosing-an-fl-framework` explains how to tell which and how the two compare.

Start from the X-ray classification tutorial for your framework rather than from scratch:

- :gh:`NVIDIA FLARE version <fl-tutorials/nvflare/image_classification/xray_classification/README.md>`
- :gh:`Flower version <fl-tutorials/flower/xray_classification/README.md>`

Then adapt it with the matching guide: :doc:`../working-with-flip-apps/create-flip-app-from-flare` or
:doc:`../working-with-flip-apps/create-flip-app-from-flower`. :doc:`../working-with-flip-apps/flip-utils-package`
describes the ``flip`` package your app uses to read the cohort and images.

.. tip::

   You can run the tutorial on your own machine before uploading anything. From the repo root:

   .. code-block:: shell

      make -C fl-tutorials download-xray-data
      make -C fl-tutorials run-tutorial TUTORIAL=xray_classification                    # NVIDIA FLARE
      make -C fl-tutorials run-tutorial TUTORIAL=xray_classification FL_BACKEND=flower  # Flower

   See the :gh:`tutorials README <fl-tutorials/README.md>` for requirements.

If your own model needs labels that are not in OMOP, such as segmentation masks, upload them into each Trust's XNAT
now: see :ref:`data-enrichment`.

********************************************
Step 6 — Create a model and upload its files
********************************************

Once the project is approved, click **Create Model** on the project page. On the model page, drag your app's files
into **Model Files**. Each file is checked before it can be used.

The **Model Files** panel lists the files your app's job type requires and marks any still missing.

.. important::

   Your app must not download anything while it runs, such as pretrained weights. Upload every file it needs.

.. seealso::

   :ref:`create-model`, :ref:`model-files` and :ref:`training-configuration`.

***********************
Step 7 — Start training
***********************

On the model page, switch on **Dataset enriched**, choose the participating Trusts and click **Initiate Training**.
You must confirm enrichment even when your data needed none. FLIP queues the model until a training net is free, then
trains it at every selected Trust.

.. seealso::

   :ref:`initiate-training`.

*************************
Step 8 — Read the results
*************************

The model page graphs the metrics your app reports while it trains. When training finishes, click
**Download Results** to get a ``.zip`` of the results.

If a run stalls, open the **Connection Status** page to see whether a Trust has a failing service.

.. seealso::

   :ref:`training-metrics`, :ref:`view-results` and :ref:`connection-status`.

**********
Where next
**********

- :doc:`user-common` — every task in the web interface, in detail.
- :doc:`../working-with-flip-apps` — building your own FL app.
- :doc:`../working-with-flip-apps/wrap-model-for-deployment` — wrapping a trained model for clinical deployment.
