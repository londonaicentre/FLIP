######################
Common user functions
######################

.. admonition:: Before you start
   :class: note

   You need a FLIP account. To get one, click **Request access** on the login page (see :ref:`request-access`), or
   ask your local FLIP system administrator or information asset owner (IAO), giving your email address and the role
   you need.

These pages cover the FLIP web interface from a **researcher's** point of view: preparing a project, training models
across the Trusts and collecting the results. New to FLIP? Start with :doc:`getting-started`.

What you can do depends on your role (see :ref:`rbac-roles`). Each user has one of these roles:

- ``researcher`` — everything described in these pages.
- ``viewer`` — read-only access to the projects you are assigned to. You cannot create projects, run queries, upload
  files or start training.
- ``admin`` — everything a researcher can do, plus un-staging projects and approving projects at every Trust that has
  no Trust Admin (see :ref:`admin-project-and-user-management` and the wider :ref:`sys-admin` section).

A **Trust Admin** is a researcher who also holds the Trust Admin role for one Trust, and approves or declines projects
for it (see :ref:`trust-admin-guide`).

****************
A project's life
****************

FLIP organises work into **projects**. A project has members, one cohort query and any number of models. Its typical
life is:

1. Create the project and write its cohort query: :doc:`user-projects`.
2. Stage it for approval; each Trust approves or declines: :ref:`stage-project`.
3. Add any labels your model needs that are not in OMOP: :doc:`user-data-enrichment`.
4. Create a model and upload its files: :doc:`user-models`.
5. Start training, follow it and download the results: :doc:`user-training`.

FLIP supports two FL frameworks, :term:`NVIDIA FLARE` and the :term:`Flower Framework`, and your app is written for
the one your deployment runs (see :ref:`choosing-an-fl-framework`). The file requirements and job types for each are
on the :ref:`FL nets component page <flip-fl-nets>`.

.. toctree::
   :maxdepth: 1

   user-account
   user-projects
   user-data-enrichment
   user-models
   user-training
