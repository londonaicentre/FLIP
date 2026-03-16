Overview
========

About flip-utils
----------------

``flip-utils`` is the pip-installable distribution published from this
repository. Its Python import package is ``flip``, which contains the shared
platform logic used by FLIP jobs and services including core training logic,
NVFLARE components, and utility helpers.

The FLIP platform uses this package to power federated learning applications
across multiple job types: standard federated training, distributed evaluation,
diffusion model training, and custom federated optimization.

Installation
~~~~~~~~~~~~

Install from the repository:

.. code-block:: bash

   uv sync
   # or
   pip install .

Build a distributable wheel:

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
   - ``JobType`` — Enum for supported FL job types
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

+-----------------+--------------------------------------------------------------------------+
| Type            | Description                                                              |
+=================+==========================================================================+
| ``standard``    | Federated training with FedAvg aggregation (default)                    |
+-----------------+--------------------------------------------------------------------------+
| ``evaluation``  | Distributed model evaluation without training                           |
+-----------------+--------------------------------------------------------------------------+
| ``diffusion_model``  | Two-stage training: VAE encoder followed by diffusion model training |
+-----------------+--------------------------------------------------------------------------+
| ``fed_opt``     | Custom federated optimization with flexible aggregation strategies      |
+-----------------+--------------------------------------------------------------------------+


User Application Requirements
-----------------------------

User-provided application code goes in the job's ``custom/`` directory. The
executor wrappers dynamically import these files:

+-------------------+-----------------------------------------------------------+
| File              | Description                                               |
+===================+===========================================================+
| ``trainer.py``    | Training logic — must export ``FLIP_TRAINER`` class       |
+-------------------+-----------------------------------------------------------+
| ``validator.py``  | Validation logic — must export ``FLIP_VALIDATOR`` class   |
+-------------------+-----------------------------------------------------------+
| ``models.py``     | Model definitions — must export ``get_model()`` function  |
+-------------------+-----------------------------------------------------------+
| ``config.json``   | Hyperparameters — must include ``LOCAL_ROUNDS`` and ``LEARNING_RATE`` |
+-------------------+-----------------------------------------------------------+
| ``transforms.py`` | Data transforms *(optional)*                              |
+-------------------+-----------------------------------------------------------+


Development Mode
----------------

To test FL applications locally before deploying to production:

1. Set environment variables in ``.env.development``:

   .. code-block:: bash

      LOCAL_DEV=true
      DEV_IMAGES_DIR=../data/accession-resources
      DEV_DATAFRAME=../data/sample_get_dataframe.csv
      JOB_TYPE=standard

2. Place your application files in ``src/<JOB_TYPE>/app/custom/``.

3. Run the simulator in Docker:

   .. code-block:: bash

      make run-container


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

From the repository root, run:

.. code-block:: bash

   make docs

The generated HTML site will be written to ``docs/_build/html``. To clean
previous builds:

.. code-block:: bash

   make docs-clean


How the API Reference is Generated
-----------------------------------

The API reference is built with ``sphinx-autoapi`` and points directly at the
``flip/`` source tree. That keeps the reference pages aligned with the code
without maintaining hand-written module stubs. See the :ref:`API Reference <reference/index:API Reference>` section for complete documentation of all public classes and functions.
