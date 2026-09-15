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
# Copied verbatim from
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/network/nnunet_arch.py,
# flattened into this tutorial's directory. That file is itself derived from nnU-Net
# v2 (DKFZ); its original Apache 2.0 notice is retained below.

#    Copyright 2020 Division of Medical Image Computing, German Cancer Research Center (DKFZ), Heidelberg, Germany
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
#
#    Modified by: Yovin Yahathugoda (yovin.yahathugoda@kcl.ac.uk)
#    Modifications: Adapted for MambaX-Net project with custom dataset fingerprinting
#    for medical image segmentation. Original nnUNet v2 dataset fingerprint extractor
#    modified to support custom data loading and 3D/2D segmentation planning.

import torch.nn as nn
from nnunetv2.utilities.get_network_from_plans import get_network_from_plans
from torch._dynamo import OptimizedModule


def build_network_architecture(
    architecture_class_name: str,
    arch_init_kwargs: dict,
    arch_init_kwargs_req_import,
    num_input_channels: int,
    num_output_channels: int,
    enable_deep_supervision: bool = True,
) -> nn.Module:
    """
    This is where you build the architecture according to the plans. There is no obligation to use
    get_network_from_plans, this is just a utility we use for the nnU-Net default architectures. You can do what
    you want. Even ignore the plans and just return something static (as long as it can process the requested
    patch size)
    but don't bug us with your bugs arising from fiddling with this :-P
    This is the function that is called in inference as well! This is needed so that all network architecture
    variants can be loaded at inference time (inference will use the same nnUNetTrainer that was used for
    training, so if you change the network architecture during training by deriving a new trainer class then
    inference will know about it).

    If you need to know how many segmentation outputs your custom architecture needs to have, use the following snippet:
    > label_manager = plans_manager.get_label_manager(dataset_json)
    > label_manager.num_segmentation_heads
    (why so complicated? -> We can have either classical training (classes) or regions. If we have regions,
    the number of outputs is != the number of classes. Also there is the ignore label for which no output
    should be generated. label_manager takes care of all that for you.)

    """
    return get_network_from_plans(
        architecture_class_name,
        arch_init_kwargs,
        arch_init_kwargs_req_import,
        num_input_channels,
        num_output_channels,
        allow_init=True,
        deep_supervision=enable_deep_supervision,
    )


def set_deep_supervision_enabled(enabled: bool, is_ddp=False, network=None):
    """
    This function is specific for the default architecture in nnU-Net. If you change the architecture, there are
    chances you need to change this as well!
    """
    if is_ddp:
        mod = network.module
    else:
        mod = network
    if isinstance(mod, OptimizedModule):
        mod = mod._orig_mod

    mod.decoder.deep_supervision = enabled
    return network
