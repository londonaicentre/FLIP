# X-Ray classification

This code allows to train a classifier of X-Ray pathologies.

## Data requirements

This application is compatible with the new MI-CDM. The input dataframe to the `trainer` and `validator` files should contain column names corresponding to those passed in the `config.json` `LESIONS` field.

Example:

If `LESIONS` is `{"0": "Effusion", "1": "Edema", "-1": "Lungs in normal arrangement"}`, the dataframe input to the training and validation scripts should contain a column for each of these values.
Note that key '-1' is reserved for "Normality". A positive value of this columns implies that every other will be set to 0. This tag does not count as part of the classification.

THe `config.json` file must also have a `value_to_numerical` argument mapping values 0 and 1 to how these values are represented in the dataframe (example: 1->`Yes`, 0->`No`).

The images have to  DICOM (although this can be easily modified by changing the resource type and the package used to load images from `pydicom` to `nibabel`). We consider that the images are 2D, grayscale.

Bear in mind that **class imabalance** can lead to NaN metrics because of lack of representation for some classes.
Even though NaNs are handled gracefully, ensure that you use N=300 approximately per site. 

## The network

The network used for this application is a DenseNet-121 pre-trained on ImageNet that is implemented using MONAI.

## The training logic

We use Binary-Cross-Entropy and mask the dontcares to not take them into account on the loss calculation.
We have a validation round within the `trainer.py`, which runs every few local rounds (`config.json` `VALIDATE_EVERY` field), and then the test within `validator.py`. The splits taken for training, validation and testing are consistent (randomisation happens after).

## Metrics

For metrics, we obtain the loss value, as well as precision, recall and F1-Score.

## How to run?

The quickest path uses the published reference dataset on Hugging Face. From the repo root:

```bash
make -C fl-tutorials download-xray-data          # fetch + lay out data/xrays_mini_300/
make -C fl-tutorials run-tutorial TUTORIAL=xray_classification
```

`download-xray-data` pulls `aicentreflip/flip-fl-base-test-data` and normalises it into
`fl-tutorials/nvflare/data/xrays_mini_300/` (gitignored), matching this tutorial's `.env.app` defaults
(`DEV_IMAGES_DIR`, `DEV_DATAFRAME`). Requires GPUs + the `flare-fl-base` image to run the simulator.

To run against your **own** data instead, point the `.env.app` (or environment) values at it:

- set `DEV_DATAFRAME` to your CSV containing the OMOP data (with an `accession_id` column and the lesion columns named in `config.json`).
- set `DEV_IMAGES_DIR` to where your images are. DICOM is the supported format, so images must be `.dcm` and contained in folders named exactly as the accession ID.
