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

# Federated nnU-Net fingerprint (Flower)

## Overview

`job_type=fingerprint` produces the dataset fingerprint nnU-Net plans a network from, for a
federation whose sites cannot be read from one place. Nothing is trained. Each site reduces its
cases to histograms (per axis, the share of its cases at each voxel spacing, cropped shape and
physical extent; its mean per-case foreground intensity histogram; the share of cases at each crop
ratio), and the unmodified `standard` server averages them by case count, which gives the pooled
dataset's own histograms. The downloaded model is the pooled fingerprint.

Unlike `standard` and `evaluation`, the **client is FLIP's**: `app/client_app.py`,
`app/fingerprint.py` and `app/models.py` ship with the template beside `app/server_app.py` and
`app/strategy.py`, and base files win over uploaded ones, so an upload cannot replace them.

## What does the user upload?

The required files (see [`required_files.json`](./required_files.json)) are:

- `cases.py` — defines `iter_cases(context)`, which yields one `(image, foreground, spacing)` per
  case: the image as a `(C, d, h, w)` array, the label mask as a `(d, h, w)` boolean array and the
  voxel spacing in mm as `(d, h, w)`. It fetches the cohort and the files through `flip.FLIP`, as
  any client does.
- `config.json` — `job_type`, plus the histogram grids and the floors:

| Key | Meaning |
| --- | --- |
| `NUM_CHANNELS` | image channels `iter_cases` yields |
| `MIN_SITE_CASES` | fewest cases a site releases a fingerprint for; values below 10 are ignored |
| `MIN_CELL_COUNT` | fewest cases in any released spacing, shape, extent or crop-ratio bin; values below 5 are ignored |
| `SPACING_BIN_MM`, `MAX_SPACING_MM` | spacing grid; a case beyond the maximum is refused, not clipped |
| `MAX_SHAPE` | largest cropped shape in voxels on any axis; refused beyond it |
| `EXTENT_BIN_MM`, `MAX_EXTENT_MM` | physical-extent grid; refused beyond the maximum |
| `INTENSITY_MIN`, `INTENSITY_MAX`, `INTENSITY_BIN` | intensity grid; values outside are clipped into the end bins |

Other uploaded files (the modules `cases.py` imports) are bundled alongside.

## What the platform enforces

- **Only histograms and a case count are sent.** `client_app.py` builds the reply; `cases.py`
  returns arrays to it and never writes the payload.
- **A site with fewer than ten cases sends nothing**, and the refusal does not say how many it has.
- **No released bin holds fewer than five of a site's cases.** Smaller bins are folded into the
  nearest bin that holds at least five.
- **No local differential privacy.** The clip-and-noise mod is calibrated for weight updates and
  would turn the histograms into noise without an error, so the template does not register it and
  declares no `dp-*` key.

These are code paths an upload cannot replace, not a sandbox: `cases.py` still runs inside the
client process, so the code review of an upload remains the control on what it does there. The hub
sees each site's histograms in the clear; FLIP has no secure aggregation.

## Run it

[`3d_prostate_nnunet_fingerprint`](../../../fl-tutorials/flower/image_segmentation/3d_prostate_nnunet_fingerprint/README.md)
is the worked example, including how the result becomes a plan for the training job:

```bash
make -C fl-tutorials prepare-prostate-local-data NUM_CASES=40
make -C fl-tutorials run-tutorial TUTORIAL=3d_prostate_nnunet_fingerprint FL_BACKEND=flower
```
