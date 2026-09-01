# Process Tools — Ark+ Evaluation Helpers

This directory contains one-off processing scripts and utilities for the
Ark+ chest X-ray evaluation pipeline.  These are **not** part of the
NVFLARE app itself — they are run ahead of time to prepare assets.

## Files

### `preprocess_checkpoints.py`

Converts raw Ark+ checkpoint files into clean state dicts that match the raw
architecture exactly, so the evaluator's strict ``load_state_dict`` on the
client side accepts the broadcast weights.
Run this **before** deploying the evaluation job.

#### Processor keys

| Key | Description | Heads | Sub-key |
|---|---|---|---|---|
| `arkplus_pretrained` | Foundation model | 14,14,14,3,6,1 | `ckpt["teacher"]` |

#### Usage

```bash
# Single entry
python preprocess_checkpoints.py \
    --entry /raw/Ark6_swinLarge768_ep50.pth.tar /clean/pretrained.pt arkplus_pretrained
```

### `checkpoint_utils.py`

Holds the raw-checkpoint loading logic (`load_arkplus_state_dict`,
`_strip_key_prefix`, `_remap_downsample_keys`, `_filter_state_dict`).
Imported by `preprocess_checkpoints.py`, not by the NVFLARE app.

These functions handle ``module.`` / ``ark_model.`` key prefixes, timm
downsample layer remapping, and computed-buffer filtering — everything
needed to convert a raw Ark+ checkpoint into a ``strict=True``-compatible
state dict.
