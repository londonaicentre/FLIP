.. _trust-admin-guide:

####################################
Approving Projects for your Trust
####################################

.. warning:: Must be logged into FLIP with the ``trust admin`` role for your Trust. A FLIP Admin assigns it — see :ref:`assigning-a-trust-admin`.

A **Trust Admin** approves or declines, on their Trust's behalf, the projects that model developers stage at it. While your Trust has a Trust Admin, nobody at the hub can decide for it: a project uses your Trust's patients' data only once you approve it. Otherwise you have the same access as a ``researcher`` (see :ref:`rbac-roles`).

The My Trust page
=================

Open **My Trust** from the top navigation. Its badge counts the projects awaiting your decision.

- **Awaiting your decision** — a card for each project staged at your Trust that you have not decided yet, with the count beside the heading.
- **Decided** — a table of the projects you (or the hub, before your Trust had a Trust Admin) approved or declined, each row marked green or red with who decided and when. Click a row for its description and a link to its query.
- **Trust detail** — to the right, your Trust as the Connection Status page shows it: whether it is online, and the health and version of each of its services.

Deciding on a project
=====================

1. Each card under 'Awaiting your decision' shows:

   - the project's owner and description, and when it was staged
   - the size of its cohort at your Trust — or that your Trust did not report one, withheld a count below its disclosure threshold, or could not run the query
   - whether it uses imaging; for an imaging project, a note that approving starts the imaging pull from PACS to XNAT
   - **View query**, which opens the project's cohort query page

2. Click **Approve** or **Decline**, then confirm. The confirmation names your Trust and what follows.

Your decision applies to your Trust only; other Trusts decide in their own time. The project is approved as soon as one Trust approves it, and if yours approves later, it joins the project then, including its existing models. Once the project is approved your decision is final.

.. figure:: ../assets/generated/gifs/flip/my-trust-approve.gif
   :align: center

   Approving a project for your Trust from the My Trust page.

Viewing a project staged at your Trust
======================================

You can open any project staged at your Trust — its details, cohort query and results, the list of its models and imaging status — even if you are not a member of it. You cannot edit, stage or delete it or add models to it, a model's own page (metrics, logs, results) stays with the project's members, and projects staged only at other Trusts stay hidden from you.
