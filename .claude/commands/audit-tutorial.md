---
description: Audit an existing FLIP tutorial against the tutorial contract — guards, GPU-free export, registrations and the README checklist — fixing what is small and listing what needs the author or a GPU
argument-hint: "<name> [nvflare|flower|both]"
allowed-tools: Bash(make:*), Bash(git:*), Bash(bash:*), Bash(uv:*), Read, Edit, Grep, Glob
---

<!--
    Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
    Licensed under the Apache License, Version 2.0 (the "License");
    you may not use this file except in compliance with the License.
    You may obtain a copy of the License at
        http://www.apache.org/licenses/LICENSE-2.0
    Unless required by applicable law or agreed to in writing, software
    distributed under the License is distributed on an "AS IS" BASIS,
    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
    See the License for the specific language governing permissions and
    limitations under the License.
-->
Invoke the `tutorial-contract` skill first (Skill tool). `$ARGUMENTS` names the tutorial (the
directory basename, the same on both backends) and optionally one backend; default both.

1. **Guards.** `make -C fl-tutorials test` (ruff + the CPU-only suites — parity, min_clients, import
   paths, sim-tutorial mapping, offline apps, the catalogue guard, make targets) and
   `bash scripts/check_tutorial_sync.sh`. For NVFLARE, `make export` in the tutorial dir builds the
   full job config without a GPU or data.
2. **Registrations.** Read, do not assume: the Flower `sim-tutorial.sh` `case` entry (+ `RUN_CONFIG`
   for evaluation); `DICOM_APPS` for a 2-D DICOM app; `scripts/check_tutorial_sync.sh` for files
   shared with the twin (the cohort query at least); `docs/source/components/component-fl-nets.rst`,
   `fl-tutorials/README.md`, both `AGENTS.md`; the `e2e_smoke_<name>` target and the demo recorder's
   `APPS` profile when the cohort is seeded on the dev trusts (`references/registration-checklist.md`
   is the full list).
3. **README.** Walk `references/readme-shape.md`: what to upload, the job type, the imaging toggle,
   the dataset target and the simulator command (every quoted `make -C fl-tutorials` target must
   resolve); an input → output figure from a committed `docs/make_<what>_figure.py`; "Expected
   performance" with a reference curve or table, rounds to plateau, hardware and failure signatures;
   `## References` with BibTeX and what differs from the paper; the demo video link.
4. **Known drift** (the skill's §6 list): a Flower client reading `SUPERNODE_NAME` directly, a
   `torch.load` without `weights_only=True`, a target the root Makefile does not forward, a missing
   catalogue line.

Fix what is small in place (a path, a missing registration line, a header, a README section whose
content exists elsewhere in the tutorial). Do **not** invent what needs the author or a GPU — a
citation for the author's own method, a reference training curve, the demo video — list those as
findings with where each would come from. Do not commit; end with the fixed items, the open findings,
and which checks ran.
