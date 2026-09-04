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
# https://github.com/yoviny/MambaX-Net/blob/main/mambax_net/dataset/csv_load.py,
# flattened into this tutorial's directory.

import argparse
import os
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml
from batchgenerators.utilities.file_and_folder_operations import load_json


def picai_data_load(
    df_path: str, prostateX_mapping: str
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Load and preprocess data from CSV and JSON files.

    Args:
        df_path (str): Path to the CSV file.
        prostateX_mapping (str): Path to the ProstateX mapping JSON file.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame]: Training and testing DataFrames.
    """
    main_df = pd.read_csv(df_path)
    main_df["patient_id"] = main_df["patient_id"].astype(int)

    data = load_json(prostateX_mapping)

    px = pd.DataFrame(list(data.items()), columns=["ProstateX_id", "PICCAI_id"])
    # remove ProstateX-0074 since T2 and mask sizes are different
    px = px[px["ProstateX_id"] != "ProstateX-0074_12-12-2011"]
    main_df = main_df[main_df["patient_id"] != 10930]

    px[["patient_id", "study_id"]] = px["PICCAI_id"].str.split("_", n=1, expand=True)
    px["prostateX_filename"] = px["ProstateX_id"].str.split("_").str[0]

    px["p_id"] = px["prostateX_filename"].str.split("-")
    px["p_id"] = px["p_id"].str[1].astype(int)
    pat_ids_to_drop = px[px["p_id"] > 203]["patient_id"].astype(int)
    px = px[px["p_id"] <= 203].drop(columns=["p_id"])
    main_df = main_df[~main_df["patient_id"].isin(pat_ids_to_drop)]
    main_df["patient_id"] = main_df["patient_id"].astype(int)
    px["patient_id"] = px["patient_id"].astype(int)

    test_df = (
        px.merge(main_df, on="patient_id", how="inner")
        .drop(columns=["study_id_x"])
        .rename(columns={"study_id_y": "study_id"})
    )
    test_df = test_df.drop_duplicates(subset=["patient_id", "study_id"]).reset_index(
        drop=True
    )

    train_df = main_df[~main_df["patient_id"].isin(px["patient_id"])].drop_duplicates()
    train_df = train_df.reset_index(drop=True)

    return train_df, test_df


def _load_nifti_study_ids(folder_path: str) -> List[str]:
    """List study IDs derived from ``.nii.gz`` files in a folder.

    Args:
        folder_path (str): Directory to scan for NIfTI files.

    Returns:
        List[str]: Filenames in ``folder_path`` ending in ``.nii.gz``, with
            that suffix stripped.
    """
    return [
        x.replace(".nii.gz", "")
        for x in os.listdir(folder_path)
        if x.endswith(".nii.gz")
    ]


def _filter_with_timepoint_fallback(
    df: pd.DataFrame, preferred_timepoint: str
) -> pd.DataFrame:
    """For each patient, use ``preferred_timepoint`` if available, otherwise
    fall back to the earliest timepoint present for that patient.

    Parameters
    ----------
    df:
        DataFrame with a ``study_id`` column (e.g. ``001_2``).
    preferred_timepoint:
        Suffix to prefer, e.g. ``"_2"``.
    """
    df = df.copy()
    df["_patient_id"] = df["study_id"].str.rsplit("_", n=1).str[0]
    df["_tp_raw"] = df["study_id"].str.rsplit("_", n=1).str[1]
    df["_tp"] = pd.to_numeric(df["_tp_raw"], errors="coerce")

    preferred = df[df["study_id"].str.endswith(preferred_timepoint)]
    preferred_patients = set(preferred["_patient_id"].unique())

    # For patients without the preferred timepoint, pick the earliest available
    # (non-numeric suffixes sort after all numeric ones via NaN)
    missing = df[~df["_patient_id"].isin(preferred_patients)]
    earliest = (
        missing.sort_values("_tp")
        .groupby("_patient_id", sort=False)
        .first()
        .reset_index(drop=True)
    )

    result = pd.concat([preferred, earliest], ignore_index=True).drop(
        columns=["_patient_id", "_tp", "_tp_raw"]
    )
    return result.reset_index(drop=True)


def active_surveillance_infer_data_load(
    t2_path: str,
    wp_label_path: Optional[str] = None,
    pz_tz_label_path: Optional[str] = None,
    test_wp_label_path: Optional[str] = None,
    test_time_point: str = "_2",
    rem_ids_config_path: Optional[str] = None,
) -> pd.DataFrame:
    """Build the AS inference set from the paths used at inference time."""

    t2_ids = set(_load_nifti_study_ids(t2_path))

    if wp_label_path is not None and pz_tz_label_path is not None:
        study_ids = (
            t2_ids
            & set(_load_nifti_study_ids(wp_label_path))
            & set(_load_nifti_study_ids(pz_tz_label_path))
        )
    elif test_wp_label_path is not None:
        study_ids = t2_ids & set(_load_nifti_study_ids(test_wp_label_path))
    else:
        study_ids = t2_ids

    if rem_ids_config_path is not None and os.path.exists(rem_ids_config_path):
        print(f"Loading ignore ids from {rem_ids_config_path}")
        with open(rem_ids_config_path, "r") as f:
            rem_ids_config = yaml.safe_load(f)
        rem_ids = set(rem_ids_config.get("ignore_ids", []))
        before = len(study_ids)
        study_ids = study_ids - rem_ids
        print(f"Removed {before - len(study_ids)} ignored ids from inference set")

    test_df = pd.DataFrame(sorted(study_ids), columns=["study_id"])

    if test_time_point != "-1":
        test_df = _filter_with_timepoint_fallback(test_df, test_time_point)

    # Dual-scan inference requires a prior timepoint scan. Single-visit
    # patients (no earlier T2 in t2_path) can't supply one - drop them
    # instead of letting DualScanDataset crash on a missing prior.
    def _has_prior_scan(study_id: str) -> bool:
        """Check whether an earlier timepoint scan exists for this study's patient.

        Args:
            study_id (str): Study ID in ``{patient_id}_{timepoint}`` form.

        Returns:
            bool: True if any timepoint from ``timepoint - 1`` down to ``0``
                is present in ``t2_ids`` for the same patient.
        """
        p_id, s_id = study_id.rsplit("_", 1)
        for k in range(int(s_id) - 1, -1, -1):
            if f"{p_id}_{k}" in t2_ids:
                return True
        return False

    before = len(test_df)
    test_df = test_df[test_df["study_id"].apply(_has_prior_scan)]
    dropped = before - len(test_df)
    if dropped:
        print(
            f"Removed {dropped} studies with no prior timepoint scan "
            "(dual-scan inference requires one)"
        )

    return test_df.reset_index(drop=True)


def active_surveillance_data_load(
    args: argparse.Namespace,
    config: dict,
    t2_path: str,
    val_wp_label_path: str,
    test_wp_label_path: str,
    test_time_point: str = "_2",
    rem_ids_config_path: str = None,
    include_timepoints: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build train/val/test study ID splits for active surveillance training.

    Val and test sets are built from the studies present in
    ``val_wp_label_path`` and ``test_wp_label_path``. The train set is built
    from all studies in ``t2_path``, with any patient appearing in val or
    test excluded to avoid leakage, and ids listed in
    ``rem_ids_config_path`` removed. If ``include_timepoints`` is given, each
    split is filtered to studies ending in one of those timepoint suffixes;
    otherwise each split falls back to ``_filter_with_timepoint_fallback``
    using ``test_time_point`` (unless it is ``"-1"``). When
    ``args.sample_sz`` is below 250, the train set is subsampled to that
    many unique patients (seeded by ``config["seed"]``); any error during
    sampling is silently ignored. Raises if any patient ends up in both
    train and val, or both train and test.

    Args:
        args (argparse.Namespace): Must provide ``sample_sz``, the max
            number of patients to keep in the train set.
        config (dict): Must provide ``seed``, used for reproducible patient
            sampling.
        t2_path (str): Directory of T2 files defining the training pool.
        val_wp_label_path (str): Directory of whole-prostate label files
            defining the validation set.
        test_wp_label_path (str): Directory of whole-prostate label files
            defining the test set.
        test_time_point (str): Preferred timepoint suffix (e.g. ``"_2"``)
            for the fallback filter; ``"-1"`` disables timepoint filtering.
        rem_ids_config_path (str): Path to a YAML file with an
            ``ignore_ids`` list of study IDs to drop from training.
        include_timepoints (Optional[List[str]]): If given, keep only
            studies whose ID ends with one of these suffixes instead of
            using the timepoint fallback filter.

    Returns:
        Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]: Train, validation,
            and test DataFrames, each with a single ``study_id`` column.
    """
    if rem_ids_config_path is not None:
        print(f"Loading ignore ids from {rem_ids_config_path}")
        with open(rem_ids_config_path, "r") as f:
            rem_ids_config = yaml.safe_load(f)
        rem_ids = rem_ids_config.get("ignore_ids", [])
        print(f"Loaded {len(rem_ids)} ids to remove from training")
    else:
        rem_ids = []

    val_df = pd.DataFrame(
        [x.split(".")[0] for x in os.listdir(val_wp_label_path)], columns=["study_id"]
    )
    val_patient_ids = val_df["study_id"].str.rsplit("_", n=1).str[0].unique()
    if include_timepoints is not None:
        val_df = val_df[
            val_df["study_id"].str.endswith(tuple(include_timepoints))
        ].reset_index(drop=True)
    elif test_time_point != "-1":
        val_df = _filter_with_timepoint_fallback(val_df, test_time_point)

    test_df = pd.DataFrame(
        [x.split(".")[0] for x in os.listdir(test_wp_label_path)], columns=["study_id"]
    )
    test_patient_ids = test_df["study_id"].str.rsplit("_", n=1).str[0].unique()
    if include_timepoints is not None:
        test_df = test_df[
            test_df["study_id"].str.endswith(tuple(include_timepoints))
        ].reset_index(drop=True)
    elif test_time_point != "-1":
        test_df = _filter_with_timepoint_fallback(test_df, test_time_point)

    # All patient base IDs that must not appear in training
    excluded_patient_ids = set(np.concatenate([val_patient_ids, test_patient_ids]))

    train_df = pd.DataFrame(
        [x.split(".")[0] for x in os.listdir(t2_path)], columns=["study_id"]
    )

    if include_timepoints is not None:
        train_df = train_df[
            train_df["study_id"].str.endswith(tuple(include_timepoints))
        ].reset_index(drop=True)
    elif test_time_point != "-1":
        train_df = _filter_with_timepoint_fallback(train_df, test_time_point)
    train_df = train_df[~train_df["study_id"].isin(rem_ids)].reset_index(drop=True)
    # Exclude any scan whose patient base ID appears in val or test
    train_df["_patient_id"] = train_df["study_id"].str.rsplit("_", n=1).str[0]
    train_df = (
        train_df[~train_df["_patient_id"].isin(excluded_patient_ids)]
        .drop(columns=["_patient_id"])
        .reset_index(drop=True)
    )

    try:
        if args.sample_sz < 250:
            if include_timepoints is not None:
                # Sample by unique patient (base ID before the last underscore) so all
                # requested time points are included for each sampled patient.
                train_df["_patient_id"] = (
                    train_df["study_id"].str.rsplit("_", n=1).str[0]
                )
                unique_patients = (
                    train_df["_patient_id"]
                    .drop_duplicates()
                    .sort_values()
                    .reset_index(drop=True)
                )
                sampled_patients = unique_patients.sample(
                    min(args.sample_sz, len(unique_patients)),
                    random_state=config["seed"],
                )
                train_df = (
                    train_df[train_df["_patient_id"].isin(sampled_patients)]
                    .drop(columns=["_patient_id"])
                    .reset_index(drop=True)
                )
            else:
                # Patient-level sampling so the same patients are selected regardless
                # of whether include_timepoints is set (e.g. mx_net_train vs nnunet_train).
                train_df["_patient_id"] = (
                    train_df["study_id"].str.rsplit("_", n=1).str[0]
                )
                unique_patients = (
                    train_df["_patient_id"]
                    .drop_duplicates()
                    .sort_values()
                    .reset_index(drop=True)
                )
                sampled_patients = unique_patients.sample(
                    min(args.sample_sz, len(unique_patients)),
                    random_state=config["seed"],
                )
                train_df = (
                    train_df[train_df["_patient_id"].isin(sampled_patients)]
                    .drop(columns=["_patient_id"])
                    .reset_index(drop=True)
                )
    except:
        pass

    print(f"Total number of studies in train: {len(train_df)}")
    print(f"Total number of studies in val: {len(val_df)}")
    print(f"Total number of studies in test: {len(test_df)}")

    # Patient-level data leakage check
    train_patients = set(train_df["study_id"].str.rsplit("_", n=1).str[0].unique())
    val_patients = set(val_df["study_id"].str.rsplit("_", n=1).str[0].unique())
    test_patients = set(test_df["study_id"].str.rsplit("_", n=1).str[0].unique())
    train_val_leak = train_patients & val_patients
    train_test_leak = train_patients & test_patients
    if train_val_leak:
        raise RuntimeError(
            f"Data leakage detected: {len(train_val_leak)} patient(s) appear in both train and val: "
            f"{sorted(train_val_leak)}"
        )
    if train_test_leak:
        raise RuntimeError(
            f"Data leakage detected: {len(train_test_leak)} patient(s) appear in both train and test: "
            f"{sorted(train_test_leak)}"
        )

    return train_df, val_df, test_df
