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

# Evaluation of a model with FLIP

This job type is to test one or more models on different sites.

## What's the logic?

The evaluation pipeline loads the model weights in the server side, and then sends the models to every site.
The custom-code `evaluator.py` is used to then obtain the metrics, which are saved under `evaluation_results.json`
file in the server evaluation folder.

## What does the user upload?

The user uploads:

- `evaluator.py`: this contains the testing pipeline. The method `execute` is called by the parent of this class,
and retrieves a DXO with the metrics.
- `models.py`: this contains the code to instance the model(s) that are to be tested.
- [checkpoints]: any model has to have its checkpoint uploaded under `pt` format.
- `transforms.py`: support file to define data transforms and other data related thing.
- `config.json`: configuration for the model. The following fields are required in this pipeline:
  - `models`. For each element of this class, `checkpoint` and `path` are mandatory. Checkpoint is the name of the
    pt file for this specific model. 'path' is the key to the function that defines this model in `models.py`
    (in dictionary `model_paths`).
  - `evaluation_output`: this is the skeletton of the evaluation output, defining, the output that you'll get
    for each model. Your evaluator.py output will be checked against this, to ensure that additional fields are not
    being added. All results should be floats (no text can be passed).

## Test it with the spleen MSD dataset

`make test-evaluation`

Should run this code with a pre-trained U-Net network on the MSD dataset.
