# Evaluation of a 3D segmentation model

This application contains an example of evaluating a model in a federated fashion using FLIP.
It is paired to the `image_segmentation/3d_spleen_segmentation_evaluation` app: once the model is trained, it can be tested
with this app.

## Key elements

This app uses the `job_type=evaluation`. With this job type, you have to pass:

- an `evaluator` script which performs inference
- auxiliary `models.py` which instances the model or models to do inference on. A dictionary `model_paths` must point to the instance of the different models that will be tested.
- optionally, a `transforms.py` script to handle data transformations

In the `config.json` file, you have to have the following fields:

- `models`: a dictionary of dictionaries where they key is the model name, and the value is a dictionary with keys `checkpoint`, pointing to the weights file for this specific model, and a `path` key, pointing to the name of the model in the `model_paths` dictionary (see `models.py`).
- `evaluation_output`: this contains the structure of the evaluation output. In the example provided, the output is: `{"spleen": {"mean_dice": 0.0, "raw_dice": []}}`. When you populate this output in the `evaluator.py` file, its parent class will check that the function is outputting a dictionary like that one, where the contents can only be float values (e.g. no strings).

## Test it with the spleen MSD dataset

You need to download the spleen data (see `testing/data_download_utils/preprocess_spleen_data.py`) from the Medical Imaging Segmentation decathlon.

You will also need to download a pre-trained model by running: `make download-checkpoints`. This will download the checkpoint of the pre-trained UNet into `app_files`.
After that, just do:

`make run`

Should run this code with a pre-trained U-Net network on the MSD dataset.
