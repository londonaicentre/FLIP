---
description: Create a FLIP tutorial under fl-tutorials/ from a centralised training script, on NVFLARE, Flower or both, by copying the nearest existing tutorial and working through the registration checklist
argument-hint: "[<name>] [<path to the centralised script>]"
allowed-tools: Bash(make:*), Bash(git:*), Bash(cp:*), Bash(uv:*), Read, Edit, Write, Grep, Glob
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
Invoke the `tutorial-contract` skill first (Skill tool); it holds the contract, the copy-from table and
the two references this command works through.

`$ARGUMENTS` may give the tutorial name and the path to the centralised script. Then settle the shape in
**one** `AskUserQuestion` round (skip any answer the user already gave):

1. Backend(s): both / NVFLARE only / Flower only.
2. Job type: `standard` / `evaluation` / `fed_opt` / `diffusion_model` (the four `fl-apps` templates).
3. Data: 2-D DICOM / 3-D NIfTI via XNAT with enrichment labels / tabular (OMOP-only, imaging off).
4. Dataset: reuse an existing one under `fl-tutorials/datasets/` / new public / new access-controlled.

Ask for whatever is still missing — the name (unique across categories and backends, the same on
both), the task in one sentence, the script path — in a second question only if needed. A new dataset,
and especially one that must exist on the platform, is a separate deliverable: say so and scope it with
the user rather than fold it in.

Then:

1. Pick the nearest tutorial from the skill's copy-from table and copy its directory (both backends
   when asked), rename, and keep the structural files and their comments.
2. Split the centralised script: the model, transforms / feature engineering and the train or eval loop
   with FLIP data access go into `app_files/` (NVFLARE) or `app/` (Flower); dataset download or
   derivation and checkpoint conversion stay host-side (`fl-tutorials/datasets/`, `process_tools/`).
   Data only through `flip.FLIP`; simulator-only logic behind `FlipConstants.LOCAL_DEV`; Flower identity
   from `client_identity(context)`; no run-time downloads, `torch.load(..., weights_only=True)`.
3. Work through `references/registration-checklist.md` — the Flower `sim-tutorial.sh` `case` entry
   and a new dataset target in `DATASET_TARGETS` break a run when forgotten; the docs catalogue, both
   `AGENTS.md` files and `fl-tutorials/README.md` make the tutorial invisible when forgotten.
4. Write the README to `references/readme-shape.md`; add the Apache header to every new source file
   (the `headers` command).
5. Verify: `make -C fl-tutorials test`; `make export` in the NVFLARE tutorial dir (no GPU needed);
   the simulator (`make -C fl-tutorials run-tutorial TUTORIAL=<name> [FL_BACKEND=flower]`) when the
   dataset is downloaded and, for imaging, a GPU is present — check with `nvidia-smi` and
   `ls fl-tutorials/data` rather than assuming. Report which of the three ran.

Do not commit; summarise the files created, the registrations touched, and what still needs the
author (a citation, a reference curve, a demo video).
