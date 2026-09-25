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
# Fingerprint extraction and experiment planning adapted
# from https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/ (dataset_fingerprint.py as of
# 9c4be96, "fix bug in dataset fingerprint calculation"). The two classes are adapted from upstream.
# Both are themselves derived from nnU-Net v2 (DKFZ); its original Apache 2.0 notice is retained
# below.
#
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
#    Modifications: Adapted for MambaX-Net project (https://github.com/yoviny/MambaX-Net) with custom dataset
#    fingerprinting, experiment planning and 3D segmentation support. Original nnUNet v2 dataset fingerprint
#    extractor and experiment planner modified to work with custom data loading and a custom planning pipeline.

import argparse
import multiprocessing
import os
import warnings
from copy import deepcopy
from pathlib import Path
from time import sleep
from typing import Any

import monai
import numpy as np
import torch
from batchgenerators.utilities.file_and_folder_operations import (
    isfile,
    join,
    load_json,
    save_json,
)
from dynamic_network_architectures.architectures.unet import (
    PlainConvUNet,
    ResidualEncoderUNet,
)
from dynamic_network_architectures.building_blocks.helper import (
    convert_dim_to_conv_op,
    get_matching_instancenorm,
)
from einops import rearrange
from nnunetv2.configuration import ANISO_THRESHOLD
from nnunetv2.experiment_planning.experiment_planners.network_topology import (
    get_pool_and_conv_props,
)
from nnunetv2.preprocessing.cropping.cropping import crop_to_nonzero
from nnunetv2.preprocessing.normalization.map_channel_name_to_normalization import (
    get_normalization_scheme,
)
from nnunetv2.preprocessing.resampling.default_resampling import (
    compute_new_shape,
    resample_data_or_seg_to_shape,
)
from nnunetv2.utilities.default_n_proc_DA import get_allowed_n_proc_DA
from nnunetv2.utilities.get_network_from_plans import get_network_from_plans
from nnunetv2.utilities.json_export import recursive_fix_for_json_export
from torch.utils.data import ConcatDataset
from tqdm import tqdm

from dataset import PicaiDataset

warnings.filterwarnings("ignore")


class DatasetFingerprintExtractor:
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
    ) -> tuple[list[np.ndarray], list[dict[str, float]]]:
        """
        images=image with multiple channels = shape (c, x, y(, z))
        """
        assert images.ndim == 4
        assert segmentation.ndim == 4

        assert not np.any(
            np.isnan(segmentation)
        ), "Segmentation contains NaN values. grrrr.... :-("
        assert not np.any(np.isnan(images)), "Images contains NaN values. grrrr.... :-("

        rs = np.random.default_rng(seed)

        intensities_per_channel = []
        # we don't use the intensity_statistics_per_channel at all, it's just something that might be nice to have
        intensity_statistics_per_channel = []

        # segmentation is 4d: 1,x,y,z. We need to remove the empty dimension for the following code to work
        foreground_mask = segmentation[0] > 0

        for _, image_channel in enumerate(images):
            foreground_pixels = image_channel[foreground_mask]
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
    ) -> tuple[
        tuple[int, ...], list[float], list[np.ndarray], list[dict[str, float]], float
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
        data_cropped, seg_cropped, _ = crop_to_nonzero(
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

    def run(self, overwrite_existing: bool = False) -> dict[str, Any]:
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
                workers = list(p._pool)
                with tqdm(
                    desc=None, total=len(self.dataloader), disable=self.verbose
                ) as pbar:
                    while len(remaining) > 0:
                        all_alive = all(j.is_alive() for j in workers)
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


class ExperimentPlanner:
    """Derives training configurations (2D, 3D fullres, 3D lowres) from a dataset fingerprint.

    Uses the precomputed dataset fingerprint (spacings, shapes, intensity statistics) together
    with a GPU memory budget to pick target spacing, patch size, batch size, network topology
    and normalization scheme for each configuration, and writes the result to a plans json file.
    """

    def __init__(
        self,
        fingerprint_dir: str,
        output_folder: str,
        dataloader: Any,
        num_channels: int = 1,
        gpu_memory_target_in_gb: float = 8,
        preprocessor_name: str = "DefaultPreprocessor",
        plans_name: str = "nnUNetPlans",
        overwrite_target_spacing: list[float] | tuple[float, ...] | None = None,
        suppress_transpose: bool = False,
        resnet: bool = False,
    ) -> None:
        """
        overwrite_target_spacing only affects 3d_fullres! (but by extension 3d_lowres which starts with fullres may
        also be affected
        """
        self.fingerprint_dir = fingerprint_dir
        self.output_folder = output_folder
        self.dataloader = dataloader
        self.num_channels = num_channels
        self.suppress_transpose = suppress_transpose
        self.resnet = resnet

        # load dataset fingerprint
        if not isfile(join(self.fingerprint_dir, "dataset_fingerprint.json")):
            raise RuntimeError(
                "Fingerprint missing for this dataset. Please run data fingerprint extraction first"
            )

        self.dataset_fingerprint = load_json(
            join(self.fingerprint_dir, "dataset_fingerprint.json")
        )

        self.anisotropy_threshold = ANISO_THRESHOLD

        if self.resnet:
            self.unet_class = ResidualEncoderUNet
            # the following two numbers are really arbitrary and were set to reproduce default
            # nnU-Net's configurations as much as possible
            self.unet_reference_val_3d = 680000000
            self.unet_reference_val_2d = 135000000
            self.unet_blocks_per_stage_encoder = (1, 3, 4, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6)
            self.unet_blocks_per_stage_decoder = (1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)
        else:
            self.unet_class = PlainConvUNet
            # the following two numbers are really arbitrary and were set to reproduce nnU-Net v1's configurations as
            # much as possible
            self.unet_reference_val_3d = 560000000  # 455600128  550000000
            self.unet_reference_val_2d = 85000000  # 83252480
            self.unet_blocks_per_stage_encoder = (
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
                2,
            )
            self.unet_blocks_per_stage_decoder = (2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2)

        self.unet_base_num_features = 32
        self.unet_reference_com_nfeatures = 32
        self.unet_reference_val_corresp_gb = 8
        self.unet_reference_val_corresp_bs_2d = 12
        self.unet_reference_val_corresp_bs_3d = 2
        self.unet_featuremap_min_edge_length = 4

        self.unet_min_batch_size = 2
        self.unet_max_features_2d = 512
        self.unet_max_features_3d = 320
        self.max_dataset_covered = 0.05  # we limit the batch size so that no more than 5% of the dataset can be seen
        # in a single forward/backward pass

        self.unet_vram_target_gb = gpu_memory_target_in_gb

        self.lowres_creation_threshold = (
            0.25  # if the patch size of fullres is less than 25% of the voxels in the
        )
        # median shape then we need a lowres config as well

        self.preprocessor_name = preprocessor_name
        self.plans_identifier = plans_name
        self.overwrite_target_spacing = overwrite_target_spacing
        assert overwrite_target_spacing is None or len(
            overwrite_target_spacing
        ), "if overwrite_target_spacing is used then three floats must be given (as list or tuple)"
        assert overwrite_target_spacing is None or all(
            isinstance(i, float) for i in overwrite_target_spacing
        ), "if overwrite_target_spacing is used then three floats must be given (as list or tuple)"

        self.plans = None

        # if isfile(join(self.raw_dataset_folder, 'splits_final.json')):
        #     _maybe_copy_splits_file(join(self.raw_dataset_folder, 'splits_final.json'),
        #                             join(preprocessed_folder, 'splits_final.json'))

    # def determine_reader_writer(self):
    #     example_image = self.dataset[self.dataset.keys().__iter__().__next__()]['images'][0]
    #     return determine_reader_writer_from_dataset_json(self.dataset_json, example_image)

    @staticmethod
    def static_estimate_vram_usage(
        patch_size: tuple[int, ...],
        input_channels: int,
        output_channels: int,
        arch_class_name: str,
        arch_kwargs: dict[str, Any],
        arch_kwargs_req_import: tuple[str, ...],
    ) -> float:
        """
        Works for PlainConvUNet, ResidualEncoderUNet
        """
        a = torch.get_num_threads()
        torch.set_num_threads(get_allowed_n_proc_DA())
        # print(f'instantiating network, patch size {patch_size}, pool op: {arch_kwargs["strides"]}')
        net = get_network_from_plans(
            arch_class_name,
            arch_kwargs,
            arch_kwargs_req_import,
            input_channels,
            output_channels,
            allow_init=False,
        )
        ret = net.compute_conv_feature_map_size(patch_size)
        torch.set_num_threads(a)
        return ret

    def determine_resampling(
        self, *args, **kwargs
    ) -> tuple[Any, dict[str, Any], Any, dict[str, Any]]:
        """
        returns what functions to use for resampling data and seg, respectively. Also returns kwargs
        resampling function must be callable(data, current_spacing, new_spacing, **kwargs)

        determine_resampling is called within get_plans_for_configuration to allow for different functions for each
        configuration
        """
        resampling_data = resample_data_or_seg_to_shape
        resampling_data_kwargs = {
            "is_seg": False,
            "order": 3,
            "order_z": 0,
            "force_separate_z": None,
        }
        resampling_seg = resample_data_or_seg_to_shape
        resampling_seg_kwargs = {
            "is_seg": True,
            "order": 1,
            "order_z": 0,
            "force_separate_z": None,
        }
        return (
            resampling_data,
            resampling_data_kwargs,
            resampling_seg,
            resampling_seg_kwargs,
        )

    def determine_segmentation_softmax_export_fn(
        self, *args, **kwargs
    ) -> tuple[Any, dict[str, Any]]:
        """
        function must be callable(data, new_shape, current_spacing, new_spacing, **kwargs). The new_shape should be
        used as target. current_spacing and new_spacing are merely there in case we want to use it somehow

        determine_segmentation_softmax_export_fn is called within get_plans_for_configuration to allow for different
        functions for each configuration

        """
        resampling_fn = resample_data_or_seg_to_shape
        resampling_fn_kwargs = {
            "is_seg": False,
            "order": 1,
            "order_z": 0,
            "force_separate_z": None,
        }
        return resampling_fn, resampling_fn_kwargs

    def determine_fullres_target_spacing(self) -> np.ndarray:
        """
        per default we use the 50th percentile=median for the target spacing. Higher spacing results in smaller data
        and thus faster and easier training. Smaller spacing results in larger data and thus longer and harder training

        For some datasets the median is not a good choice. Those are the datasets where the spacing is very anisotropic
        (for example ACDC with (10, 1.5, 1.5)). These datasets still have examples with a spacing of 5 or 6 mm in
        the low resolution axis. Choosing the median here will result in bad interpolation artifacts that can
        substantially impact performance (due to the low number of slices).
        """
        if self.overwrite_target_spacing is not None:
            return np.array(self.overwrite_target_spacing)

        spacings = self.dataset_fingerprint["spacings"]
        sizes = self.dataset_fingerprint["shapes_after_crop"]

        target = np.percentile(np.vstack(spacings), 50, 0)

        # todo sizes_after_resampling = [compute_new_shape(j, i, target) for i, j in zip(spacings, sizes)]

        target_size = np.percentile(np.vstack(sizes), 50, 0)

        # we need to identify datasets for which a different target spacing could be beneficial. These datasets have
        # the following properties:
        # - one axis which much lower resolution than the others
        # - the lowres axis has much less voxels than the others
        # - (the size in mm of the lowres axis is also reduced)
        worst_spacing_axis = np.argmax(target)
        other_axes = [i for i in range(len(target)) if i != worst_spacing_axis]
        other_spacings = [target[i] for i in other_axes]
        other_sizes = [target_size[i] for i in other_axes]

        has_aniso_spacing = target[worst_spacing_axis] > (
            self.anisotropy_threshold * max(other_spacings)
        )
        has_aniso_voxels = target_size[
            worst_spacing_axis
        ] * self.anisotropy_threshold < min(other_sizes)

        if has_aniso_spacing and has_aniso_voxels:
            spacings_of_that_axis = np.vstack(spacings)[:, worst_spacing_axis]
            target_spacing_of_that_axis = np.percentile(spacings_of_that_axis, 10)
            # don't let the spacing of that axis get higher than the other axes
            if target_spacing_of_that_axis < max(other_spacings):
                target_spacing_of_that_axis = (
                    max(max(other_spacings), target_spacing_of_that_axis) + 1e-5
                )
            target[worst_spacing_axis] = target_spacing_of_that_axis
        return target

    def determine_normalization_scheme_and_whether_mask_is_used_for_norm(
        self,
    ) -> tuple[list[str], list[bool]]:
        """Determine the per-channel normalization scheme and mask usage.

        Looks up the normalization class for each modality/channel and decides whether the
        nonzero mask should be used during normalization. Mask usage is only enabled for
        schemes that support it, and only when the median relative size after cropping is
        below 75% (i.e. cropping removed a substantial amount of background).

        Returns:
            Tuple[List[str], List[bool]]: The name of the normalization scheme for each channel,
                and whether the nonzero mask should be used for normalization for each channel.
        """
        # if 'channel_names' not in self.dataset_json.keys():
        #     print('WARNING: "modalities" should be renamed to "channel_names" in dataset.json. This will be '
        #           'enforced soon!')
        # modalities = self.dataset_json['channel_names'] if 'channel_names' in self.dataset_json.keys() else \
        #     self.dataset_json['modality']
        modalities = {0: "T2"}
        normalization_schemes = [
            get_normalization_scheme(m) for m in modalities.values()
        ]
        if self.dataset_fingerprint["median_relative_size_after_cropping"] < (3 / 4.0):
            use_nonzero_mask_for_norm = [
                i.leaves_pixels_outside_mask_at_zero_if_use_mask_for_norm_is_true
                for i in normalization_schemes
            ]
        else:
            use_nonzero_mask_for_norm = [False] * len(normalization_schemes)
            assert all(
                i in (True, False) for i in use_nonzero_mask_for_norm
            ), "use_nonzero_mask_for_norm must be True or False and cannot be None"
        normalization_schemes = [i.__name__ for i in normalization_schemes]
        return normalization_schemes, use_nonzero_mask_for_norm

    def determine_transpose(self) -> tuple[list[int], list[int]]:
        """Determine the axis order to transpose images to before further processing.

        Puts the axis with the largest (worst) spacing first, which is the axis nnU-Net-style
        planning treats specially (e.g. as the "z" axis for 2D configurations). If
        suppress_transpose is set, the identity ordering is returned instead.

        Returns:
            Tuple[List[int], List[int]]: The forward transpose (original axis order to
                planning order) and the backward transpose (planning order back to original
                axis order).
        """
        if self.suppress_transpose:
            return [0, 1, 2], [0, 1, 2]

        # todo we should use shapes for that as well. Not quite sure how yet
        target_spacing = self.determine_fullres_target_spacing()

        max_spacing_axis = np.argmax(target_spacing)
        remaining_axes = [i for i in list(range(3)) if i != max_spacing_axis]
        transpose_forward = [max_spacing_axis] + remaining_axes
        transpose_backward = [
            np.argwhere(np.array(transpose_forward) == i)[0][0] for i in range(3)
        ]
        return transpose_forward, transpose_backward

    def get_plans_for_configuration(
        self,
        spacing: np.ndarray | tuple[float, ...] | list[float],
        median_shape: np.ndarray | tuple[int, ...],
        data_identifier: str,
        approximate_n_voxels_dataset: float,
        _cache: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a full plan (patch size, batch size, architecture, resampling) for one configuration.

        Starting from an initial patch size derived from the given spacing, computes the network
        topology (pooling/conv kernel sizes), then shrinks the patch size in a loop until the
        estimated VRAM usage fits within the target budget. From the resulting patch size and
        remaining VRAM headroom, derives a batch size (capped so a batch covers no more than
        max_dataset_covered of the dataset), and assembles the resampling functions and
        normalization scheme into a single plan dict.

        Args:
            spacing (Union[np.ndarray, Tuple[float, ...], List[float]]): Target voxel spacing for
                this configuration.
            median_shape (Union[np.ndarray, Tuple[int, ...]]): Median image shape (in voxels) at
                this spacing.
            data_identifier (str): Identifier used to name the preprocessed data for this
                configuration.
            approximate_n_voxels_dataset (float): Approximate total number of voxels in the
                dataset at this spacing, used to cap the batch size.
            _cache (Dict[str, Any]): Cache mapping (patch_size, strides) keys to previously
                computed VRAM estimates, to avoid re-instantiating the same network twice.

        Returns:
            Dict[str, Any]: The plan for this configuration, including batch size, patch size,
                normalization scheme, resampling functions and architecture kwargs.
        """

        def _features_per_stage(num_stages, max_num_features) -> tuple[int, ...]:
            """Compute the number of feature maps for each encoder stage.

            Doubles the base number of features at every stage, capped at max_num_features.

            Args:
                num_stages: Number of stages to generate feature counts for.
                max_num_features: Upper bound on the number of features per stage.

            Returns:
                Tuple[int, ...]: Number of feature maps for each stage.
            """
            return tuple(
                min(max_num_features, self.unet_base_num_features * 2**i)
                for i in range(num_stages)
            )

        def _keygen(
            patch_size: tuple[int, ...], strides: tuple[tuple[int, ...], ...]
        ) -> str:
            """Build a cache key from a patch size and stride configuration.

            Args:
                patch_size (Tuple[int, ...]): Patch size to encode in the key.
                strides (Tuple[Tuple[int, ...], ...]): Per-stage pooling strides to encode in the
                    key.

            Returns:
                str: String key uniquely identifying this (patch_size, strides) combination.
            """
            return str(patch_size) + "_" + str(strides)

        assert all(i > 0 for i in spacing), f"Spacing must be > 0! Spacing: {spacing}"
        # num_input_channels = len(self.dataset_json['channel_names'].keys()
        #                          if 'channel_names' in self.dataset_json.keys()
        #                          else self.dataset_json['modality'].keys())
        num_input_channels = self.num_channels
        max_num_features = (
            self.unet_max_features_2d
            if len(spacing) == 2
            else self.unet_max_features_3d
        )
        unet_conv_op = convert_dim_to_conv_op(len(spacing))

        # print(spacing, median_shape, approximate_n_voxels_dataset)
        # find an initial patch size
        # we first use the spacing to get an aspect ratio
        tmp = 1 / np.array(spacing)

        # we then upscale it so that it initially is certainly larger than what we need (rescale to have the same
        # volume as a patch of size 256 ** 3)
        # this may need to be adapted when using absurdly large GPU memory targets. Increasing this now would not be
        # ideal because large initial patch sizes increase computation time because more iterations in the while loop
        # further down may be required.
        if len(spacing) == 3:
            initial_patch_size = [
                round(i) for i in tmp * (256**3 / np.prod(tmp)) ** (1 / 3)
            ]
        elif len(spacing) == 2:
            initial_patch_size = [
                round(i) for i in tmp * (2048**2 / np.prod(tmp)) ** (1 / 2)
            ]
        else:
            raise RuntimeError()

        # clip initial patch size to median_shape. It makes little sense to have it be larger than that. Note that
        # this is different from how nnU-Net v1 does it!
        # todo patch size can still get too large because we pad the patch size to a multiple of 2**n
        if self.resnet:
            initial_patch_size = np.minimum(
                initial_patch_size, median_shape[: len(spacing)]
            )
        else:
            initial_patch_size = np.array(
                [
                    min(i, j)
                    for i, j in zip(initial_patch_size, median_shape[: len(spacing)])
                ]
            )

        # use that to get the network topology. Note that this changes the patch_size depending on the number of
        # pooling operations (must be divisible by 2**num_pool in each axis)
        (
            _,
            pool_op_kernel_sizes,
            conv_kernel_sizes,
            patch_size,
            shape_must_be_divisible_by,
        ) = get_pool_and_conv_props(
            spacing, initial_patch_size, self.unet_featuremap_min_edge_length, 999999
        )
        num_stages = len(pool_op_kernel_sizes)

        norm = get_matching_instancenorm(unet_conv_op)

        if self.resnet:
            architecture_kwargs = {
                "network_class_name": self.unet_class.__module__
                + "."
                + self.unet_class.__name__,
                "arch_kwargs": {
                    "n_stages": num_stages,
                    "features_per_stage": _features_per_stage(
                        num_stages, max_num_features
                    ),
                    "conv_op": unet_conv_op.__module__ + "." + unet_conv_op.__name__,
                    "kernel_sizes": conv_kernel_sizes,
                    "strides": pool_op_kernel_sizes,
                    "n_blocks_per_stage": self.unet_blocks_per_stage_encoder[
                        :num_stages
                    ],
                    "n_conv_per_stage_decoder": self.unet_blocks_per_stage_decoder[
                        : num_stages - 1
                    ],
                    "conv_bias": True,
                    "norm_op": norm.__module__ + "." + norm.__name__,
                    "norm_op_kwargs": {"eps": 1e-5, "affine": True},
                    "dropout_op": None,
                    "dropout_op_kwargs": None,
                    "nonlin": "torch.nn.LeakyReLU",
                    "nonlin_kwargs": {"inplace": True},
                },
                "_kw_requires_import": ("conv_op", "norm_op", "dropout_op", "nonlin"),
            }
        else:
            architecture_kwargs = {
                "network_class_name": self.unet_class.__module__
                + "."
                + self.unet_class.__name__,
                "arch_kwargs": {
                    "n_stages": num_stages,
                    "features_per_stage": _features_per_stage(
                        num_stages, max_num_features
                    ),
                    "conv_op": unet_conv_op.__module__ + "." + unet_conv_op.__name__,
                    "kernel_sizes": conv_kernel_sizes,
                    "strides": pool_op_kernel_sizes,
                    "n_conv_per_stage": self.unet_blocks_per_stage_encoder[:num_stages],
                    "n_conv_per_stage_decoder": self.unet_blocks_per_stage_decoder[
                        : num_stages - 1
                    ],
                    "conv_bias": True,
                    "norm_op": norm.__module__ + "." + norm.__name__,
                    "norm_op_kwargs": {"eps": 1e-5, "affine": True},
                    "dropout_op": None,
                    "dropout_op_kwargs": None,
                    "nonlin": "torch.nn.LeakyReLU",
                    "nonlin_kwargs": {"inplace": True},
                },
                "_kw_requires_import": ("conv_op", "norm_op", "dropout_op", "nonlin"),
            }

        # now estimate vram consumption
        if _keygen(patch_size, pool_op_kernel_sizes) in _cache.keys():
            estimate = _cache[_keygen(patch_size, pool_op_kernel_sizes)]
        else:
            estimate = self.static_estimate_vram_usage(
                patch_size,
                num_input_channels,
                len(self.dataloader),
                architecture_kwargs["network_class_name"],
                architecture_kwargs["arch_kwargs"],
                architecture_kwargs["_kw_requires_import"],
            )
            _cache[_keygen(patch_size, pool_op_kernel_sizes)] = estimate

        # how large is the reference for us here (batch size etc)?
        # adapt for our vram target
        reference = (
            self.unet_reference_val_2d
            if len(spacing) == 2
            else self.unet_reference_val_3d
        ) * (self.unet_vram_target_gb / self.unet_reference_val_corresp_gb)

        # we enforce a batch size of at least two, reference values may have been computed for different batch sizes.
        # Correct for that in the while loop if statement
        while estimate > reference:
            # patch size seems to be too large, so we need to reduce it. Reduce the axis that currently violates the
            # aspect ratio the most (that is the largest relative to median shape)
            axis_to_be_reduced = np.argsort(
                [i / j for i, j in zip(patch_size, median_shape[: len(spacing)])]
            )[-1]

            # we cannot simply reduce that axis by shape_must_be_divisible_by[axis_to_be_reduced] because this
            # may cause us to skip some valid sizes, for example shape_must_be_divisible_by is 64 for a shape of 256.
            # If we subtracted that we would end up with 192, skipping 224 which is also a valid patch size
            # (224 / 2**5 = 7; 7 < 2 * self.unet_featuremap_min_edge_length(4) so it's valid). So we need to first
            # subtract shape_must_be_divisible_by, then recompute it and then subtract the
            # recomputed shape_must_be_divisible_by. Annoying.
            patch_size = list(patch_size)
            tmp = deepcopy(patch_size)
            tmp[axis_to_be_reduced] -= shape_must_be_divisible_by[axis_to_be_reduced]
            _, _, _, _, shape_must_be_divisible_by = get_pool_and_conv_props(
                spacing, tmp, self.unet_featuremap_min_edge_length, 999999
            )
            patch_size[axis_to_be_reduced] -= shape_must_be_divisible_by[
                axis_to_be_reduced
            ]

            # now recompute topology
            (
                _,
                pool_op_kernel_sizes,
                conv_kernel_sizes,
                patch_size,
                shape_must_be_divisible_by,
            ) = get_pool_and_conv_props(
                spacing, patch_size, self.unet_featuremap_min_edge_length, 999999
            )

            num_stages = len(pool_op_kernel_sizes)
            if self.resnet:
                architecture_kwargs["arch_kwargs"].update(
                    {
                        "n_stages": num_stages,
                        "kernel_sizes": conv_kernel_sizes,
                        "strides": pool_op_kernel_sizes,
                        "features_per_stage": _features_per_stage(
                            num_stages, max_num_features
                        ),
                        "n_blocks_per_stage": self.unet_blocks_per_stage_encoder[
                            :num_stages
                        ],
                        "n_conv_per_stage_decoder": self.unet_blocks_per_stage_decoder[
                            : num_stages - 1
                        ],
                    }
                )
            else:
                architecture_kwargs["arch_kwargs"].update(
                    {
                        "n_stages": num_stages,
                        "kernel_sizes": conv_kernel_sizes,
                        "strides": pool_op_kernel_sizes,
                        "features_per_stage": _features_per_stage(
                            num_stages, max_num_features
                        ),
                        "n_conv_per_stage": self.unet_blocks_per_stage_encoder[
                            :num_stages
                        ],
                        "n_conv_per_stage_decoder": self.unet_blocks_per_stage_decoder[
                            : num_stages - 1
                        ],
                    }
                )
            if _keygen(patch_size, pool_op_kernel_sizes) in _cache.keys():
                estimate = _cache[_keygen(patch_size, pool_op_kernel_sizes)]
            else:
                estimate = self.static_estimate_vram_usage(
                    patch_size,
                    num_input_channels,
                    len(self.dataloader),
                    architecture_kwargs["network_class_name"],
                    architecture_kwargs["arch_kwargs"],
                    architecture_kwargs["_kw_requires_import"],
                )
                _cache[_keygen(patch_size, pool_op_kernel_sizes)] = estimate

        # alright now let's determine the batch size. This will give self.unet_min_batch_size if the while loop was
        # executed. If not, additional vram headroom is used to increase batch size
        ref_bs = (
            self.unet_reference_val_corresp_bs_2d
            if len(spacing) == 2
            else self.unet_reference_val_corresp_bs_3d
        )
        batch_size = round((reference / estimate) * ref_bs)

        # we need to cap the batch size to cover at most 5% of the entire dataset. Overfitting precaution. We cannot
        # go smaller than self.unet_min_batch_size though
        bs_corresponding_to_5_percent = round(
            approximate_n_voxels_dataset
            * self.max_dataset_covered
            / np.prod(patch_size, dtype=np.float64)
        )
        batch_size = max(
            min(batch_size, bs_corresponding_to_5_percent), self.unet_min_batch_size
        )

        (
            resampling_data,
            resampling_data_kwargs,
            resampling_seg,
            resampling_seg_kwargs,
        ) = self.determine_resampling()
        resampling_softmax, resampling_softmax_kwargs = (
            self.determine_segmentation_softmax_export_fn()
        )

        normalization_schemes, mask_is_used_for_norm = (
            self.determine_normalization_scheme_and_whether_mask_is_used_for_norm()
        )

        plan = {
            "data_identifier": data_identifier,
            "preprocessor_name": self.preprocessor_name,
            "batch_size": batch_size,
            "patch_size": patch_size,
            "median_image_size_in_voxels": median_shape,
            "spacing": spacing,
            "normalization_schemes": normalization_schemes,
            "use_mask_for_norm": mask_is_used_for_norm,
            "resampling_fn_data": resampling_data.__name__,
            "resampling_fn_seg": resampling_seg.__name__,
            "resampling_fn_data_kwargs": resampling_data_kwargs,
            "resampling_fn_seg_kwargs": resampling_seg_kwargs,
            "resampling_fn_probabilities": resampling_softmax.__name__,
            "resampling_fn_probabilities_kwargs": resampling_softmax_kwargs,
            "architecture": architecture_kwargs,
        }
        return plan

    def plan_experiment(self) -> None:
        """
        MOVE EVERYTHING INTO THE PLANS. MAXIMUM FLEXIBILITY

        Ideally I would like to move transpose_forward/backward into the configurations so that this can also be done
        differently for each configuration but this would cause problems with identifying the correct axes for 2d. There
        surely is a way around that but eh. I'm feeling lazy and featuritis must also not be pushed to the extremes.

        So for now if you want a different transpose_forward/backward you need to create a new planner. Also not too
        hard.
        """
        # we use this as a cache to prevent having to instantiate the architecture too often. Saves computation time
        _tmp = {}

        # first get transpose
        transpose_forward, transpose_backward = self.determine_transpose()

        # get fullres spacing and transpose it
        fullres_spacing = self.determine_fullres_target_spacing()
        fullres_spacing_transposed = fullres_spacing[transpose_forward]

        # get transposed new median shape (what we would have after resampling)
        new_shapes = [
            compute_new_shape(j, i, fullres_spacing)
            for i, j in zip(
                self.dataset_fingerprint["spacings"],
                self.dataset_fingerprint["shapes_after_crop"],
            )
        ]
        new_median_shape = np.median(new_shapes, 0)
        new_median_shape_transposed = new_median_shape[transpose_forward]

        # approximate_n_voxels_dataset = float(np.prod(new_median_shape_transposed, dtype=np.float64) *
        #                                      self.dataset_json['numTraining'])

        approximate_n_voxels_dataset = float(
            np.prod(new_median_shape_transposed, dtype=np.float64)
            * len(self.dataloader)
        )
        # only run 3d if this is a 3d dataset
        if new_median_shape_transposed[0] != 1:
            plan_3d_fullres = self.get_plans_for_configuration(
                fullres_spacing_transposed,
                new_median_shape_transposed,
                self.generate_data_identifier("3d_fullres"),
                approximate_n_voxels_dataset,
                _tmp,
            )
            # maybe add 3d_lowres as well
            patch_size_fullres = plan_3d_fullres["patch_size"]
            median_num_voxels = np.prod(new_median_shape_transposed, dtype=np.float64)
            num_voxels_in_patch = np.prod(patch_size_fullres, dtype=np.float64)

            plan_3d_lowres = None
            lowres_spacing = deepcopy(plan_3d_fullres["spacing"])

            spacing_increase_factor = (
                1.03  # used to be 1.01 but that is slow with new GPU memory estimation!
            )
            while (
                num_voxels_in_patch / median_num_voxels < self.lowres_creation_threshold
            ):
                # we incrementally increase the target spacing. We start with the anisotropic axis/axes until it/they
                # is/are similar (factor 2) to the other ax(i/e)s.
                max_spacing = max(lowres_spacing)
                if np.any((max_spacing / lowres_spacing) > 2):
                    lowres_spacing[
                        (max_spacing / lowres_spacing) > 2
                    ] *= spacing_increase_factor
                else:
                    lowres_spacing *= spacing_increase_factor
                median_num_voxels = np.prod(
                    plan_3d_fullres["spacing"]
                    / lowres_spacing
                    * new_median_shape_transposed,
                    dtype=np.float64,
                )

                plan_3d_lowres = self.get_plans_for_configuration(
                    lowres_spacing,
                    tuple(
                        [
                            round(i)
                            for i in plan_3d_fullres["spacing"]
                            / lowres_spacing
                            * new_median_shape_transposed
                        ]
                    ),
                    self.generate_data_identifier("3d_lowres"),
                    float(np.prod(median_num_voxels) * len(self.dataloader)),
                    _tmp,
                )
                num_voxels_in_patch = np.prod(
                    plan_3d_lowres["patch_size"], dtype=np.int64
                )
                current_median_shape = (
                    plan_3d_fullres["spacing"] / lowres_spacing * new_median_shape_transposed
                )
                print(
                    f"Attempting to find 3d_lowres config. "
                    f"\nCurrent spacing: {lowres_spacing}. "
                    f"\nCurrent patch size: {plan_3d_lowres['patch_size']}. "
                    f"\nCurrent median shape: {current_median_shape}"
                )
            if (
                np.prod(new_median_shape_transposed, dtype=np.float64)
                / median_num_voxels
                < 2
            ):
                rounded_lowres_shape = [
                    round(i)
                    for i in plan_3d_fullres["spacing"] / lowres_spacing * new_median_shape_transposed
                ]
                print(
                    f"Dropping 3d_lowres config because the image size difference to 3d_fullres is too small. "
                    f"3d_fullres: {new_median_shape_transposed}, "
                    f"3d_lowres: {rounded_lowres_shape}"
                )
                plan_3d_lowres = None
            if plan_3d_lowres is not None:
                plan_3d_lowres["batch_dice"] = False
                plan_3d_fullres["batch_dice"] = True
            else:
                plan_3d_fullres["batch_dice"] = False
        else:
            plan_3d_fullres = None
            plan_3d_lowres = None

        # 2D configuration
        plan_2d = self.get_plans_for_configuration(
            fullres_spacing_transposed[1:],
            new_median_shape_transposed[1:],
            self.generate_data_identifier("2d"),
            approximate_n_voxels_dataset,
            _tmp,
        )
        plan_2d["batch_dice"] = True

        print("2D U-Net configuration:")
        print(plan_2d)
        print()

        # median spacing and shape, just for reference when printing the plans
        median_spacing = np.median(self.dataset_fingerprint["spacings"], 0)[
            transpose_forward
        ]
        median_shape = np.median(self.dataset_fingerprint["shapes_after_crop"], 0)[
            transpose_forward
        ]

        plans = {
            "plans_name": self.plans_identifier,
            "original_median_spacing_after_transp": [float(i) for i in median_spacing],
            "original_median_shape_after_transp": [int(round(i)) for i in median_shape],
            "transpose_forward": [int(i) for i in transpose_forward],
            "transpose_backward": [int(i) for i in transpose_backward],
            "configurations": {"2d": plan_2d},
            "experiment_planner_used": self.__class__.__name__,
            "label_manager": "LabelManager",
            "foreground_intensity_properties_per_channel": self.dataset_fingerprint[
                "foreground_intensity_properties_per_channel"
            ],
        }

        if plan_3d_lowres is not None:
            plans["configurations"]["3d_lowres"] = plan_3d_lowres
            if plan_3d_fullres is not None:
                plans["configurations"]["3d_lowres"][
                    "next_stage"
                ] = "3d_cascade_fullres"
            print("3D lowres U-Net configuration:")
            print(plan_3d_lowres)
            print()
        if plan_3d_fullres is not None:
            plans["configurations"]["3d_fullres"] = plan_3d_fullres
            print("3D fullres U-Net configuration:")
            print(plan_3d_fullres)
            print()
            if plan_3d_lowres is not None:
                plans["configurations"]["3d_cascade_fullres"] = {
                    "inherits_from": "3d_fullres",
                    "previous_stage": "3d_lowres",
                }

        self.plans = plans
        self.save_plans(plans)
        return plans

    def save_plans(self, plans: dict[str, Any]) -> None:
        """Write a plans dict to the plans json file, preserving existing custom configurations.

        If a plans file already exists at the target path, any configurations in it that are not
        being overwritten by `plans` are kept and merged in, so manually added/customized
        configurations are not lost on re-planning.

        Args:
            plans (Dict[str, Any]): Plans dict to save.
        """
        recursive_fix_for_json_export(plans)

        plans_file = join(self.output_folder, self.plans_identifier + ".json")

        # we don't want to overwrite potentially existing custom configurations every time this is executed. So let's
        # read the plans file if it already exists and keep any non-default configurations
        if isfile(plans_file):
            old_plans = load_json(plans_file)
            old_configurations = old_plans["configurations"]
            for c in plans["configurations"].keys():
                if c in old_configurations.keys():
                    del old_configurations[c]
            plans["configurations"].update(old_configurations)

        save_json(plans, plans_file, sort_keys=False)
        print(
            f"Plans were saved to {join(self.output_folder, self.plans_identifier + '.json')}"
        )

    def generate_data_identifier(self, configuration_name: str) -> str:
        """
        configurations are unique within each plans file but different plans file can have configurations with the
        same name. In order to distinguish the associated data we need a data identifier that reflects not just the
        config but also the plans it originates from
        """
        return self.plans_identifier + "_" + configuration_name

    def load_plans(self, fname: str) -> None:
        """Load a plans json file into self.plans.

        Args:
            fname (str): Path to the plans json file to load.
        """
        self.plans = load_json(fname)


def patch_collate_fingerprint(
    batch: list[dict],
) -> tuple[list[torch.Tensor], list[torch.Tensor]]:
    """Collate function for fingerprinting patches.

    Moved here from MambaX-Net's custom_collate.py, this script being its only caller. No stock
    collate can stand in: fingerprint mode yields `{"image": Nifti1Image, "mask": Nifti1Image}`
    (dataset.py's as_fingerprint_pair), and both torch's default_collate and monai's
    list_data_collate raise TypeError on a Nifti1Image rather than passing it through. Kept a
    module-level def, not a lambda, so it stays picklable for the DataLoader's workers
    (num_workers defaults to 8).

    Args:
        batch: List of dictionaries containing image and mask tensors.

    Returns:
        A tuple containing two lists - images and masks.
    """
    imgs = [item["image"] for item in batch]
    masks = [item["mask"] for item in batch]
    return imgs, masks


def run_fingerprint_extractor() -> None:
    """Run the fingerprint extractor and the experiment planner over one or more sites."""

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--site-dir",
        type=Path,
        nargs="+",
        required=True,
        help="One or more data/prostate/sites/<CENTER> folders (each holding manifest.csv, nifti/, "
        "labels/, zonal_labels/). Passing several pools their studies into a single fingerprint, "
        "so every client can be given the same plan.",
    )
    parser.add_argument("-o", "--output-dir", type=Path, required=True)
    parser.add_argument(
        "-m",
        "--modality",
        type=str,
        default="t2w",
        help="Which PI-CAI scan modality to fingerprint (t2w, adc, or hbv).",
    )
    parser.add_argument("-np", "--num-processes", type=int, default=8)
    parser.add_argument("-mem", "--gpu-memory-GB", type=int, default=8)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of images for processing, per site (for testing)",
    )
    args = parser.parse_args()

    site_datasets = []
    for site_dir in args.site_dir:
        site_dataset = PicaiDataset(
            site_dir,
            modality=args.modality,
            fingerprint=True,
        )
        if args.limit is not None:
            site_dataset.df = site_dataset.df.iloc[: args.limit]
        print(f"{site_dir.name}: {len(site_dataset)} studies")
        site_datasets.append(site_dataset)

    picai_dataset = ConcatDataset(site_datasets)
    print(
        f"Fingerprinting {len(picai_dataset)} studies across {len(site_datasets)} site(s)"
    )

    dataloader = monai.data.DataLoader(
        picai_dataset,
        batch_size=1,
        collate_fn=patch_collate_fingerprint,
        num_workers=args.num_processes,
        pin_memory=True,
        shuffle=False,
    )

    fingerprint_extractor = DatasetFingerprintExtractor(
        output_folder=str(args.output_dir),
        dataloader=dataloader,
        channels=1,
        num_processes=args.num_processes,
        verbose=args.verbose,
    )

    fingerprint_extractor.run(overwrite_existing=True)

    print("Fingerprint extraction complete")

    print("Planning experiment")
    planner = ExperimentPlanner(
        fingerprint_dir=str(args.output_dir),
        output_folder=str(args.output_dir),
        dataloader=dataloader,
        num_channels=1,
        gpu_memory_target_in_gb=args.gpu_memory_GB,
    )
    ret = planner.plan_experiment()
    print(ret)

    # rename the dataset fingerprint file
    os.rename(
        os.path.join(args.output_dir, "dataset_fingerprint.json"),
        os.path.join(args.output_dir, "dataset_fingerprint_segmentation.json"),
    )

    # rename the experiment plan file
    os.rename(
        os.path.join(args.output_dir, "nnUNetPlans.json"),
        os.path.join(args.output_dir, "nnUNetPlans_segmentation.json"),
    )


if __name__ == "__main__":
    run_fingerprint_extractor()
