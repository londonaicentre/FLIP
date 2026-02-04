# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

.PHONY: docs clean

docs:
	uv run $(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

clean:
	# Remove the build directory and its contents
	rm -rf $(BUILDDIR)/*
	# Remove the API documentation directory except the index.rst file
	cd $(SOURCEDIR)/autoapi && find . -mindepth 1 -type d -exec rm -rf {} +
