---
license: apache-2.0
pretty_name: FLIP documentation GIFs
viewer: false
tags:
  - flip
  - federated-learning
  - documentation
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

# FLIP documentation GIFs

The animated walkthroughs embedded in the [FLIP](https://github.com/londonaicentre/FLIP) user guides on
[ReadTheDocs](https://londonaicentreflip.readthedocs.io/en/latest/). They are recorded by Cypress from the
FLIP UI against a mocked backend (`flip-ui/test/cypress/docs/`), converted with ffmpeg, and published here by
`.github/workflows/regenerate_docs_gifs.yml` so the recordings never enter the git repository
([FLIP#1236](https://github.com/londonaicentre/FLIP/issues/1236)).

## Layout

One copy of every file, at an unversioned path:

| Path | Contents |
| --- | --- |
| `admin/<name>.gif` | one per `flip-ui/test/cypress/docs/admin/<name>.spec.ts` |
| `flip/<name>.gif` | one per `flip-ui/test/cypress/docs/flip/<name>.spec.ts` |
| `manifest.json` | the provenance of this recording and the sha256 + size of every GIF |
| `README.md` | this card (`docs/scripts/docs_gifs_card.md` in the FLIP repository) |

## Versions

Every commit on `main` is one recording of FLIP `develop`, published by its CI workflow: the GIF of every demo
spec, deletions for GIFs whose spec is gone, the manifest and this card. A recording's version is
`YYYYMMDDTHHMMSSZ-<sha7>`: the UTC instant it started and the first seven characters of the commit it was
recorded from. `main` moves with every recording, and the FLIP `latest` docs follow it. Two other kinds of ref
point at recordings ([FLIP#1547](https://github.com/londonaicentre/FLIP/issues/1547)):

- **`vX.Y.Z` tags**: the recording FLIP release `vX.Y.Z` ships, tagged by its release workflow. Release tags
  are **never moved** and **never deleted**, so that release's documentation keeps resolving them. The same
  holds for the `YYYYMMDDTHHMMSSZ-<sha7>` tags pinned by releases v0.8.0 to v0.12.0, from before release tags.
- **`pr-<N>` branches**: a recording made by hand for FLIP pull request #N, for GIFs CI cannot record (one
  that needs a live trust, say); that PR's documentation preview shows it. Delete the branch once the PR closes.

## Manifest

`manifest.json` is canonical JSON (sorted keys, two-space indent, trailing newline):

```json
{
  "version": "20260907T122006Z-5945242",
  "source_commit": "<the 40-hex FLIP develop commit the GIFs were recorded from>",
  "recorded_at": "2026-09-07T12:20:06Z",
  "workflow_run": "https://github.com/londonaicentre/FLIP/actions/runs/<id>",
  "files": {
    "admin/create-user.gif": { "sha256": "<64 hex>", "bytes": 1234567 },
    "flip/create-project.gif": { "sha256": "<64 hex>", "bytes": 2345678 }
  }
}
```

`version` names the recording and `recorded_at` is its timestamp; `workflow_run` is `null` for a hand publish.
A consumer verifies both `sha256` and `bytes` of every file before using it, and treats the manifest as the
complete list of files in that recording.

## Consuming

Everything is public and read anonymously. Resolve the ref (a tag, a branch, or `main`) to a commit first, then
read everything at that commit, so a recording published meanwhile cannot mix two sets:

```text
https://huggingface.co/api/datasets/aicentreflip/docs-gifs/revision/<ref>       -> {"sha": "<commit>", ...}
https://huggingface.co/datasets/aicentreflip/docs-gifs/resolve/<commit>/manifest.json
https://huggingface.co/datasets/aicentreflip/docs-gifs/resolve/<commit>/<category>/<name>.gif
```

The FLIP docs build does exactly this (`docs/scripts/fetch_docs_gifs.py`, run from `docs/source/conf.py`).
