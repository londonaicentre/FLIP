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

.. _choosing-an-fl-framework:

#######################
NVIDIA FLARE or Flower?
#######################

FLIP runs federated learning on two frameworks, :term:`NVIDIA FLARE` and the :term:`Flower Framework`. Your app is
written for one of them, so this is the first decision you make as a model developer. This page compares the two
from the app author's side and points you to the right guide.

.. contents:: On this page
   :local:
   :depth: 1


Check which framework your FLIP deployment runs
===============================================

**The deployment chooses the framework, not the model.** Each FL net runs one backend, fixed when the platform's
nets are set up, and the hub bundles your uploaded files with that backend's template. An app written for the other
framework will not run there.

To see which one you are on, open a model's page: the **Model Files** panel shows the backend (*FLARE* or
*Flower*) next to the detected ``job_type``. Before you have a model, ask your FLIP administrator.

If the deployment you will train on is fixed, write for its framework and skip to the matching guide. The rest of
this page helps when you do have a choice, for example when you are planning a deployment or porting an app.


Decision table
==============

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - If you need…
     - Pick
   * - To train on a FLIP deployment whose nets already run one framework
     - **That framework**
   * - Two-stage diffusion model training (``diffusion_model`` job type)
     - **NVIDIA FLARE**
   * - Federated optimisation with a server-side optimiser (``fed_opt`` job type)
     - **NVIDIA FLARE**
   * - A privacy filter each Trust enforces on its own side, which the app cannot switch off
     - **NVIDIA FLARE** — Flower has no site-side filter hook
   * - Differential-privacy noise on shared updates
     - **Flower** — a per-round Gaussian mechanism, valid only with a calibrated ``dp-sensitivity``; NVIDIA FLARE's
       filter is a heuristic, without noise
   * - The least FLIP-specific code in your app
     - **NVIDIA FLARE** — the template reports status, resolves client identity and wires the privacy filter
   * - Federated averaging (``standard``) or per-Trust evaluation (``evaluation``)
     - **Either**
   * - To port an existing app
     - **The framework it is already written for**
   * - To start from the Ark+ chest X-ray or latent-diffusion tutorials
     - **NVIDIA FLARE** (these tutorials exist only for it)
   * - To start from the nnU-Net prostate segmentation tutorial
     - **Flower** (this tutorial exists only for it)


How the two compare
===================

.. list-table::
   :header-rows: 1
   :widths: 26 37 37

   * -
     - NVIDIA FLARE
     - Flower
   * - Job types
     - ``standard``, ``evaluation``, ``diffusion_model``, ``fed_opt``
     - ``standard``, ``evaluation``
   * - What you upload
     - ``trainer.py``, ``models.py``, ``config.json`` (+ helper modules)
     - ``client_app.py``, ``models.py``, ``config.json`` (+ optional ``config.toml``)
   * - Server side
     - FLIP template (nothing to write)
     - FLIP template ``ServerApp`` (you write one only for local runs)
   * - Model lifecycle status
     - Reported automatically by the FLIP template
     - ``flip.update_status`` calls in the ``ServerApp``
   * - Run configuration
     - ``config.json`` (``GLOBAL_ROUNDS``, ``LOCAL_ROUNDS``, …)
     - ``config.toml`` overriding ``[tool.flwr.app.config]``
   * - Per-epoch metrics
     - ``SummaryWriter.add_scalar``, forwarded by the template
     - ``MetricRecord`` in the reply, forwarded by the server
   * - Client identity
     - Resolved automatically
     - ``client_identity(context)`` placed in the reply's ``ConfigRecord``
   * - Privacy filter on updates
     - Percentile filter (clip and sparsify, no noise), wired by the template
     - Local differential privacy (clip plus Gaussian noise), registered by your ``client_app.py``
   * - Site-enforced privacy policy
     - Optional, set by each Trust; the app cannot bypass it
     - Not available
   * - Local run
     - ``job.py`` recipe: ``make export`` / ``make sim``
     - ``make run-tutorial`` (simulator)

Privacy on shared updates
-------------------------

The two frameworks privatise a Trust's training result differently before it leaves the site:

- **NVIDIA FLARE** sparsifies and clips the update, without noise. This is a heuristic filter, not formal
  differential privacy. It can be tuned or switched off only in local or recipe runs; on the platform the template
  fixes it, and a Trust can also enforce **its own** filter, which runs whatever the app does.
- **Flower** clips the update and adds Gaussian noise: a per-round Gaussian mechanism, valid only with a calibrated
  ``dp-sensitivity``. It is applied only if your ``client_app.py`` registers the privacy mod, so it is enforced by
  code review rather than by the platform, and the shipped defaults are demonstration values, not a defensible privacy
  budget.

If the Trusts in your project require a site-enforced policy, use NVIDIA FLARE.

.. seealso::

   *Privacy filters on shared model updates* on :ref:`fl-update-privacy` covers both mechanisms, their parameters and
   their caveats.


Tutorials and templates per framework
=====================================

Each framework has its own app templates, one per job type, and its own worked tutorials. The same model often
exists on both, so you can compare them side by side. The templates are under :gh:`fl-apps/nvflare` and
:gh:`fl-apps/flower`; to list the tutorials for each framework:

.. code-block:: shell

   make -C fl-tutorials list-tutorials                    # NVIDIA FLARE
   make -C fl-tutorials list-tutorials FL_BACKEND=flower

:doc:`/paper/arkplus-fine-tuning` walks through one tutorial end to end on the platform.


Next steps
==========

- :doc:`create-flip-app-from-flare` — adapt an NVIDIA FLARE Client API script to run on FLIP.
- :doc:`create-flip-app-from-flower` — adapt a Flower app to run on FLIP.
- :ref:`fl-jobs` — job types, required files and configuration keys for both frameworks.
