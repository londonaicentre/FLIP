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

from monai.networks.nets import UNet
from nvflare.app_opt.monai import decomposers


class FLUNet(UNet):
    """A UNet wrapper that stores init params for FedAvgRecipe serialization.

    FedAvgRecipe's PTModel persister captures nn.Module state_dict for weight
    exchange, but it needs the constructor parameters to reconstruct the model
    architecture on the server. FLUNet stores these as instance attributes so
    the PT serialiser can infer them from the model instance.

    All parameters match monai.networks.nets.UNet defaults for 3D spleen
    segmentation.
    """

    def __init__(
        self,
        spatial_dims: int = 3,
        in_channels: int = 1,
        out_channels: int = 2,
        channels: list[int] | None = None,
        strides: list[int] | None = None,
        num_res_units: int = 2,
        norm: str = "batch",
    ) -> None:
        # Store configuration for JobAPI serialisation
        self.spatial_dims = spatial_dims
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.channels = channels or [16, 32, 64, 128, 256]
        self.strides = strides or [2, 2, 2, 2]
        self.num_res_units = num_res_units
        self.norm = norm

        super().__init__(
            spatial_dims=self.spatial_dims,
            in_channels=self.in_channels,
            out_channels=self.out_channels,
            channels=self.channels,
            strides=self.strides,
            num_res_units=self.num_res_units,
            norm=self.norm,
        )

        # Register MONAI decomposers so NVFlare can serialise MONAI types
        decomposers.register_monai_types()
