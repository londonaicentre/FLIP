<!--
    Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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

# Adaptive Federated Optimization (FedOpt)

## Overview

This app differs from Federated Averaging in that the server has a persistent optimizer and, optionally, a learning rate scheduler that update the global weight running the optimizer on the difference between global and local gradients.

It is based on the paper "Adaptive Federated Optimization" by Reddi S. et al. (2020), and on NVFlare's implementation in their CIFAR10 tutorial available at: <https://github.com/NVIDIA/NVFlare/tree/2.4/examples/advanced/cifar10/cifar10-sim/jobs/cifar10_fedopt/cifar10_fedopt>.

## Technical differences

This app differs in that the Shareable Generator at the server is different and holds an optimizer and a learning rate scheduler that can be customised by the user.

Currently it's defaulted as:

- Adam
- Learning rate: 0.5 (note that the learning rate has to be between 0.1 and 1.0)
- Exponential decay learning rate scheduler with gamma 0.95

## Changes to trainer / validator

To use FedOpt, the trainer has to commit the weights differences between the local and global models in the form of `DataKind` `WEIGHT_DIFF`.

Make sure to compute weight differences using the `get_model_weights_diff` function from `flip.utils.model_weights_handling`.

Note that the validator does not need to be changed.
