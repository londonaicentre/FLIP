# X-Ray classification

This code allows to train a classifier of X-Ray pathologies.

## Data requirements

This application is compatible with the new MI-CDM. The input dataframe to the `trainer` and `validator` files should contain column names corresponding to those passed in the `config.json` `LESIONS` field.

Example:

If `LESIONS` is `{"0": "Effusion", "1": "Edema", "-1": "Lungs in normal arrangement"}`, the dataframe input to the training and validation scripts should contain a column for each of these values. 
Note that key '-1' is reserved for "Normality". A positive value of this columns implies that every other will be set to 0. This tag does not count as part of the classification.

THe `config.json` file must also have a `value_to_numerical` argument mapping values 0 and 1 to how these values are represented in the dataframe (example: 1->`Yes`, 0->`No`). 


The images have to  DICOM (although this can be easily modified by changing the resource type and the package used to load images from `pydicom` to `nibabel`). We consider that the images are 2D, grayscale.

## The network

The network used for this application is a DenseNet-121 pre-trained on ImageNet that is implemented using MONAI.

## The training logic

We use Binary-Cross-Entropy and mask the dontcares to not take them into account on the loss calculation.
We have a validation round within the `trainer.py`, which runs every few local rounds (`config.json` `VALIDATE_EVERY` field), and then the test within `validator.py`. The splits taken for training, validation and testing are consistent (randomisation happens after).

## Metrics

For metrics, we obtain the loss value, as well as precision, recall and F1-Score.

## How to run?

- set environment variable DEV_DATAFRAME to the path of your CSV containing the OMOP data (with an accession_id and raiology_note columns).
- set environment variable DEV_IMAGES_DIR to the path where your images are. Note that currently DICOM is the supported format, so images have to be .dcm and must be contained in folders named exactly as the accession ID.
