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

.. _fl-update-privacy:

#################################
Privacy controls on model updates
#################################

During training, each Trust's FL client sends a model update to the FL server on the Central Hub. FLIP can
privatise that update before it leaves the Trust, and a Trust can set its own controls on top of what the app
does.

This page is for model developers choosing a mechanism and for Trust operators setting their site's policy. The
two backends differ:

.. list-table::
   :header-rows: 1
   :widths: 28 36 36

   * - Control
     - FLARE
     - Flower
   * - **App filter**
     - Percentile sparsification and clipping, no noise. On in every training template.
     - Local differential privacy (clipping plus Gaussian noise). **Opt-in**: your ``client_app.py`` must
       register it.
   * - **Site-enforced policy**
     - Set by the Trust; runs whatever the app does.
     - Not available.

.. contents:: On this page
   :local:
   :depth: 2

***************************************
Privacy filters on shared model updates
***************************************

FLARE: percentile sparsification
==================================

The FLARE training job types (``standard``, ``fed_opt``, ``diffusion_model``) pass each client's training
result through a percentile privacy filter before it leaves the site (``PercentilePrivacy``, after Shokri &
Shmatikov, "Privacy-preserving deep learning", CCS '15). The filter:

- zeroes weight-diff components whose magnitude is **below** the ``percentile``-th percentile, so only the largest
  ``100 - percentile`` % of each update is shared;
- truncates the remaining components to ``±gamma``.

The templates use the FLARE defaults, ``percentile=10`` and ``gamma=0.01`` (share the top 90 %, clip at 0.01).
The ``diffusion_model`` job type computes the cutoff separately for each training stage. The ``evaluation``
templates have no filter: they send metrics, not weights.

The filter is set in the template's ``config_fed_client.json``, which an upload cannot replace, so every FLIP run
uses these values. In your own FLARE runs (the simulator, or a job exported with
``FlipFedAvgRecipe(percentile_privacy=...)``) you can tune both, or switch the filter off with ``off: true``.

.. warning::

   **A high** ``percentile`` **sharply degrades training.** At 95, only the top 5 % of every update survives,
   which stalls FedAvg convergence. On a frozen-backbone (head-only) fine-tune the filter would reset the global
   head every round, so with ``AGGREGATE_ONLY_REGEX`` set (see :ref:`fl-training-configuration`) FLIP drops the
   frozen parameters first and computes the percentile over the trainable ones only.

.. important::

   **This is a heuristic output filter, not formal differential privacy.** It bounds what a single round reveals
   but adds no calibrated noise and gives no ``(epsilon, delta)`` guarantee. It complements FLIP's primary output
   controls (review of the uploaded app code, and aggregate-only results) rather than replacing them.

Flower: local differential privacy
==================================

On Flower, the filter is ``flip.flower.privacy.flip_local_dp_mod``, a Flower *mod* that runs on the SuperNode. It
clips the local update to a fixed L2 norm and adds Gaussian noise scaled to the configured budget, so the FL
server only receives a privatised update:

.. code-block:: text

   sigma = dp-sensitivity * sqrt(2 * ln(1.25 / dp-delta)) / dp-epsilon

**It is opt-in.** You upload ``client_app.py``, so the template cannot register the mod for you: register it on
your training function. An app that omits it shares raw updates, and only the review of the uploaded code catches
that. Every Flower training tutorial registers it:

.. code-block:: python

   from flip.flower.privacy import flip_local_dp_mod

   @app.train(mods=[flip_local_dp_mod])
   def train(msg: Message, context: Context) -> Message:
       ...

The parameters live in ``[tool.flwr.app.config]`` and can be overridden per run in ``config.toml``:

.. list-table::
   :header-rows: 1
   :widths: 30 50 20

   * - Key
     - Meaning
     - Template value
   * - ``dp-enabled``
     - ``false`` makes the mod a pass-through, so DP-on and DP-off runs use an identical app.
     - ``true``
   * - ``dp-clipping-norm``
     - L2 norm the update is clipped to.
     - ``1.0``
   * - ``dp-sensitivity``, ``dp-epsilon``, ``dp-delta``
     - The noise calibration in the formula above.
     - ``1e-4``, ``10.0``, ``1e-5``

Also worth knowing:

- **It applies to training rounds only**, not evaluation, matching the FLARE filter's scope.
- **Integer buffers pass through unprivatised.** Counters such as BatchNorm's ``num_batches_tracked`` are step
  counts, not learned parameters.

.. warning::

   The template values are demonstration values that favour utility, not a defensible privacy budget. A real
   budget calibrates ``dp-sensitivity`` to the local dataset and accounts for composition across rounds, which this
   mod does not: every round spends the budget again.

****************************************
Site-enforced privacy policy (per trust)
****************************************

The filters above belong to the **app**. On FLARE, a Trust can also enforce **its own** update-privacy filter
through FLARE's site privacy policy, configured entirely on the Trust's side. Different Trusts can run
different policies in the same federation, with no hub redeploy.

**To enable it** (*Who: Trust operator*):

#. Set the ``FL_SITE_PRIVACY_*`` variables in the Trust's kit file (``trust/.env.<CODE>.<env>``, Host-local
   profile section), or the same settings in the Trust's governance document (see below), not both:

   .. list-table::
      :header-rows: 1
      :widths: 34 46 20

      * - Variable
        - Meaning
        - Default
      * - ``FL_SITE_PRIVACY_POLICY``
        - ``percentile`` (deterministic clip and sparsify). Unset means no site policy.
        - unset
      * - ``FL_SITE_PRIVACY_PERCENTILE`` / ``FL_SITE_PRIVACY_GAMMA``
        - Parameters of the percentile filter, as above. ``percentile`` is 0–100; ``gamma`` is any positive
          value, and a larger one only weakens the truncation.
        - ``10`` / ``0.01``

#. Restart the Trust's FL clients:

   .. code-block:: shell

      make -C trust up-fl-clients-kit KIT=<CODE>

   The client writes the policy into its workspace at start-up. With nothing set, no policy is written and only
   the app's filter applies.

.. important::

   An **invalid** setting, including a misspelled ``FL_SITE_PRIVACY_*`` name, stops the FL client at start-up
   rather than letting it run unfiltered.

How the site policy behaves (verified against FLARE 2.9.0):

- **The job cannot bypass it.** FLARE runs site filters *before* the job's own filters, in one chain, and the
  site filter has no ``off`` switch. It also covers jobs whose app has no filter of its own.
- **Jobs cannot choose a weaker scope.** The policy defines a single default scope, and FLIP refuses any job whose
  ``meta.json`` sets a ``scope``.
- **The percentile covers the whole model.** The site filter runs before FLIP drops the frozen parameters of a
  head-only fine-tune, so its percentile includes their all-zero diffs. Keep site percentiles modest (the default
  ``10``); the convergence warning above applies doubly.
- **Evaluation results are unaffected.** The filter changes only weight payloads; metrics pass through.
- **Failures are loud.** A filter error appears as ``TASK_RESULT_FILTER_ERROR`` in the FL logs and the client's
  audit trail. Nothing is ever sent unfiltered.
- **FLARE only.** Flower has no site-side filter hook, so on Flower the review of uploaded training code remains
  the control.

*************************************
Trust governance document (per trust)
*************************************

A Trust can also state its policy as a **governance document**, ``trust/governance.<CODE>.toml``, by pointing
``ACCESS_POLICY_FILE`` in its kit at it. The document is optional and can only tighten a Trust's posture; without
one, the kit settings are the only controls. ``trust/governance.example.toml`` is a worked example of every key.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Section
     - What it controls
   * - ``[fl_privacy.nvflare]``
     - The site-enforced privacy policy above, as an **alternative** to ``FL_SITE_PRIVACY_*``. Setting the filter
       in both places stops the FL client.
   * - ``[disclosure]`` / ``[access]``
     - A minimum cohort size (``min_cohort_size``, which may raise the kit's ``COHORT_QUERY_THRESHOLD``, never
       lower it) and permit/deny rules by project and operation, enforced by the Data Access API (see
       :doc:`component-trust-apis`).
   * - ``[fl_privacy.flower]``
     - Refused: Flower has no site-side hook yet. ``make -C trust check-governance`` fails a site privacy section
       on a Flower Trust rather than report it as active.

The FL client never sees the whole document, because researcher code runs inside the client and could read every
access rule. On FLARE, a one-shot init step hands the client its ``[fl_privacy]`` section only. If the document
is invalid, that step fails and the client does not start.

**To apply an edit to a live Trust** (*Who: Trust operator*):

.. code-block:: shell

   make -C trust check-governance KIT=<CODE>    # validate the document
   make -C trust reload-governance KIT=<CODE>   # recreate data-access-api (and the FLARE clients) only

``reload-governance`` touches no data. Recreating the FL clients does interrupt any job they are running, so apply
it between runs.

.. warning::

   Do not use ``up-trust`` or ``restart-trust`` to apply a governance change. They are first-install commands: on
   a live Trust they wipe the XNAT archive and database and can replace the data volumes.
