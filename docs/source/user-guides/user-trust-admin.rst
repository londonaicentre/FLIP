.. _trust-admin-guide:

#################################
Approving projects for your Trust
#################################

.. admonition:: Before you start
   :class: note

   You need the **Trust Admin** role for your Trust. A FLIP admin assigns it (see :ref:`assigning-a-trust-admin`).

As a **Trust Admin**, you approve or decline, on your Trust's behalf, the projects that model developers stage at
your Trust.

While your Trust has a Trust Admin, nobody at the hub can decide for it: a project uses your Trust's patients' data
only once you approve it. Apart from that, you have the same access as a ``researcher`` (see :ref:`rbac-roles`).

.. contents:: On this page
   :local:
   :depth: 1

The My Trust page
=================

Open **My Trust** from the top navigation. Its badge counts the projects awaiting your decision.

- **Awaiting your decision** — a card for each project staged at your Trust that you have not decided yet, with the count beside the heading.
- **Decided** — a table of the projects you (or the hub, before your Trust had a Trust Admin) approved or declined, each row marked green or red with who decided and when. Click a row for its description and a link to its query.
- **Trust detail** — to the right, your Trust as the Connection Status page shows it: whether it is online, and the health and version of each of its services.

Deciding on a project
=====================

1. Review the project's card under **Awaiting your decision**. It shows:

   - the project's owner and description, and when it was staged
   - the size of its cohort at your Trust, or, if there is no count, the reason:

     - your Trust did not report one
     - the count was below your Trust's disclosure threshold, so it was withheld
     - your Trust could not run the query

   - whether it uses imaging; for an imaging project, a note that approving starts copying the cohort's images from
     PACS into XNAT (see :doc:`../components/component-xnat`)
   - **View query**, which opens the project's cohort query page

2. Click **Approve** or **Decline**, then confirm. The confirmation names your Trust and what follows.

Your decision applies to your Trust only; other Trusts decide in their own time.

- The project is approved as soon as one Trust approves it.
- If your Trust approves later, it joins the project then, including its existing models.

.. important::

   Once the project is approved, your decision is final.

.. figure:: ../assets/generated/gifs/flip/my-trust-approve.gif
   :align: center

   Approving a project for your Trust from the My Trust page.

Viewing a project staged at your Trust
======================================

You can open any project staged at your Trust, even if you are not a member of it. You see its details, cohort query
and results, its list of models and its imaging status.

You cannot:

- edit, stage or delete the project, or add models to it;
- open a model's own page (metrics, logs, results), which stays with the project's members;
- see projects staged only at other Trusts.
