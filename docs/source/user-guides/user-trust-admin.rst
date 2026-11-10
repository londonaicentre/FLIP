.. _trust-admin-guide:

#################################
Approving projects for your Trust
#################################

.. admonition:: Before you start
   :class: note

   You need the **Trust Admin** role for your Trust. A FLIP admin assigns it (see :ref:`assigning-a-trust-admin`).

A **Trust Admin** approves or declines, on their Trust's behalf, the projects that model developers stage at it.

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
- **Trust kit** — in the same card: download the kit your Trust's node is installed from, or issue it new keys. See `Your Trust's kit`_.

Deciding on a project
=====================

1. Each card under 'Awaiting your decision' shows:

   - the project's owner and description, and when it was staged
   - the size of its cohort at your Trust — or that your Trust did not report one, withheld a count below its disclosure threshold, or could not run the query
   - whether it uses imaging; for an imaging project, a note that approving starts the imaging pull from PACS to XNAT
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

Your Trust's kit
================

The kit is one encrypted file with what your Trust's node needs from the hub: its keys, the hub's settings and its
federated-learning credentials. It applies to a node on a Docker Compose host; the steps for the person who runs the
node are on :ref:`onboarding-kit-from-ui`.

- **Download kit** saves the file and shows its passphrase once. FLIP keeps neither, so save the passphrase before
  you close the dialog. The first download creates your Trust's keys; later ones carry no keys and are safe to take
  whenever the card shows *Kit update available*.
- **Issue new keys** replaces your Trust's keys and downloads a kit that carries them. The node stops connecting at
  once and stays offline until that kit is installed, so agree a time with whoever runs it. You type your Trust's
  code to confirm.

The file holds credentials. Download it on a machine you trust, pass it only to the person who runs the node, and
delete it once it is installed. Every download is recorded against your account.

Viewing a project staged at your Trust
======================================

You can open any project staged at your Trust, even if you are not a member of it. You see its details, cohort query
and results, its list of models and its imaging status.

You cannot:

- edit, stage or delete the project, or add models to it;
- open a model's own page (metrics, logs, results), which stays with the project's members;
- see projects staged only at other Trusts.
