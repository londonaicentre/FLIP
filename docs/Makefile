# Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# Makefile for Sphinx documentation and automated API doc generation
# Due to multiple Makefiles in this project, the second command should be run from the docs/ directory

# Usage:
#   make clean
#   make docs

SPHINXBUILD   = sphinx-build
SOURCEDIR     = source
BUILDDIR      = build

.PHONY: docs clean test

docs:
	uv run $(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

clean:
	# Remove the build directory and its contents
	rm -rf $(BUILDDIR)/*
	# Remove what the conf.py build hooks generate: the AWS diagrams and the fetched docs GIFs (FLIP#1236)
	rm -rf $(SOURCEDIR)/assets/generated
	# Remove the API documentation directory except the index.rst file
	cd $(SOURCEDIR)/autoapi && find . -mindepth 1 -type d -exec rm -rf {} +

# Unit tests + lint for the build-time scripts under scripts/ (the docs GIFs fetcher and publisher, FLIP#1236),
# the conf.py hook that runs the fetcher, and the static guards in tests/ (rst<->spec wiring, the regenerate
# workflow's token scope). `--no-project` keeps pytest out of the docs lock: the scripts are PEP 723 and the
# Sphinx project stays exactly what ReadTheDocs installs; `sphinx` and `pyyaml` are pulled in only so conf.py
# imports and the workflow parses. conf.py is linted (its hooks are code) but not format-checked (it predates
# ruff format).
test:
	uv run --no-project --with pytest --with 'requests>=2.33' --with 'huggingface_hub>=1.6' --with pyyaml --with 'sphinx>=8' pytest tests -q
	uvx ruff@0.14.7 check scripts tests source/conf.py && uvx ruff@0.14.7 format --check scripts tests
