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
   - ``JobType`` — Enum for supported FL job types
   - ``PTConstants`` — PyTorch-specific constants and settings

``flip.utils``
   Utility helpers:

   - ``Utils`` — General utility functions
   - ``model_weights_handling`` — Model weight aggregation and manipulation

``flip.nvflare``
   NVFLARE-specific components:

   - ``controllers/`` — Workflow controllers (ScatterAndGather, CrossSiteModelEval, etc.)
   - ``components/`` — Event handlers, persistors, privacy filters, model locators, etc.
   - ``recipes/`` — High-level NVFLARE job recipes
   - ``runtime.py`` — Runtime helpers for NVFLARE apps
   - ``metrics.py`` — Metrics collection and reporting

``flip.flower``
   Flower-specific helpers:

   - ``strategy.py`` — Flower ``Strategy`` implementations (e.g. ``FedAvgWithClientMetrics``)
   - ``metrics.py`` — Server-side metrics collection and reporting for Flower runs
   - ``progress.py`` — Progress/status reporting helpers for Flower runs


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

Pass the job type to the ``FLIP()`` factory (``FLIP(job_type=...)``). The
``JobType`` enum (``flip.constants.job_types``) defines the values recognised by
``FLIP()``:

===========================  ====================================================================================
Type                         Description
===========================  ====================================================================================
``standard``                 Federated training with FedAvg aggregation (default)
``evaluation``               Distributed model evaluation without training
``diffusion_model``          Two-stage training: VAE encoder followed by diffusion model training
``fed_opt``                  Custom federated optimization with flexible aggregation strategies
===========================  ====================================================================================

The NVFLARE backend additionally ships a template directory under
``fl-apps/nvflare/`` for each Client-API job type (``standard``,
``evaluation``, ``diffusion_model``, ``fed_opt``); the template names match the
``JobType`` enum values above. The Flower backend ships its own ``standard`` and
``evaluation`` templates under ``fl-apps/flower/`` — selected at the deploy
layer by ``FL_BACKEND=flower``.


Data Enrichment (``flip.xnat``)
-------------------------------

``flip.xnat`` uploads *data-enrichment* files — segmentation masks and other
image-derived annotations — into a FLIP project's XNAT, so that supervised apps
find a label beside each pulled image. Labels that already exist in OMOP (a lab
result, a coded report finding) do not need this: project them as a column of
the cohort query instead.

Unlike the rest of this package, ``flip.xnat`` does **not** run in the FL
client. It runs on the model developer's workstation inside the Trust network
(or as an XNAT Container Service job), authenticated as their own XNAT account;
FL clients hold no XNAT credentials. It is the write-side counterpart to
``get_by_accession_number()``, which reads the same scan resource.

A ``flip-xnat`` console script is installed with the package:

.. code-block:: bash

   export XNAT_HOST=https://xnat.trust.example
   export XNAT_USER=your-username
   export XNAT_PASS=your-password

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv --dry-run

The manifest is a CSV of ``accession_id,file_path`` (plus an optional
``target_filename``, which must be a bare filename). By default each uploaded
file is named after the image already in the scan's ``NIFTI`` resource, swapping
the ``input_`` prefix for ``label_``, which is the pairing the apps rely on.
Existing files are never replaced unless ``--overwrite`` is passed.

Every Trust in the project needs enriching — each Trust's XNAT holds only its
own studies — so repeat ``--credentials-file`` to cover the roster in one run:

.. code-block:: bash

   flip-xnat upload --flip-project-id <project-uuid> --manifest manifest.csv \
     --credentials-file gstt.json --credentials-file kch.json

The same manifest goes to every Trust; an accession exists at exactly one site
and the others report it as *no matching scan*. A run that resolves no
destination anywhere exits non-zero, so an automated pipeline cannot mistake a
wholly-skipped enrichment for a completed one; pass ``--allow-no-op`` when an
empty run is genuinely expected.

The same operations are available as a Python API:

.. code-block:: python

   from flip.xnat import XnatClient, read_manifest, run_enrichment

   clients = [XnatClient.from_config_file("gstt.json"), XnatClient.from_config_file("kch.json")]
   report = run_enrichment(clients, read_manifest("manifest.csv"), flip_project_id="<project-uuid>")
   print(report.render())
   raise SystemExit(report.exit_code())

Run it only after the image pull **and** after DICOM-to-NIfTI conversion: the
target filename is derived from the converted image, so running earlier skips
every scan.


User Application Requirements
-----------------------------

The job components dynamically import user-provided code from the job's
``custom/`` directory. On the platform that directory is assembled by the FL
API, which merges the uploaded app files onto the matching
``fl-apps/nvflare/<template>/app`` template; in local SimEnv runs the
tutorial's ``job.py`` stages its ``app_files/`` into the job's ``custom/``
directly.

====================  ================================================================
File                  Description
====================  ================================================================
``trainer.py``        Training logic — a plain ``nvflare.client`` script
``validator.py``      Extra validation module where the job type requires one
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

2. Place your application files in the tutorial's ``app_files/`` directory
   (e.g. ``fl-tutorials/nvflare/image_classification/xray_classification/app_files/``).
   On the platform they are merged onto the matching
   ``fl-apps/nvflare/<template>/app/`` template at submit time.

3. Run one of the shipped tutorials against the NVFLARE simulator from the repository root:

   .. code-block:: bash

      make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
      # list every available tutorial with:
      make -C fl-tutorials list-tutorials

   Each tutorial's ``make run`` delegates to ``make sim`` — its ``job.py`` driving a FLIP recipe on
   the NVFLARE simulator (SimEnv) from the flip-utils venv — configured per-tutorial via that
   tutorial's ``.env.app``.


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
