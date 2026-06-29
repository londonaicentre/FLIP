---
description: Add the Apache 2.0 copyright header to new/changed source files that are missing it
allowed-tools: Bash(git:*), Read, Edit, Grep
---

CLAUDE.md requires every source file to carry the Apache 2.0 copyright header, but **nothing
in CI or pre-commit enforces it**. This command finds source files missing the header and adds it.

The canonical header (FLIP-authored files use the year **2026** and this exact owner string):

**Hash-comment languages** (`.py`, `.sh`, `.yml`, `.yaml`, `Dockerfile`, `Makefile`, `.toml`):
```
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
```

**C-style block-comment languages** (`.ts`, `.js`, `.vue`, `.css`):
```
/*
 * Copyright (c) 2026 Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
```

Steps:

1. **Determine the file set.** Default to files added/changed on this branch:
   ```
   git diff --name-only --diff-filter=ACMR develop
   git status --porcelain
   ```
   If the user passed paths in `$ARGUMENTS`, use those instead. Restrict to source files in the languages above; skip vendored/generated files (e.g. anything carrying a `Copyright (c) 2026 Flower Labs GmbH` header — leave third-party headers untouched), lockfiles, JSON, Markdown, and `required_files.json`.

2. **Check each file** for an existing copyright header (Grep for `Copyright (c)` in the first ~15 lines). Skip files that already have one.

3. **Insert the matching header** for files that are missing it, using Edit:
   - Preserve any shebang (`#!/usr/bin/env ...`) or encoding line as the **first** line — insert the header immediately after it.
   - For `.vue` files, the header goes at the very top of the file (before `<template>`/`<script>`).
   - Otherwise insert at the very top.

4. **Report** the list of files you added headers to, and any you skipped (already had one / vendored / unsupported type). Do not commit — leave staging to the user.
