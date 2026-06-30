"""ArkSwinTransformer model definitions for Ark+.

This file only contains the model class definitions.  Weight-loading
utilities live in ``models.py``.
"""

import torch.nn as nn
import timm.models.swin_transformer as swin


class ArkSwinTransformer(swin.SwinTransformer):
    """Swin Transformer with multiple omni classifier heads.

    Each entry in *num_classes_list* creates a separate linear head.
    ``forward(x, head_n=None)`` returns a list of all head outputs;
    ``forward(x, head_n=i)`` returns ``(features, head_i_output)``.
    """

    def __init__(
        self, num_classes_list, projector_features=None, use_mlp=False, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        assert num_classes_list is not None

        self.projector = None
        if projector_features:
            encoder_features = self.num_features
            self.num_features = projector_features
            if use_mlp:
                self.projector = nn.Sequential(
                    nn.Linear(encoder_features, self.num_features),
                    nn.ReLU(inplace=True),
                    nn.Linear(self.num_features, self.num_features),
                )
            else:
                self.projector = nn.Linear(encoder_features, self.num_features)

        heads = []
        for num_classes in num_classes_list:
            heads.append(
                nn.Linear(self.num_features, num_classes)
                if num_classes > 0
                else nn.Identity()
            )
        self.omni_heads = nn.ModuleList(heads)

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
        return [head(x) for head in self.omni_heads]

    def generate_embeddings(self, x, after_proj=True):
        x = self.forward_features(x)
        x = self._global_pool(x)
        if after_proj and self.projector:
            x = self.projector(x)
        return x



