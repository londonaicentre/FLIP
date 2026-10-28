# MONAI Label demo video

Records a clinician's path through MONAI Label on a live trust, as an mp4:

1. sign in to the trust's XNAT and open a CT session in the OHIF viewer;
2. switch on MONAI Label (*Options → Preferences → Experimental*, once per browser);
3. build the spleen from nothing: a new empty segment named `spleen`, then `sam_2d` (Segment
   Anything, under *DeepGrow*) prompted on the spleen's largest slice with a positive click and,
   where it leaks, negative (Ctrl) clicks;
4. export the mask to XNAT as a DICOM-SEG ROI collection;
5. wait for the `export_mask` Container Service command and show the NIfTI it wrote beside the
   DICOM-SEG.

The clicks are chosen before recording (`choose_clicks.py`), against the same server the viewer
calls: the first is always positive and deepest inside the spleen (located with
`segmentation_spleen`); extra positive and negative clicks are searched, fewest first, until the
slice's Dice against the session's ground-truth label (or `segmentation_spleen` when there is none)
passes `TARGET_DICE` (default 0.95). The captions report that Dice after each click. Orientation matters
to SAM: on the spleen data written upright (FLIP#1495) one positive click already reaches about 0.95, while
the earlier 180°-rotated copy of the same scan spilled over the body until two negative clicks reined it in. `sam_3d` is
not offered: on the spleen data it spread over the body and ignored negative clicks.

The recording fails unless step 5 happens, so a finished video is also an end-to-end check of
MONAI Label, the viewer, the export and the converter on that trust.

## Prerequisites

- A trust up with `MONAI_LABEL=true` (see [`trust/README.md`](../README.md#monai-label-optional)),
  with `segmentation_spleen` among `MONAI_LABEL_MODELS`, and `export_mask` registered.
- `segmentation_spleen` and `sam_2d` served by that MONAI Label (`MONAI_LABEL_MODELS` must include
  `segmentation_spleen`; the SAM models register on their own).
- A spleen CT session with exactly one scan (the spleen tutorial's data): the recorder saves a new
  ROI collection on it. Sessions differ: one may need a single click, another three.
- Run it **on the trust host**. The browser runs in a container on the host network and reaches
  XNAT and MONAI Label the way a browser on that host would; MONAI Label's registered URL must
  resolve from there (the default `http://localhost:8030` does).

## Usage

```sh
make -C trust/monailabel-demo demo-video KIT=GSTT XNAT_EXPERIMENT=XNAT_E02855
# -> trust/monailabel-demo/out/monailabel-demo.mp4 (2560x1600; DEMO_VIDEO_SCALE=3 for 4K)
```

Then cut the documentation GIFs (one per chapter: `enable`, `sam-clicks`, `export`, as listed in
`docs/scripts/live-recorded-gifs.txt`) with the docs pipeline's own ffmpeg settings:

```sh
make -C trust/monailabel-demo demo-gifs   # -> out/gifs/monailabel/monailabel-<chapter>.gif
```

To publish them, fetch the other GIFs (`uv run docs/scripts/fetch_docs_gifs.py`; every recording carries
the full set), copy `out/gifs/monailabel/` into `docs/source/assets/generated/gifs/`, then publish to the
dataset branch of the PR they belong to: `uv run docs/scripts/publish_docs_gifs.py --branch pr-<N>
--source-commit <sha>` (an HF write token), and rebuild that PR's preview. Once the PR merges, CI carries
them into the dataset's `main` (CONTRIBUTING.md, "Documentation GIFs").

It signs in as the kit's `XNAT_ADMIN_USER`. On failure `out/failure.png` shows what the recorder
was looking at.

Frames come from Playwright's screencast and are encoded with ffmpeg (h264 CRF 18) rather than
Playwright's built-in VP8 recorder, which is soft at this resolution.
