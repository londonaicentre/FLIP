# Process Tools — Ark+ Checkpoint Helpers

Host-side scripts that prepare the Ark+ foundation checkpoint for the fine-tuning job. These are **not**
part of the FLARE app itself — they are run ahead of time to prepare assets. See the
[tutorial README](../README.md) for where this step fits.

## Files

### `preprocess_checkpoints.py`

Converts the raw Ark+ foundation checkpoint into a backbone-only state dict. The `omni_heads.*`
classification heads are stripped, so fine-tuning trains its own classifier head from scratch.

Run this **before** exporting or running the job. `make prepare-checkpoint` runs it for you.

#### Processor keys

| Key | Description | Heads | Sub-key |
|---|---|---|---|
| `arkplus_pretrained` | Foundation model | 14,14,14,3,6,1 | `ckpt["teacher"]` |

**Heads** lists the number of output classes of each classification head in the raw checkpoint (stripped
from the output). **Sub-key** is where the state dict is read from in the loaded checkpoint.

#### Usage

Each `--entry` takes three values: `<raw checkpoint> <output path> <processor key>`. Repeat `--entry` to
convert several checkpoints in one run.

```bash
python preprocess_checkpoints.py \
    --entry /raw/Ark6_swinLarge768_ep50.pth.tar /clean/pretrained_weights.pt arkplus_pretrained
```

### `checkpoint_utils.py`

Holds the raw-checkpoint loading logic (`load_arkplus_state_dict`,
`_strip_key_prefix`, `_remap_downsample_keys`, `_filter_state_dict`).
Imported by `preprocess_checkpoints.py`, not by the FLARE app.

These functions strip the `module.` and `ark_model.` key prefixes, remap
timm downsample layer keys, and filter out computed buffers. Together they
convert a raw Ark+ checkpoint into a state dict that loads with `strict=True`.
