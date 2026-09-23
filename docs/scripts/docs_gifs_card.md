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
| `manifest.json` | the provenance of this version and the sha256 + size of every GIF |
| `README.md` | this card (`docs/scripts/docs_gifs_card.md` in the FLIP repository) |

## Versions

A version is a git **tag** on this dataset, named `YYYYMMDDTHHMMSSZ-<sha7>`: the UTC instant the recording
started and the first seven characters of the FLIP `develop` commit the GIFs were recorded from. Every publish
is one commit on `main` (the GIF of every demo spec, deletions for GIFs whose spec is gone, the manifest and
this card) followed by one tag on that commit. The FLIP docs pin a tag in `docs/.gifs_version` and fetch it at
build time, so:

- **Tags are never moved** — the publisher refuses a tag that already exists rather than re-pointing it.
- **Tags are never deleted** — historical documentation builds keep resolving their pin.
- `main` is a moving ref: consumers pin a tag, never `main`.

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

`version` is the tag; `recorded_at` is the tag's timestamp; `workflow_run` is `null` for a hand publish. A
consumer verifies both `sha256` and `bytes` of every file before using it, and treats the manifest as the
complete list of files at that version.

## Consuming

Everything is public and read anonymously:

```text
https://huggingface.co/datasets/aicentreflip/docs-gifs/resolve/<tag>/manifest.json
https://huggingface.co/datasets/aicentreflip/docs-gifs/resolve/<tag>/<category>/<name>.gif
```

The FLIP docs build does exactly this (`docs/scripts/fetch_docs_gifs.py`, run from `docs/source/conf.py`).
