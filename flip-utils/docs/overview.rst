Getting Started with flip-utils
===============================

About flip-utils
----------------

``flip-utils`` is the pip-installable distribution published from this
repository. Its Python import package is ``flip``, which contains the shared
platform logic used by FLIP jobs and services including core training logic,
NVFLARE components, Flower helpers, and utility helpers.

The FLIP platform uses this package to power federated learning applications
across multiple job types: standard federated training, distributed evaluation,
diffusion model training, and custom federated optimization.

Installation
~~~~~~~~~~~~

Install the published package from PyPI:

.. code-block:: bash

   pip install flip-utils
   # or with uv
   uv add flip-utils

To use the latest development version, clone the monorepo and install from source:

.. code-block:: bash

   git clone https://github.com/londonaicentre/FLIP.git
   cd FLIP/flip-utils
   uv sync
   # or
   pip install .

To build a distributable wheel for development:

.. code-block:: bash

   uv build


Package Structure & Modules
---------------------------

The ``flip`` package is organized into logical modules:

``flip.core``
   Core classes and abstractions:

   - ``FLIPBase`` — Abstract base class with common FL logic
   - ``FLIPStandardProd`` — Production implementation using FLIP platform APIs
   - ``FLIPStandardDev`` — Development implementation using local CSV/filesystem
   - ``FLIP()`` factory — Automatically selects the correct implementation based on environment

``flip.constants``
   Configuration and enumerations:

   - ``FlipConstants`` — Pydantic-settings configuration singleton
   - ``ResourceType`` — Enum for imaging resource types (DICOM, NIFTI, etc.)
   - ``ModelStatus`` — Enum for model training states
   - ``PTConstants`` — PyTorch-specific constants and settings

``flip.utils``
   Utility helpers:

   - ``Utils`` — General utility functions
   - ``model_weights_handling`` — Model weight aggregation and manipulation

``flip.nvflare``
   NVFLARE-specific components:

   - ``executors/`` — RUN_TRAINER, RUN_VALIDATOR, RUN_EVALUATOR wrappers
   - ``controllers/`` — Workflow controllers (ScatterAndGather, CrossSiteModelEval, etc.)
   - ``components/`` — Event handlers, persistors, privacy filters, model locators, etc.
   - ``metrics.py`` — Metrics collection and reporting

``flip.flower``
   Flower-specific helpers:

   - ``metrics.py`` — Server-side metrics collection and reporting for Flower runs


Using the FLIP Factory
~~~~~~~~~~~~~~~~~~~~~~

The ``FLIP()`` factory automatically selects between development and production
implementations based on the ``LOCAL_DEV`` environment variable:

.. code-block:: python

   from flip import FLIP

   # Uses FLIPStandardProd in production or FLIPStandardDev in local dev
   flip = FLIP()
   df = flip.get_dataframe(project_id, query)

See the API reference for detailed method documentation.


Job Types
---------

Set the job type via the ``JOB_TYPE`` environment variable:

===========================  ====================================================================================
Type                         Description
===========================  ====================================================================================
``standard``                 Federated training with FedAvg aggregation (default; NVFLARE Executor API)
``standard_client_api``      Federated training via the NVFLARE Client API (Client-API path used for Ark+ etc.)
``evaluation``               Distributed model evaluation without training (NVFLARE Executor API)
``evaluation_client_api``    Distributed model evaluation via the NVFLARE Client API (head-only eval path)
``diffusion_model``          Two-stage training: VAE encoder followed by diffusion model training
``fed_opt``                  Custom federated optimization with flexible aggregation strategies
===========================  ====================================================================================

The Flower backend ships its own ``standard`` and ``evaluation`` templates under
``fl-apps/flower/`` — selected at the deploy layer by ``FL_BACKEND=flower``.


User Application Requirements
-----------------------------

User-provided application code goes in the job's ``custom/`` directory. The
executor wrappers dynamically import these files:

====================  ================================================================
File                  Description
====================  ================================================================
``trainer.py``        Training logic — must export ``FLIP_TRAINER`` class
``validator.py``      Validation logic — must export ``FLIP_VALIDATOR`` class
``models.py``         Model definitions — must export ``get_model()`` function
``config.json``       Hyperparameters — must include ``LOCAL_ROUNDS`` and ``LEARNING_RATE``
``transforms.py``     Data transforms *(optional)*
====================  ================================================================


Development Mode
----------------

To test FL applications locally before deploying to production:

1. Set environment variables in ``.env.development``:

   .. code-block:: bash

      LOCAL_DEV=true
      DEV_IMAGES_DIR=../data/accession-resources
      DEV_DATAFRAME=../data/sample_get_dataframe.csv
      JOB_TYPE=standard

2. Place your application files in ``fl-apps/nvflare/<JOB_TYPE>/app/custom/`` (e.g. fl-apps/nvflare/standard/app/custom/).

3. Run one of the shipped tutorials against the NVFLARE simulator from the repository root:

   .. code-block:: bash

      make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
      # list every available tutorial with:
      make -C fl-tutorials list-tutorials

   The simulator harness is documented in ``fl-tutorials/nvflare/testing/`` and is driven per-tutorial
   via that tutorial's ``.env.app``. See ``fl-services/nvflare/README.md`` for building the local
   ``:dev`` FL images the harness uses.


Running Tests
-------------

Run unit tests for the ``flip`` package:

.. code-block:: bash

   make unit-test
   # or
   uv run pytest -s -vv

Tests use pytest with coverage reporting and are located in ``tests/unit/``.


Building the Docs Locally
--------------------------

``flip-utils`` is documented as part of the FLIP documentation. From the
repository root, run:

.. code-block:: bash

   cd docs && make docs

The generated HTML site will be written to ``docs/build/html``. To clean
previous builds:

.. code-block:: bash

   cd docs && make clean


How the API Reference is Generated
-----------------------------------

The API reference is built with ``sphinx-autoapi`` and points directly at the
``flip/`` source tree. That keeps the reference pages aligned with the code
without maintaining hand-written module stubs. See the API Reference section of
the built documentation for complete coverage of all public classes and functions.

.. note::

   The ``flip-utils`` package lives under ``flip-utils/`` in the `FLIP repository
   <https://github.com/londonaicentre/FLIP>`_, and its documentation is published as part of the
   `FLIP documentation <https://londonaicentreflip.readthedocs.io/en/latest/>`_.
