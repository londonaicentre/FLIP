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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/utilities/dataset_fingerprint.py
# (as of 9c4be96, "fix bug in dataset fingerprint calculation"), flattened into this
# tutorial's directory. That file is itself derived from nnU-Net v2 (DKFZ); its
# original Apache 2.0 notice is retained below.

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
#    Modifications: Adapted for MambaX-Net project (https://github.com/yoviny/MambaX-Net) with custom dataset fingerprinting
#    for medical image segmentation. Original nnUNet v2 dataset fingerprint extractor
#    modified to support custom data loading.

import multiprocessing
import os
from os.path import join
from time import sleep
from typing import Any, Dict, List, Tuple

import numpy as np
from batchgenerators.utilities.file_and_folder_operations import (
    isfile,
    load_json,
    save_json,
)
from einops import rearrange
from nnunetv2.preprocessing.cropping.cropping import crop_to_nonzero
from tqdm import tqdm


class DatasetFingerprintExtractor(object):
    """Extracts and caches a dataset fingerprint used for experiment planning.

    Iterates the cases yielded by `dataloader`, analyzing each one in parallel to
    collect foreground intensity samples/statistics, spacing, and shape after
    cropping to the non-zero region, then aggregates these into a single fingerprint
    dict that is cached to `dataset_fingerprint.json` in `output_folder`.

    Attributes:
        output_folder (str): Directory the fingerprint json is read from/written to.
        num_channels (int): Number of image channels/modalities.
        num_processes (int): Number of worker processes used to analyze cases.
        dataloader (Any): Iterable yielding (image, mask) pairs, one per case.
        verbose (bool): If True, disables the progress bar shown during `run`.
        num_foreground_voxels_for_intensitystats (float): Target total number of
            foreground voxels to sample across the whole dataset for intensity stats.
    """

    def __init__(
        self,
        output_folder: str,
        dataloader: Any,
        channels: int,
        num_processes: int = 8,
        verbose: bool = False,
    ) -> None:
        """Store the settings needed to extract the dataset fingerprint.

        Doesn't do any extraction itself — that happens in `run()`. This
        just holds onto the output folder, dataloader, channel count and
        worker count for later use.

        Philosophy here is to do only what we really need. Don't store stuff that we can easily read from somewhere
        else. Don't compute stuff we don't need (except for intensity_statistics_per_channel)
        """
        self.verbose = verbose

        self.output_folder = output_folder
        self.num_channels = channels
        self.num_processes = num_processes
        self.dataloader = dataloader

        # We don't want to use all foreground voxels because that can accumulate a lot of data (out of memory). It is
        # also not critically important to get all pixels as long as there are enough. Let's use 10e7 voxels in total
        # (for the entire dataset)
        self.num_foreground_voxels_for_intensitystats = 10e7

    @staticmethod
    def collect_foreground_intensities(
        segmentation: np.ndarray,
        images: np.ndarray,
        seed: int = 1234,
        num_samples: int = 10000,
    ) -> Tuple[List[np.ndarray], List[Dict[str, float]]]:
        """
        images=image with multiple channels = shape (c, x, y(, z))
        """
        assert images.ndim == 4
        assert segmentation.ndim == 4

        assert not np.any(
            np.isnan(segmentation)
        ), "Segmentation contains NaN values. grrrr.... :-("
        assert not np.any(np.isnan(images)), "Images contains NaN values. grrrr.... :-("

        rs = np.random.RandomState(seed)

        intensities_per_channel = []
        # we don't use the intensity_statistics_per_channel at all, it's just something that might be nice to have
        intensity_statistics_per_channel = []

        # segmentation is 4d: 1,x,y,z. We need to remove the empty dimension for the following code to work
        foreground_mask = segmentation[0] > 0

        for i in range(len(images)):
            foreground_pixels = images[i][foreground_mask]
            num_fg = len(foreground_pixels)
            # sample with replacement so that we don't get issues with cases that have less than num_samples
            # foreground_pixels. We could also just sample less in those cases but that would than cause these
            # training cases to be underrepresented
            intensities_per_channel.append(
                rs.choice(foreground_pixels, num_samples, replace=True)
                if num_fg > 0
                else []
            )
            intensity_statistics_per_channel.append(
                {
                    "mean": np.mean(foreground_pixels) if num_fg > 0 else np.nan,
                    "median": np.median(foreground_pixels) if num_fg > 0 else np.nan,
                    "min": np.min(foreground_pixels) if num_fg > 0 else np.nan,
                    "max": np.max(foreground_pixels) if num_fg > 0 else np.nan,
                    "percentile_99_5": (
                        np.percentile(foreground_pixels, 99.5) if num_fg > 0 else np.nan
                    ),
                    "percentile_00_5": (
                        np.percentile(foreground_pixels, 0.5) if num_fg > 0 else np.nan
                    ),
                }
            )

        return intensities_per_channel, intensity_statistics_per_channel

    @staticmethod
    def analyze_case(
        image: Any, mask: Any, num_samples: int = 10000
    ) -> Tuple[
        Tuple[int, ...], List[float], List[np.ndarray], List[Dict[str, float]], float
    ]:
        """Compute cropping/spacing/intensity statistics for a single case.

        Loads the image and mask data, reorders axes to (c, d, h, w), crops both
        to the non-zero region of the mask, and samples foreground intensities
        (and their statistics) per channel from the cropped data.

        Args:
            image (Any): Nibabel-like image object exposing `get_fdata()` and
                `header` (used to read voxel spacing).
            mask (Any): Nibabel-like segmentation object exposing `get_fdata()`
                and `header`.
            num_samples (int, optional): Number of foreground voxels to sample
                per channel for the intensity statistics. Defaults to 10000.

        Returns:
            Tuple containing:
                shape_after_crop (Tuple[int, ...]): Image shape after cropping
                    to the non-zero region.
                spacings_for_nnunet (List[float]): Voxel spacing (d, h, w),
                    read from the image header and reordered to match
                    `shape_after_crop`.
                foreground_intensities_per_channel (List[np.ndarray]): Sampled
                    foreground intensity values, one array per channel.
                foreground_intensity_stats_per_channel (List[Dict[str, float]]):
                    Per-channel mean/median/min/max/percentile statistics of the
                    foreground intensities.
                relative_size_after_cropping (float): Ratio of the cropped
                    volume to the original (uncropped) volume.
        """
        images, properties_images = image.get_fdata(), image.header
        segmentation, _ = mask.get_fdata(), mask.header

        images = rearrange(images, "c h w d -> c d h w")
        segmentation = rearrange(segmentation, "c h w d -> c d h w")

        # we no longer crop and save the cropped images before this is run. Instead we run the cropping on the fly.
        # Downside is that we need to do this twice (once here and once during preprocessing). Upside is that we don't
        # need to save the cropped data anymore. Given that cropping is not too expensive it makes sense to do it this
        # way. This is only possible because we are now using our new input/output interface.
        # data_cropped, seg_cropped, bbox = crop_to_nonzero(images, segmentation, nonzero_label=1)
        data_cropped, seg_cropped, bbox = crop_to_nonzero(
            images, segmentation
        )  # setting non_zero_label to 1 is not working

        foreground_intensities_per_channel, foreground_intensity_stats_per_channel = (
            DatasetFingerprintExtractor.collect_foreground_intensities(
                seg_cropped, data_cropped, num_samples=num_samples
            )
        )

        zooms = [float(i) for i in properties_images.get_zooms()]
        # load() prepends a dummy zoom for the channel axis, leaving (1.0, h, w, d)
        h_spacing, w_spacing, d_spacing = zooms[1:4] if len(zooms) == 4 else zooms[:3]
        # match the `c h w d -> c d h w` reorder applied to the arrays above
        spacings_for_nnunet = [d_spacing, h_spacing, w_spacing]

        shape_before_crop = images.shape[1:]
        shape_after_crop = data_cropped.shape[1:]
        relative_size_after_cropping = np.prod(shape_after_crop) / np.prod(
            shape_before_crop
        )
        return (
            shape_after_crop,
            spacings_for_nnunet,
            foreground_intensities_per_channel,
            foreground_intensity_stats_per_channel,
            relative_size_after_cropping,
        )

    def run(self, overwrite_existing: bool = False) -> Dict[str, Any]:
        """Compute (or load a cached) dataset fingerprint.

        If `dataset_fingerprint.json` does not already exist in `output_folder`,
        or `overwrite_existing` is True, analyzes every case in `dataloader` in
        parallel via `analyze_case`, aggregates the per-case results (spacings,
        shapes after cropping, concatenated foreground intensities reduced to
        per-channel statistics, and the median relative size after cropping)
        into a fingerprint dict, and saves it to `dataset_fingerprint.json`
        (removing the partial file again if saving fails). Otherwise, loads and
        returns the existing fingerprint file.

        Args:
            overwrite_existing (bool, optional): If True, recompute the
                fingerprint even if a cached file already exists. Defaults to
                False.

        Returns:
            Dict[str, Any]: The fingerprint, with keys "spacings",
            "shapes_after_crop", "foreground_intensity_properties_per_channel",
            and "median_relative_size_after_cropping".
        """
        preprocessed_output_folder = self.output_folder
        os.makedirs(preprocessed_output_folder, exist_ok=True)
        properties_file = join(preprocessed_output_folder, "dataset_fingerprint.json")

        if not isfile(properties_file) or overwrite_existing:
            # determine how many foreground voxels we need to sample per training case
            num_foreground_samples_per_case = int(
                self.num_foreground_voxels_for_intensitystats // len(self.dataloader)
            )

            r = []
            with multiprocessing.get_context("spawn").Pool(self.num_processes) as p:
                for i, (img, mask) in tqdm(
                    enumerate(self.dataloader), total=len(self.dataloader)
                ):
                    r.append(
                        p.starmap_async(
                            DatasetFingerprintExtractor.analyze_case,
                            ((img[0], mask[0], num_foreground_samples_per_case),),
                        )
                    )
                remaining = list(range(len(self.dataloader)))
                # p is pretty nifti. If we kill workers they just respawn but don't do any work.
                # So we need to store the original pool of workers.
                workers = [j for j in p._pool]
                with tqdm(
                    desc=None, total=len(self.dataloader), disable=self.verbose
                ) as pbar:
                    while len(remaining) > 0:
                        all_alive = all([j.is_alive() for j in workers])
                        if not all_alive:
                            raise RuntimeError(
                                "Some background worker is 6 feet under. Yuck. \n"
                                "OK jokes aside.\n"
                                "One of your background processes is missing. This could be because of "
                                "an error (look for an error message) or because it was killed "
                                "by your OS due to running out of RAM. If you don't see "
                                "an error message, out of RAM is likely the problem. In that case "
                                "reducing the number of workers might help"
                            )
                        done = [i for i in remaining if r[i].ready()]
                        for _ in done:
                            pbar.update()
                        remaining = [i for i in remaining if i not in done]
                        sleep(0.1)

            results = [i.get()[0] for i in r]

            shapes_after_crop = [r[0] for r in results]
            spacings = [r[1] for r in results]
            foreground_intensities_per_channel = [
                np.concatenate([r[2][i] for r in results])
                for i in range(len(results[0][2]))
            ]
            # we drop this so that the json file is somewhat human readable
            # foreground_intensity_stats_by_case_and_modality = [r[3] for r in results]
            median_relative_size_after_cropping = np.median([r[4] for r in results], 0)

            intensity_statistics_per_channel = {}
            for i in range(self.num_channels):
                intensity_statistics_per_channel[i] = {
                    "mean": float(np.mean(foreground_intensities_per_channel[i])),
                    "median": float(np.median(foreground_intensities_per_channel[i])),
                    "std": float(np.std(foreground_intensities_per_channel[i])),
                    "min": float(np.min(foreground_intensities_per_channel[i])),
                    "max": float(np.max(foreground_intensities_per_channel[i])),
                    "percentile_99_5": float(
                        np.percentile(foreground_intensities_per_channel[i], 99.5)
                    ),
                    "percentile_00_5": float(
                        np.percentile(foreground_intensities_per_channel[i], 0.5)
                    ),
                }

            fingerprint = {
                "spacings": spacings,
                "shapes_after_crop": shapes_after_crop,
                "foreground_intensity_properties_per_channel": intensity_statistics_per_channel,
                "median_relative_size_after_cropping": median_relative_size_after_cropping,
            }

            try:
                save_json(fingerprint, properties_file)
            except Exception as e:
                if isfile(properties_file):
                    os.remove(properties_file)
                raise e
        else:
            fingerprint = load_json(properties_file)
        return fingerprint
