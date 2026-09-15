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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/nifti_utilities.py,
# flattened into this tutorial's directory.

import json
from pathlib import Path
from typing import Any, Generator, List, Optional, Tuple, Union

import monai
import nibabel as nib
import numpy as np
import pandas as pd
import SimpleITK as sitk
from nibabel.orientations import axcodes2ornt, io_orientation, ornt_transform
from tqdm.auto import tqdm


def path_generator(input_folder: Union[str, Path], extension: str = ".nii.gz") -> Generator[Path, None, None]:
    """Returns a generator to iterate through filepaths

    Args:
        input_folder (str): path to the input folder
        extension (str): file extension to search for

    Returns:
        filepaths (generator): generator to iterate through filepaths
    """
    input_folder = Path(input_folder)
    filepaths = input_folder.glob(f"*{extension}")
    return filepaths


def check_orientation(nii: nib.Nifti1Image) -> nib.Nifti1Image:
    """Reorients a nifti image to RAS if it isn't already

    Args:
        nii (nibabel.nifti1.Nifti1Image): nifti image in any orientation

    Returns:
        nii (nibabel.nifti1.Nifti1Image): nifti image in RAS orientation. If
            the image was reoriented, the original orientation is stored as
            a JSON header extension under the key "original_orientation"
    """
    array = nii.get_fdata()
    x, y, z = nib.aff2axcodes(nii.affine)
    if x == "R" and y == "A" and z == "S":
        return nii
    else:
        current_ornt = io_orientation(nii.affine)
        desired_ornt = axcodes2ornt("RAS")
        transform = ornt_transform(current_ornt, desired_ornt)
        data = nib.orientations.apply_orientation(array, transform)
        # create new RAS affine matrix
        new_affine = nii.affine.copy()
        for i in range(3):
            axis = int(transform[i, 0])
            if transform[i, 1] < 0:
                new_affine[:3, axis] = -nii.affine[:3, i]
            else:
                new_affine[:3, axis] = nii.affine[:3, i]

        nii = nib.Nifti1Image(data, new_affine, header=nii.header)
        # add original orientation into nifti header extension
        nii.header.extensions.append(
            nib.nifti1.Nifti1Extension(40, json.dumps({"original_orientation": (x, y, z)}).encode("utf-8"))
        )
        return nii


def load(
    input_path: Union[str, Path],
) -> Tuple[str, Union[nib.Nifti1Image, sitk.Image]]:
    """loads nifti image and returns the name, header, affine and data array

    Args:
        input_path (str): path to the input file

    Returns:
        name (str): name of the file
        nii (nibabel.nifti1.Nifti1Image): nifti image
        nii_affine (numpy.ndarray): affine of the nifti image
        data (numpy.ndarray): data array
    """
    name = Path(input_path).name
    extension = Path(input_path).suffix
    if extension == ".mha":
        reader = sitk.ImageFileReader()
        reader.SetFileName(str(input_path))
        nii = reader.Execute()
        return name, nii
    else:
        reader = monai.data.NibabelReader()
        nii = reader.read(str(input_path))

        nii = check_orientation(nii)

        return name, nii


def load_preserve_orientation(
    input_path: Union[str, Path],
) -> Tuple[str, Union[nib.Nifti1Image, sitk.Image]]:
    """loads nifti image without changing orientation

    Args:
        input_path (str): path to the input file

    Returns:
        name (str): name of the file
        nii (nibabel.nifti1.Nifti1Image): nifti image in original orientation
    """
    name = Path(input_path).name
    extension = Path(input_path).suffix
    if extension == ".mha":
        reader = sitk.ImageFileReader()
        reader.SetFileName(str(input_path))
        nii = reader.Execute()
        return name, nii
    else:
        reader = monai.data.NibabelReader()
        nii = reader.read(str(input_path))
        return name, nii


def convert_orientation(nii: nib.Nifti1Image, target_orientation: str = "LAS") -> nib.Nifti1Image:
    """Convert image from current orientation to specified target orientation

    Args:
        nii: NIfTI image in any orientation
        target_orientation: Target orientation code (e.g., "LAS", "RAS", "PIR", etc.)

    Returns:
        nii: NIfTI image in target orientation
    """
    # Get current orientation as axis codes from affine
    current_axcodes = nib.aff2axcodes(nii.affine)
    current_orientation_str = "".join(current_axcodes)

    # Check if already in target orientation
    if current_orientation_str == target_orientation:
        return nii

    array = nii.get_fdata()

    # Use the actual data orientation for transformation
    current_ornt = axcodes2ornt(current_orientation_str)
    desired_ornt = axcodes2ornt(target_orientation)
    transform = ornt_transform(current_ornt, desired_ornt)

    data = nib.orientations.apply_orientation(array, transform)
    new_affine = nii.affine.copy()

    for i in range(3):
        axis = int(transform[i, 0])
        if transform[i, 1] < 0:
            new_affine[:3, axis] = -nii.affine[:3, i]
        else:
            new_affine[:3, axis] = nii.affine[:3, i]

    nii = nib.Nifti1Image(data, new_affine, header=nii.header)

    return nii


def save(
    output_path: Union[str, Path],
    nii_header: Any,
    data: np.ndarray,
    dtype: np.dtype = np.float64,
    channel_dim: Optional[int] = None,
    verbose: bool = True,
) -> None:
    """Saves the data array to a nifti file

    Args:
        output_path (str): path to the output file
        nii_header (dict): header of the nifti image
        data (numpy.ndarray): data array
        dtype (numpy.dtype): data type to save the data array
        channel_dim (int): channel dimension of the data array.
            Defaults to 0. None indicates data without any
            channel dimension.
        verbose (bool): whether to print the output

    Returns:
        None
    """
    writer = monai.data.NibabelWriter(output_dtype=dtype)
    writer.set_data_array(data, channel_dim=channel_dim)
    writer.set_metadata(nii_header)
    writer.write(str(output_path), verbose=verbose)
    return None


def read_mha_file(file_path: Union[str, Path], hbv: bool = True) -> Tuple[Optional[str], sitk.ImageFileReader]:
    """Reads the b-value from the mha file

    Args:
        file_path (str): path to the mha file

    Returns:
        b_value (str): b-value
    """
    reader = sitk.ImageFileReader()
    reader.SetFileName(file_path)
    reader.LoadPrivateTagsOn()
    reader.ReadImageInformation()

    if hbv:
        b_value = reader.GetMetaData("0018|9087")  # b-value
        return b_value, reader
    else:
        return None, reader


def get_bvalues(
    raw_path: Union[str, Path],
    df_path: Union[str, Path],
    out_path: Union[str, Path],
    json_path: Union[str, Path],
    extension: str = ".mha",
) -> pd.DataFrame:
    """Reads the b-values from the mha files and adds them to the dataframe

    Args:
        raw_path (str): path to the raw data
        df_path (str): path to the dataframe
        out_path (str): path to the output dataframe
        json_path (str): path to the json file
        extension (str): file extension to search for

    Returns:
        df (pandas.DataFrame): dataframe with b-values
    """
    print("Reading b-values from mha files")
    b_values = []
    meta_data = {}

    df = pd.read_csv(df_path)

    for _, row in tqdm(df.iterrows(), total=len(df)):
        temp_meta_data = {}

        folder = row["patient_id"]
        filepaths = list(Path(raw_path).glob(f"{folder}/*{extension}"))
        try:
            for file in filepaths:
                if str(file).endswith(f"_hbv{extension}"):
                    image_path = str(file)
                    patient_id, study_id, _ = image_path.split("/")[-1].split("_")
                    assert str(patient_id) == str(
                        row["patient_id"]
                    ), f'patient id does not match{patient_id} != {row["patient_id"]}'
                    assert str(study_id) == str(
                        row["study_id"]
                    ), f'study id does not match: {study_id} != {row["study_id"]} for patient id: {patient_id}'
                    break
        except:
            # in case a patient has multiple studies
            for file in filepaths:
                if str(file).endswith(f"_hbv{extension}"):
                    image_path = f'{raw_path}/{folder}/{folder}_{row["study_id"]}_hbv{extension}'

        b_value, _ = read_mha_file(image_path)
        b_values.append(b_value)

        _, reader = read_mha_file(image_path.replace("hbv", "t2w"), hbv=False)  # get the meta data for T2W

        header = reader.GetMetaDataKeys()
        for key in header:
            temp_meta_data[key] = reader.GetMetaData(key)

        temp_meta_data["size"] = reader.GetSize()
        temp_meta_data["spacing"] = reader.GetSpacing()
        temp_meta_data["origin"] = reader.GetOrigin()
        temp_meta_data["direction"] = reader.GetDirection()
        temp_meta_data["dimension"] = reader.GetDimension()

        name_splits = image_path.split("/")[-1].split("_")
        key_name = f"{name_splits[0]}_{name_splits[1]}"

        meta_data[key_name] = temp_meta_data

    with open(json_path, "w") as f:
        json.dump(meta_data, f)

    df["b_values"] = b_values
    df.to_csv(out_path, index=False)
    return df


def possible_patch_size(
    image_size: Tuple[int, int, int],
    suggested_patch_sz: Tuple[int, int, int],
    patch_sizes: List[int] = [64, 128, 256, 512],
) -> Tuple[Tuple[int, int, int], List[Tuple[int, int, int]]]:
    """Determines a padded crop size and the candidate patch sizes it fits

    image_size[1] (the in-plane dimension) is padded up to the next even
    number to give the crop's x/y extent, and each candidate in patch_sizes
    that evenly divides that extent is kept as a possible in-plane patch
    size, paired with the suggested z patch size.

    Args:
        image_size (Tuple[int, int, int]): size of the image
        suggested_patch_sz (Tuple[int, int, int]): suggested patch size,
            whose first element (z) is used
        patch_sizes (List[int]): candidate in-plane patch sizes to test

    Returns:
        crop_size (Tuple[int, int, int]): padded (x, y, z) crop size, with
            x/y rounded up to even and z equal to suggested z patch size + 8
        sizes (List[Tuple[int, int, int]]): (patch_size, patch_size, z)
            tuples for each candidate patch size that evenly divides the
            padded x/y extent
    """
    x_remainder = image_size[1] % 2
    z_remainder = image_size[0] % 2

    suggested_z_patch = suggested_patch_sz[0]

    new_x = int(image_size[1] + x_remainder)
    new_y = new_x
    new_z = suggested_z_patch

    sizes = []
    for patch_size in patch_sizes:
        if new_x % patch_size == 0:
            sizes.append((patch_size, patch_size, new_z))
    return (new_x, new_y, suggested_z_patch + 8), sizes
