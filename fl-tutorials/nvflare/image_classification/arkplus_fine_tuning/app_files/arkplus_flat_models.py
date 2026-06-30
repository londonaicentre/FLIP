from pathlib import Path

import torch
import torch.nn as nn
from functools import partial
from torch.hub import load_state_dict_from_url

import timm.models.vision_transformer as vit
import timm.models.swin_transformer as swin
# import timm.models.efficientnet as effinet
 
# from timm.models.helpers import load_state_dict

from arkplus_flat_utils import remap_pretrained_keys_swin


# NVFLARE's PTModelPersistenceFormatManager wraps state dicts in a nested
# format (e.g. under "model" key) when saving during FL rounds. The
# ArkPlusNVFlareWrapper.load_state_dict also injects an "ark_model."
# prefix via nn.Module mechanics. This normalises those formats so the
# pretrained weights can be loaded with strict=True into the raw
# ArkSwinTransformer.
def _normalise_checkpoint_state_dict(checkpoint, pretrained_key=None):
    if pretrained_key:
        if pretrained_key not in checkpoint:
            raise KeyError(
                f"PRETRAINED_KEY={pretrained_key!r} not found in checkpoint. "
                f"Available keys: {list(checkpoint.keys())[:20]}"
            )
        state_dict = checkpoint[pretrained_key]
    elif isinstance(checkpoint, dict) and "state_dict" in checkpoint:
        state_dict = checkpoint["state_dict"]
    elif isinstance(checkpoint, dict) and "model" in checkpoint:
        state_dict = checkpoint["model"]
    else:
        state_dict = checkpoint

    if any("module." in k for k in state_dict.keys()):
        state_dict = {k.replace("module.", "", 1): v for k, v in state_dict.items() if k.startswith("module.")}
    return state_dict


def _remap_scale_up_downsample_keys_for_timm(state_dict, model):
    """Map Ark+ scale_up checkpoint downsample keys to installed timm Swin keys.

    The Ark+ checkpoint stores downsample modules on layers.0/1/2, while the
    timm SwinTransformer available in this environment stores the same tensors
    on layers.1/2/3. Shapes verify the one-stage shift.
    """
    model_state = model.state_dict()
    remapped = {}
    used_sources = set()
    for key, value in state_dict.items():
        new_key = key
        for src, dst in (("layers.0.downsample.", "layers.1.downsample."),
                         ("layers.1.downsample.", "layers.2.downsample."),
                         ("layers.2.downsample.", "layers.3.downsample.")):
            if key.startswith(src):
                candidate = dst + key[len(src):]
                if candidate in model_state and tuple(value.shape) == tuple(model_state[candidate].shape):
                    new_key = candidate
                    used_sources.add(key)
                break
        remapped[new_key] = value

    if used_sources:
        print(f"Remapped {len(used_sources)} Ark+ scale_up downsample tensors for installed timm Swin layout.")
    return remapped


def _filter_state_dict_for_model(model, state_dict, load_backbone_only=False):
    state_dict = _remap_scale_up_downsample_keys_for_timm(state_dict, model)

    if load_backbone_only:
        state_dict = {k: v for k, v in state_dict.items() if not k.startswith("omni_heads.")}

    model_state = model.state_dict()
    compatible = {}
    skipped = []
    for key, value in state_dict.items():
        if "attn_mask" in key:
            skipped.append(key)
            continue
        if key not in model_state:
            skipped.append(key)
            continue
        if tuple(value.shape) != tuple(model_state[key].shape):
            skipped.append(key)
            continue
        compatible[key] = value

    print(f"Loading {len(compatible)} compatible pretrained tensors; skipped {len(skipped)} tensors.")
    if skipped:
        print(f"Skipped pretrained keys sample: {skipped[:20]}")
    return compatible


def _load_pretrained_weights(model, pretrained_weights, pretrained_key=None, load_backbone_only=False):
    if not pretrained_weights:
        return model

    if str(pretrained_weights).startswith("https"):
        checkpoint = load_state_dict_from_url(url=pretrained_weights, map_location="cpu")
    else:
        checkpoint_path = Path(pretrained_weights)
        if not checkpoint_path.exists():
            raise FileNotFoundError(f"Pretrained checkpoint not found: {checkpoint_path}")
        checkpoint = torch.load(str(checkpoint_path), map_location="cpu", weights_only=False)

    state_dict = _normalise_checkpoint_state_dict(checkpoint, pretrained_key=pretrained_key)
    state_dict = _filter_state_dict_for_model(
        model,
        state_dict,
        load_backbone_only=load_backbone_only,
    )
    msg = model.load_state_dict(state_dict, strict=False)
    print(f"Loaded pretrained checkpoint with msg: {msg}")
    return model


class ArkSwinTransformer(swin.SwinTransformer):
    def __init__(self, num_classes_list, projector_features = None, use_mlp=False, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert num_classes_list is not None
        
        self.projector = None 
        if projector_features:
            encoder_features = self.num_features
            self.num_features = projector_features
            if use_mlp:
                self.projector = nn.Sequential(nn.Linear(encoder_features, self.num_features), nn.ReLU(inplace=True), nn.Linear(self.num_features, self.num_features))
            else:
                self.projector = nn.Linear(encoder_features, self.num_features)

        self.omni_heads = []
        for num_classes in num_classes_list:
            self.omni_heads.append(nn.Linear(self.num_features, num_classes) if num_classes > 0 else nn.Identity())
        self.omni_heads = nn.ModuleList(self.omni_heads)

    @staticmethod
    def _global_pool(x):
        """Global-average-pool over spatial/sequence dims, leaving (B, C).

        timm's ``forward_features`` returns the unpooled feature map
        (``(B, H, W, C)`` for Swin) — pooling normally happens in
        ``forward_head``, which this model bypasses in favour of its own
        omni heads.  Without this, the heads would emit per-location
        outputs instead of one prediction per image.  Mirrors the head's
        default ``global_pool='avg'``.  A no-op if already ``(B, C)``.
        """
        if x.ndim > 2:
            x = x.mean(dim=tuple(range(1, x.ndim - 1)))
        return x

    def forward(self, x, head_n=None):
        x = self.forward_features(x)
        x = self._global_pool(x)
        if self.projector:
            x = self.projector(x)
        if head_n is not None:
            return x, self.omni_heads[head_n](x)
        else:
            return [head(x) for head in self.omni_heads]
    
    def generate_embeddings(self, x, after_proj = True):
        x = self.forward_features(x)
        x = self._global_pool(x)
        if after_proj:
            x = self.projector(x)
        return x

def build_omni_model_from_checkpoint(args, num_classes_list, key):
    if args.model_name == "swin_base": #swin_base_patch4_window7_224
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, patch_size=4, window_size=7, embed_dim=128, depths=(2, 2, 18, 2), num_heads=(4, 8, 16, 32))
    elif args.model_name == "swin_large": #swin_large_patch4_window7_224
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, patch_size=4, window_size=7, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    elif args.model_name == "swin_large_384": #swin_large_patch4_window12_384
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, img_size=getattr(args, "input_size", 384), patch_size=4, window_size=12, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    elif args.model_name == "swin_large_768": #swin_large_patch4_window12_384
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, img_size =768, patch_size=4, window_size=12, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    model = _load_pretrained_weights(
        model,
        getattr(args, "pretrained_weights", None),
        pretrained_key=key,
        load_backbone_only=bool(getattr(args, "load_backbone_only", False)),
    )

    return model

def build_omni_model(args, num_classes_list):
    if args.model_name == "swin_base": #swin_base_patch4_window7_224
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, patch_size=4, window_size=7, embed_dim=128, depths=(2, 2, 18, 2), num_heads=(4, 8, 16, 32))
    elif args.model_name == "swin_large": #swin_large_patch4_window7_224
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, patch_size=4, window_size=7, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    elif args.model_name == "swin_large_384": #swin_large_patch4_window12_384
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, img_size=getattr(args, "input_size", 384), patch_size=4, window_size=12, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    elif args.model_name == "swin_large_768": #swin_large_patch4_window12_384
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, img_size =768, patch_size=4, window_size=12, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    elif args.model_name == "swin_large_1152": #swin_large_patch4_window12_384
        model = ArkSwinTransformer(num_classes_list, args.projector_features, args.use_mlp, img_size =1152, patch_size=4, window_size=12, embed_dim=192, depths=(2, 2, 18, 2), num_heads=(6, 12, 24, 48))
    model = _load_pretrained_weights(
        model,
        getattr(args, "pretrained_weights", None),
        pretrained_key=getattr(args, "pretrained_key", None),
        load_backbone_only=bool(getattr(args, "load_backbone_only", False)),
    )

    return model

def save_checkpoint(state,filename='model'):

    torch.save(state, filename + '.pth.tar')
