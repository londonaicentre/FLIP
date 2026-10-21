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

.. _user-projects:

###############################
Projects and cohort queries
###############################

.. admonition:: Before you start
   :class: note

   You need to be signed in to FLIP with a suitable :term:`RBAC` role (see :ref:`rbac-roles`). Viewers can open the
   projects they are assigned to but cannot create or change anything.

A **project** groups the people, the cohort and the models of one piece of work. This page covers the project from
creation to the point where the Trusts have approved it and imaging is arriving at each Trust.

*********************
The Projects page
*********************

After signing in you land on the Projects page. It lists every project you can access: the ones you created and the
ones you were added to. Users with the ``admin`` role see all projects.

You can filter the list, for example by owner or by a keyword in the name or description. Each project carries a type
chip, **Imaging + OMOP** or **OMOP only**, and the type filter (**All types**, **Imaging + OMOP**, **OMOP only**)
narrows the list to one kind.

.. figure:: ../assets/generated/gifs/flip/filter-project.gif
   :width: 600
   :align: center

   Filtering list of projects.

.. _create-project:

****************
Create a project
****************

1. Click **Create project** in the top right-hand corner of the Projects page.
2. Enter the project name and description.
3. Choose whether the project **includes imaging data**:

   - Leave it on for an imaging cohort. You can then also choose whether to **convert DICOMs to NIfTI** when the images
     are imported into XNAT.
   - Turn it off for a tabular-only cohort, such as EHR data. No imaging is pulled from PACS, no XNAT project is created
     at the Trusts, and the project page shows no imaging status.

4. Optionally, enter the email addresses of other FLIP users and click **Add** for each, so they can view and
   contribute to the project.
5. Click **Create Project**.

.. important::

   Both imaging options are fixed once the project is created. To change them, create a new project.

.. figure:: ../assets/generated/gifs/flip/create-project.gif
   :width: 600
   :align: center

   Creating a new project.

**************
Edit a project
**************

1. Click **Edit Project**.
2. Update the project details, such as the name, description and members.
3. Click **Update Project**.

.. figure:: ../assets/generated/gifs/flip/edit-project.gif
   :width: 600
   :align: center

   Editing a project.

.. note::

   You can edit a project until it is staged for approval. After that, ask your FLIP administrator to **un-stage** it
   so you can edit it again.

****************
Delete a project
****************

Only the project owner or a user with the ``admin`` role can delete a project. You can delete it at any time.

1. Click **Edit Project**.
2. Under **Advanced Options**, click **Delete Project**.
3. Enter the project name.
4. Click **Continue**.

.. figure:: ../assets/generated/gifs/flip/delete-project.gif
   :width: 600
   :align: center

   Deleting a new project.

.. warning::

   Deleting a project deletes any training runs in progress; they can no longer be reached.

What happens to imaging already pulled to the Trusts:

- It is **kept** in each Trust's XNAT on purpose. Images could be pulled from PACS again, but the segmentations,
  contours and annotations added during data enrichment could not, so deleting a project never destroys them.
- The project stays visible in XNAT to users who have access to it there. It is no longer reachable through FLIP.
- Removing a Trust's imaging is a **separate administrator action** at the Trust, not part of deleting a project.

Deleting a project is a soft delete: the platform keeps the record and audits the deletion.

.. _cohort-query:

*************
Cohort query
*************

The cohort query is the SQL that selects your project's patients and data at each Trust. Cohort data is held in a
`PostgreSQL <https://www.postgresql.org/>`_ database in the OMOP Common Data Model, extended with the :term:`MI-CDM`
imaging tables. See :ref:`the schema reference <omop-schema>` for the tables you can query and
:ref:`omop-sample-queries` for two worked examples.

.. important::

   **Imaging projects must select** ``accession_id``. This column of ``image_occurrence`` identifies the DICOM study;
   XNAT uses it to retrieve the images from PACS. An imaging project whose query lists its columns and leaves out
   ``accession_id`` is rejected when you submit it, with a message naming the fix.

   A project created without imaging data needs no ``accession_id``. If an existing imaging project's cohort has no
   imaging, re-create the project with **Includes imaging data** turned off.

.. _create-cohort-query:

Create a cohort query
=====================

1. On the project page, click **Create Cohort Query** in the **Cohort Query** card on the left.
2. Enter the query in SQL, for example:

   .. code-block:: sql

       SELECT accession_id, concept_name, year_of_birth FROM omop.person p
       JOIN omop.image_occurrence r ON r.Person_Id = p.Person_Id
       JOIN omop.concept c ON p.gender_concept_id = c.concept_id
       WHERE year_of_birth < 1980

3. Click **Run on all trusts**. The query is saved and sent to each Trust.
4. View the results from each Trust as they come back.

.. figure:: ../assets/generated/gifs/flip/cohort-query.gif
   :width: 600
   :align: center

   Creating a cohort query.

What a query may contain:

- **One** ``SELECT`` **statement** against the ``omop`` schema.
- **No writes or schema changes**: ``INSERT``, ``UPDATE``, ``DELETE``, ``MERGE``, ``CREATE``, ``ALTER`` and ``DROP``
  are rejected wherever they appear in the query.
- ``LIMIT`` and ``OFFSET`` **only as literal whole numbers**.

Each Trust parses and checks the query itself, rather than matching keywords, so ordinary read-only functions such as
``SUBSTRING()`` are allowed. The Trust also runs the query as a read-only database user, so it cannot change any data.

Edit a cohort query
===================

You can change and re-run the query, for example after a timeout, until the project is staged.

1. On the project page, click **View Query**.
2. Change the query and run it again, as in :ref:`create-cohort-query`.

.. note::

   After staging, ask your FLIP administrator to un-stage the project before you can change its query.

.. _stage-project:

***************
Stage a project
***************

Staging asks the Trusts to approve your project. You can stage once the project has a saved cohort query.

1. Use the switch next to each Trust to include or exclude it. Included Trusts are asked for approval and take part in
   training.
2. Click **Stage Project**.

.. figure:: ../assets/generated/gifs/flip/stage-project.gif
   :width: 600
   :align: center

   Staging a project.

.. warning::

   Staging **locks** the project details and cohort query. To change them afterwards, ask your FLIP administrator to
   un-stage the project.

.. _project-approval:

****************
Project approval
****************

Each staged Trust decides **offline** and in its own time. The decision is recorded in FLIP by the FLIP Central Hub
admin or, if the Trust has one, by the Trust's own Trust Admin (see :ref:`trust-admin-guide`).

The **Trust Approval** section of the project page shows each Trust as *Pending*, *Approved* or *Declined*, with who
decided and when. The project is approved as soon as one Trust approves it.

.. note::

   Trusts that decline are left out of the imaging import and of training. If every Trust declines, the project stays
   staged: ask your FLIP administrator to un-stage it, amend it and stage it again.

.. _imaging-status:

************************
Imaging project status
************************

After approval, FLIP creates an XNAT project at each participating Trust and imports the cohort's images from that
Trust's PACS (see :ref:`flip-xnat`). The **Imaging project status** section of the project page shows the import's
progress at each Trust.

.. figure:: ../assets/generated/gifs/flip/imaging-status.gif
   :width: 600
   :align: center

   Imaging status overview

Large imports from PACS can take a long time, and single studies can fail when the system is under strain. FLIP
re-imports failed studies automatically; the **reimport count** shows how often.

Each Trust's card shows the latest counts it reported. If a Trust stops reporting, its card keeps the last counts,
marked **Last known** with the time they were confirmed, and the footer says why:

- *Trust XNAT not reachable*: the Trust's XNAT did not respond.
- *Trust XNAT reachable, but project not found*: XNAT answered but no longer holds the imaging project, for example
  after the Trust's deployment was reset.

The card returns to normal once the Trust reports again.

.. figure:: ../assets/generated/gifs/flip/study-reimport-max.gif
   :width: 600
   :align: center

   Reimport cap has been reached

.. important::

   Once the **reimport cap** is reached, failed studies are no longer re-imported. If failures remain, contact an XNAT
   administrator; the studies may need manual attention.

Next, add any labels your model needs that are not in OMOP: see :ref:`data-enrichment`.
