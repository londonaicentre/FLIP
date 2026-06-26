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

import copy
import gc
import json
import logging
import os.path
from pathlib import Path

import numpy as np
import pydicom
import torch
from data_utils import (
    Lesion,
    LesionDict,
    build_datalist,
    get_labels_from_radiology_row,
    get_lesion_label,
    get_xray_transforms,
    normalize_site_name,
)
from loss_and_metrics import compute_precision_recall_f1, get_bce_loss
from models import get_model
from monai.data import DataLoader, Dataset
from nvflare.apis.dxo import DataKind, from_shareable
from nvflare.apis.executor import Executor
from nvflare.apis.fl_constant import FLContextKey, ReservedKey, ReturnCode
from nvflare.apis.fl_context import FLContext
from nvflare.apis.shareable import Shareable, make_reply
from nvflare.apis.signal import Signal
from nvflare.app_common.abstract.model import make_model_learnable, model_learnable_to_dxo
from nvflare.app_common.app_constant import AppConstants
from nvflare.app_opt.pt.model_persistence_format_manager import PTModelPersistenceFormatManager

from flip import FLIP
from flip.constants import PTConstants, ResourceType
from flip.nvflare.metrics import send_metrics_value
from flip.utils import get_model_weights_diff


def _log_memory_usage(logger, prefix=""):
    rss_gb = 0.0
    cgroup_gb = 0.0
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    rss_gb = rss_kb / 1024**2
                    break
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory.current") as f:
            cgroup_gb = int(f.read().strip()) / 1024**3
    except Exception:
        try:
            with open("/sys/fs/cgroup/memory/memory.usage_in_bytes") as f:
                cgroup_gb = int(f.read().strip()) / 1024**3
        except Exception:
            pass
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        logger.info(f"{prefix} CPU_RSS={rss_gb:.2f}GiB  CGROUP={cgroup_gb:.2f}GiB  GPU_allocated={allocated:.2f}GiB  GPU_reserved={reserved:.2f}GiB")
    else:
        logger.info(f"{prefix} CPU_RSS={rss_gb:.2f}GiB  CGROUP={cgroup_gb:.2f}GiB  GPU=N/A")


class FLIP_TRAINER(Executor):
    def __init__(
        self,
        train_task_name=AppConstants.TASK_TRAIN,
        submit_model_task_name=AppConstants.TASK_SUBMIT_MODEL,
        exclude_vars=None,
        project_id="",
        query="",
    ):
        """Trainer for FLIP-based X-ray image classification.

        Args:
            train_task_name (str, optional): Task name for train task. Defaults to "train".
            submit_model_task_name (str, optional): Task name for submit model. Defaults to "submit_model".
            exclude_vars (list): List of variables to exclude during model loading.
        """
        super(FLIP_TRAINER, self).__init__()

        self._train_task_name = train_task_name
        self._submit_model_task_name = submit_model_task_name
        self._exclude_vars = exclude_vars

        # Logger
        self.logger = logging.getLogger(self.__class__.__name__)

        # Load the config
        self.config = {}
        working_dir = Path(__file__).parent.resolve()
        with open(str(working_dir / "config.json")) as file:
            self.config = json.load(file)
            self._epochs = self.config["LOCAL_ROUNDS"]
            self._lr_start = self.config["LR_START"]
            self._lr_end = self.config["LR_END"]
            self._val_split = self.config["VAL_SPLIT"]
            self._test_split = self.config["TEST_SPLIT"]
            self._split_seed = int(self.config.get("SPLIT_SEED", 42))
            self._lesions = self.config["LESIONS"]
            self._value_to_numerical = {int(i): j for i, j in self.config["value_to_numerical"].items()}
            if 0 not in self._value_to_numerical.keys() and 1 not in self._value_to_numerical.keys():
                raise ValueError("value_to_numerical must contain mappings for 0 and 1.")
            if "-1" in self._lesions.keys():
                self._normal_key = self._lesions["-1"]
                del self._lesions["-1"]
            else:
                self._normal_key = "Normal"
            self._batch_size = self.config["BATCH_SIZE"]
            self.validate_every = self.config["VALIDATE_EVERY"] if "VALIDATE_EVERY" in self.config.keys() else 1
            ark_cfg = self.config.get("ARKPLUS", {})
            self._use_teacher_student = bool(ark_cfg.get("USE_TEACHER_STUDENT", False))
            self._ema_mode = str(ark_cfg.get("EMA_MODE", "epoch")).lower()
            self._teacher_momentum = float(ark_cfg.get("TEACHER_MOMENTUM", 0.9))
            self._consistency_weight = float(ark_cfg.get("CONSISTENCY_WEIGHT", 0.1))
            self._amp_enabled = bool(ark_cfg.get("USE_AMP", False)) and torch.cuda.is_available()
            amp_dtype_name = str(ark_cfg.get("AMP_DTYPE", "float16")).lower()
            self._amp_dtype = torch.bfloat16 if amp_dtype_name == "bfloat16" else torch.float16

        self._lesions = LesionDict(items=[Lesion(id=int(k), lesion=v) for k, v in self._lesions.items()])

        # Setup the model
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        # Freeze backbone + projector, only train classifier head
        for name, param in self.model.named_parameters():
            if not name.startswith("ark_model.omni_heads"):
                param.requires_grad = False

        trainable = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        total = sum(p.numel() for p in self.model.parameters())
        self.logger.info(f"Trainable params: {trainable:,} / {total:,} (backbone frozen)")
        self.teacher_model = None
        self._teacher_initialized_from_global = False
        self._consistency_loss = torch.nn.MSELoss()
        try:
            self._amp_scaler = torch.amp.GradScaler("cuda", enabled=self._amp_enabled)
        except TypeError:
            self._amp_scaler = torch.cuda.amp.GradScaler(enabled=self._amp_enabled)
        self._setup_teacher_model()

        # Setup the transforms

        self._train_transforms = get_xray_transforms()
        self._val_transforms = get_xray_transforms(is_validation=True)

        # Data is selected lazily per NVFLARE client/site in execute(), where fl_ctx is available.
        self.flip = FLIP()
        self.project_id = project_id
        self.query = query
        self.dataframe = None
        self.train_dict = []
        self.val_dict = []
        self.training_dataset = None
        self.training_dataloader = None
        self.validation_dataset = None
        self.validation_dataloader = None
        self._loaded_site_name = None

        # Define optimizer
        self.optimizer = torch.optim.Adam(
            [p for p in self.model.parameters() if p.requires_grad],
            lr=self._lr_start,
        )
        gamma_lr = (self._lr_end / self._lr_start) ** (1 / self._epochs)
        self.scheduler = torch.optim.lr_scheduler.ExponentialLR(self.optimizer, gamma=gamma_lr)

        # Setup the persistence manager to save PT model.
        # The default training configuration is used by persistence manager
        # in case no initial model is found.
        self._default_train_conf = {"train": {"model": type(self.model).__name__}}
        self.persistence_manager = PTModelPersistenceFormatManager(
            data=self.model.state_dict(), default_train_conf=self._default_train_conf
        )

        if self._amp_enabled:
            self.logger.info(f"Mixed precision enabled for training with dtype={self._amp_dtype}.")

    def get_num_epochs(self):
        """Returns the number of epochs for training."""
        return self._epochs

    def _autocast(self):
        if hasattr(torch, "amp"):
            return torch.amp.autocast(
                device_type="cuda",
                dtype=self._amp_dtype,
                enabled=self._amp_enabled,
            )
        return torch.cuda.amp.autocast(dtype=self._amp_dtype, enabled=self._amp_enabled)

    def _site_name_from_context(self, fl_ctx: FLContext) -> str:
        site_name = fl_ctx.get_prop(FLContextKey.CLIENT_NAME, "")
        if not site_name:
            try:
                site_name = fl_ctx.get_identity_name()
            except Exception:
                site_name = ""
        return normalize_site_name(site_name) or "default"

    def _ensure_site_data_loaded(self, fl_ctx: FLContext):
        site_name = self._site_name_from_context(fl_ctx)
        if self._loaded_site_name == site_name and self.training_dataloader is not None:
            return

        self.train_dict, self.val_dict = build_datalist(
            is_test=False,
            config=self.config,
            site_name=site_name,
            project_id=self.project_id,
            logger=self.logger,
        )
        self.training_dataset = Dataset(self.train_dict, transform=self._train_transforms)
        self.training_dataloader = DataLoader(self.training_dataset, batch_size=self._batch_size, shuffle=True)
        self.validation_dataset = Dataset(self.val_dict, transform=self._val_transforms)
        self.validation_dataloader = DataLoader(self.validation_dataset, batch_size=self._batch_size, shuffle=False)
        self._loaded_site_name = site_name

        self.logger.info(
            f"DataLoader created for site={site_name}: training batches={len(self.training_dataloader)}, "
            f"validation batches={len(self.validation_dataloader)}"
        )
        self.log_dataset_class_distribution()

    def _setup_teacher_model(self):
        """Create the local EMA teacher used by Ark+-style client training."""
        if not self._use_teacher_student:
            return

        if not hasattr(self.model, "forward_with_features"):
            self.logger.warning(
                "ARKPLUS.USE_TEACHER_STUDENT=true, but the model does not expose "
                "forward_with_features(); falling back to standard training."
            )
            self._use_teacher_student = False
            return

        if self._ema_mode not in {"epoch", "iteration"}:
            self.logger.warning(
                f"Unsupported ARKPLUS.EMA_MODE={self._ema_mode!r}; using 'epoch' instead."
            )
            self._ema_mode = "epoch"

        if not 0.0 <= self._consistency_weight <= 1.0:
            raise ValueError("ARKPLUS.CONSISTENCY_WEIGHT must be between 0 and 1.")

        self.teacher_model = copy.deepcopy(self.model).to(self.device)
        self.teacher_model.eval()
        for param in self.teacher_model.parameters():
            param.requires_grad_(False)

        self.logger.info(
            "Ark+ teacher/student training enabled: "
            f"EMA_MODE={self._ema_mode}, TEACHER_MOMENTUM={self._teacher_momentum}, "
            f"CONSISTENCY_WEIGHT={self._consistency_weight}"
        )

    def _sync_teacher_from_student_once(self):
        """Initialize the local teacher from the first server-provided student weights."""
        if not self._use_teacher_student or self.teacher_model is None:
            return
        if self._teacher_initialized_from_global:
            return
        self.teacher_model.load_state_dict(self.model.state_dict())
        self.teacher_model.eval()
        self._teacher_initialized_from_global = True

    def _ema_update_teacher(self):
        """Update the local teacher as an EMA of the student model."""
        if not self._use_teacher_student or self.teacher_model is None:
            return

        momentum = self._teacher_momentum
        with torch.no_grad():
            student_state = self.model.state_dict()
            teacher_state = self.teacher_model.state_dict()
            for name, teacher_value in teacher_state.items():
                student_value = student_state[name].detach()
                if torch.is_floating_point(teacher_value):
                    teacher_value.mul_(momentum).add_(student_value.to(teacher_value.device), alpha=1.0 - momentum)
                else:
                    teacher_value.copy_(student_value.to(teacher_value.device))

    def get_image_and_label_list(self):
        """
        Returns a list of dictionaries containing a field "image" and a fields corresponding to each lesion with its
        label value.

        Args:
            dataframe (_type_): dataframe output by FLIP, which has to contain accession_id and
                columns for each of the lesions.

        Returns:
            _type_: list of dictionaries for data loading.
        """
        datalist = []

        # loop over each accession id in the train set
        for _, row in self.dataframe.iterrows():
            accession_id = row["accession_id"]
            # First, we load the radiology note; format should be: [project] - [lesion1,lesion2,lesion3_lesion3]
            pathology_dict = get_labels_from_radiology_row(
                row, self._lesions, self._value_to_numerical, self._normal_key
            )

            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.DICOM,
                    ],
                )
            except Exception as err:
                self.logger.error(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            all_images = list(accession_folder_path.rglob("*.dcm"))
            this_accession_matches = 0
            self.logger.info(f"Total base count found for accession_id {accession_id}: {len(all_images)}")

            for img in all_images:
                try:
                    _ = pydicom.dcmread(str(img))
                except Exception as e:
                    self.logger.error(f"Problem loading header of base image {str(img)}.")
                    self.logger.error(f"{e=}")
                    self.logger.error(f"{type(e)=}")
                    self.logger.error(f"{e.args=}")
                    continue

                # defines keys for image and segmentation
                item_ = {"image": str(img)}
                item_.update(pathology_dict)
                datalist.append(item_)
                this_accession_matches += 1

            self.logger.info(f"Added {this_accession_matches} image / label pairs for {accession_id}.")

        self.logger.info(f"Found {len(datalist)} files in total.")

        # Split whole image/label records. This keeps each DICOM path attached to
        # its labels while avoiding order-based class imbalance in the CSV.
        train_datalist, val_datalist, test_datalist = self._label_aware_split(
            datalist=datalist,
            label_names=[lesion.lesion for lesion in self._lesions.items],
            val_split=self._val_split,
            test_split=self._test_split,
            seed=self._split_seed,
        )

        self.logger.info(
            f"Found {len(train_datalist)} files for training, {len(val_datalist)} files for validation and "
            f"{len(test_datalist)} files for testing."
        )

        return train_datalist, val_datalist

    def _label_aware_split(self, datalist, label_names, val_split, test_split, seed):
        """Create deterministic train/val/test splits with positive labels represented when possible."""
        datalist = list(datalist)
        rng = np.random.default_rng(seed)
        rng.shuffle(datalist)

        n_total = len(datalist)
        n_train = int(n_total * (1 - val_split - test_split))
        n_val_end = int(n_total * (1 - test_split))
        n_val = n_val_end - n_train
        n_test = n_total - n_val_end

        splits = {"train": [], "val": [], "test": []}
        limits = {"train": n_train, "val": n_val, "test": n_test}
        assigned = set()

        self.logger.info(
            f"Using label-aware split with seed={seed}: train={n_train}, val={n_val}, test={n_test}."
        )

        def can_add(split_name):
            return len(splits[split_name]) < limits[split_name]

        def add_item(index, split_name):
            if index in assigned or not can_add(split_name):
                return False
            splits[split_name].append(datalist[index])
            assigned.add(index)
            return True

        label_positive_indices = {
            label: [i for i, item in enumerate(datalist) if item.get(label) == 1]
            for label in label_names
        }
        labels_by_rarity = sorted(label_names, key=lambda label: len(label_positive_indices[label]))

        for label in labels_by_rarity:
            positive_indices = list(label_positive_indices[label])
            rng.shuffle(positive_indices)

            if not positive_indices:
                self.logger.warning(f"No positive samples found for label {label} before splitting.")
                continue

            target_splits = ["train"]
            if len(positive_indices) >= 2 and n_val > 0:
                target_splits.append("val")
            if len(positive_indices) >= 3 and n_test > 0:
                target_splits.append("test")

            for split_name in target_splits:
                if any(item.get(label) == 1 for item in splits[split_name]):
                    continue
                for index in positive_indices:
                    if add_item(index, split_name):
                        break

        remaining_indices = [i for i in range(n_total) if i not in assigned]
        rng.shuffle(remaining_indices)

        for split_name in ("train", "val", "test"):
            for index in remaining_indices:
                if not can_add(split_name):
                    break
                add_item(index, split_name)

        self._log_split_balance(splits, label_names)
        return splits["train"], splits["val"], splits["test"]

    def _log_split_balance(self, splits, label_names):
        for split_name, split_rows in splits.items():
            self.logger.info(f"{split_name.upper()} split label balance ({len(split_rows)} samples):")
            for label in label_names:
                num_positive = sum(1 for item in split_rows if item.get(label) == 1)
                num_negative = sum(1 for item in split_rows if item.get(label) == 0)
                num_masked = sum(1 for item in split_rows if item.get(label) == -1)
                if num_positive == 0:
                    self.logger.warning(f"{split_name.upper()} split has no positive samples for {label}.")
                self.logger.info(
                    f"  {label:20s}: {num_positive:4d} positive, {num_negative:4d} negative, "
                    f"{num_masked:4d} masked/unknown"
                )

    def log_dataset_class_distribution(self):
        """Log the overall class distribution in training and validation datasets."""
        self.logger.info("\n" + "=" * 80)
        self.logger.info("OVERALL CLASS DISTRIBUTION IN DATASETS")
        self.logger.info("=" * 80)

        for dataset_name, datalist in [("TRAINING", self.train_dict), ("VALIDATION", self.val_dict)]:
            self.logger.info(f"\n{dataset_name} Dataset ({len(datalist)} samples):")
            for lesion in self._lesions.items:
                lesion_name = lesion.lesion
                all_labels = [item[lesion_name] for item in datalist]
                num_positive = sum(1 for label in all_labels if label == 1)
                num_negative = sum(1 for label in all_labels if label == 0)
                num_masked = sum(1 for label in all_labels if label == -1)

                if num_positive + num_negative > 0:
                    positive_ratio = num_positive / (num_positive + num_negative) * 100
                else:
                    positive_ratio = 0.0

                self.logger.info(
                    f"  {lesion_name:20s}: {num_positive:4d} positive, {num_negative:4d} negative, "
                    f"{num_masked:4d} masked/unknown"
                )
                self.logger.info(f"                        Positive ratio: {positive_ratio:.2f}% (excluding masked)")

        self.logger.info("=" * 80 + "\n")

    def local_train(self, fl_ctx: FLContext, weights, abort_signal, global_round):
        _log_memory_usage(self.logger, f"[BEFORE ROUND {global_round}]")
        # Set the model weights
        self.model.load_state_dict(state_dict=weights)
        self._sync_teacher_from_student_once()

        # Basic training
        self.model.train()
        if self.teacher_model is not None:
            self.teacher_model.eval()
        self.logger.info(f"Starting local train on device {self.device}")
        if self._use_teacher_student:
            self.logger.info(
                "Using Ark+ local teacher/student training: "
                "student is optimized, teacher is EMA-updated locally, "
                "and only student weights are returned to NVFLARE."
            )
        self.logger.info("Note: Batches with insufficient class representation will produce NaN metrics.")
        self.logger.info("      These NaN values will be ignored when computing epoch averages using np.nanmean().\n")
        training_metrics = {"loss": {"train": [], "val": []}, "f1-score": {}, "precision": {}, "recall": {}}
        for lesion_name in self._lesions.get_lesion_list():
            training_metrics["f1-score"][lesion_name] = {"train": [], "val": []}
            training_metrics["precision"][lesion_name] = {"train": [], "val": []}
            training_metrics["recall"][lesion_name] = {"train": [], "val": []}

        self._n_iterations = 0
        for epoch in range(self._epochs):
            training_metrics_ = {"loss": {"train": [], "val": []}, "f1-score": {}, "precision": {}, "recall": {}}
            for lesion_name in self._lesions.get_lesion_list():
                training_metrics_["f1-score"][lesion_name] = {"train": [], "val": []}
                training_metrics_["precision"][lesion_name] = {"train": [], "val": []}
                training_metrics_["recall"][lesion_name] = {"train": [], "val": []}

            for i, batch in enumerate(self.training_dataloader):
                if abort_signal.triggered:
                    # If abort_signal is triggered, we simply return.
                    # The outside function will check it again and decide steps to take.
                    return

                images = batch["image"].to(self.device)
                labels = get_lesion_label(batch, self._lesions).to(self.device)

                # Log class distribution for this batch
                labels_np = labels.detach().cpu().numpy()
                batch_info = (
                    f"Epoch {epoch + 1}, Train Batch {i + 1}/{len(self.training_dataloader)}, "
                    f"Batch size: {labels.shape[0]} - "
                )
                for lesion_idx, lesion in enumerate(self._lesions.items):
                    lesion_labels = labels_np[:, lesion_idx]
                    # Filter out -1 (unknown/masked) values
                    valid_labels = lesion_labels[lesion_labels != -1]
                    if len(valid_labels) > 0:
                        num_positive = np.sum(valid_labels == 1)
                        num_negative = np.sum(valid_labels == 0)
                        batch_info += f"{lesion.lesion}: {num_positive} pos / {num_negative} neg; "
                    else:
                        batch_info += f"{lesion.lesion}: all masked; "
                self.logger.info(batch_info)

                self.optimizer.zero_grad()
                with self._autocast():
                    if self._use_teacher_student and self.teacher_model is not None:
                        student_features, output = self.model.forward_with_features(images)
                        with torch.no_grad():
                            teacher_features, _ = self.teacher_model.forward_with_features(images)
                        classification_loss = get_bce_loss(output, labels)
                        consistency_loss = self._consistency_loss(student_features, teacher_features.detach())
                        loss = (
                            (1.0 - self._consistency_weight) * classification_loss
                            + self._consistency_weight * consistency_loss
                        )
                    else:
                        output = self.model(images)
                        loss = get_bce_loss(output, labels)
                self._amp_scaler.scale(loss).backward()
                self._amp_scaler.step(self.optimizer)
                self._amp_scaler.update()
                if self._ema_mode == "iteration":
                    self._ema_update_teacher()
                training_metrics_["loss"]["train"].append(loss.item())
                output = torch.sigmoid(output)
                for pathology in self._lesions.get_lesion_list():
                    precision, recall, f1_score = compute_precision_recall_f1(
                        output, labels, pathology, lesions=self._lesions
                    )
                    training_metrics_["precision"][pathology]["train"].append(precision)
                    training_metrics_["recall"][pathology]["train"].append(recall)
                    training_metrics_["f1-score"][pathology]["train"].append(f1_score)
                self._n_iterations += 1
            if self._ema_mode == "epoch":
                self._ema_update_teacher()
            if epoch % self.validate_every == 0:
                self.model.eval()
                if self.teacher_model is not None:
                    self.teacher_model.eval()
                with torch.no_grad():
                    for i, batch in enumerate(self.validation_dataloader):
                        if abort_signal.triggered:
                            # If abort_signal is triggered, we simply return.
                            # The outside function will check it again and decide steps to take.
                            return

                        images = batch["image"].to(self.device)
                        labels = get_lesion_label(batch, self._lesions).to(self.device)

                        # Log class distribution for this validation batch
                        labels_np = labels.detach().cpu().numpy()
                        batch_info = (
                            f"Epoch {epoch + 1}, Val Batch {i + 1}/{len(self.validation_dataloader)}, "
                            f"Batch size: {labels.shape[0]} - "
                        )
                        for lesion_idx, lesion in enumerate(self._lesions.items):
                            lesion_labels = labels_np[:, lesion_idx]
                            # Filter out -1 (unknown/masked) values
                            valid_labels = lesion_labels[lesion_labels != -1]
                            if len(valid_labels) > 0:
                                num_positive = np.sum(valid_labels == 1)
                                num_negative = np.sum(valid_labels == 0)
                                batch_info += f"{lesion.lesion}: {num_positive} pos / {num_negative} neg; "
                            else:
                                batch_info += f"{lesion.lesion}: all masked; "
                        self.logger.info(batch_info)

                        with self._autocast():
                            output = self.model(images)
                            loss = get_bce_loss(output, labels).item()
                        training_metrics_["loss"]["val"].append(loss)
                        output = torch.sigmoid(output)
                        for pathology in self._lesions.get_lesion_list():
                            precision, recall, f1_score = compute_precision_recall_f1(
                                output, labels, pathology, lesions=self._lesions
                            )
                            training_metrics_["precision"][pathology]["val"].append(precision)
                            training_metrics_["recall"][pathology]["val"].append(recall)
                            training_metrics_["f1-score"][pathology]["val"].append(f1_score)
                self.model.train()

            self.scheduler.step()

            # Aggregate metrics:

            for metric, metric_dump in training_metrics_.items():
                if metric == "loss":
                    training_metrics["loss"]["train"].append(np.nanmean(metric_dump["train"]))
                    if epoch % self.validate_every == 0:
                        training_metrics["loss"]["val"].append(np.nanmean(metric_dump["val"]))
                    else:
                        if len(training_metrics_["loss"]["val"]) == 0:
                            training_metrics["loss"]["val"].append(0)
                        else:
                            training_metrics["loss"]["val"].append(training_metrics["loss"]["val"][-1])
                else:
                    for lesion_name in self._lesions.get_lesion_list():
                        training_metrics[metric][lesion_name]["train"].append(
                            np.nanmean(metric_dump[lesion_name]["train"])
                        )
                        if epoch % self.validate_every == 0:
                            training_metrics[metric][lesion_name]["val"].append(
                                np.nanmean(metric_dump[lesion_name]["val"])
                            )
                        else:
                            if len(training_metrics[metric][lesion_name]["val"]) == 0:
                                training_metrics[metric][lesion_name]["val"].append(0)
                            else:
                                training_metrics[metric][lesion_name]["val"].append(
                                    training_metrics[metric][lesion_name]["val"][-1]
                                )

            # Get text
            message = f"epoch {epoch + 1}/{self.get_num_epochs()} - "
            for metric, metric_values in training_metrics.items():
                if metric == "loss":
                    message += (
                        f"{metric}: train={metric_values['train'][-1]:.4f}, val={metric_values['val'][-1]:.4f};\t"
                    )
                else:
                    for lesion_name, lesion_values in metric_values.items():
                        lvt = lesion_values["train"][-1]
                        lvv = lesion_values["val"][-1]
                        # Format with 'N/A' if NaN, otherwise show the value
                        lvt_str = "N/A" if np.isnan(lvt) else f"{lvt:.4f}"
                        lvv_str = "N/A" if np.isnan(lvv) else f"{lvv:.4f}"
                        message += f"{metric}-{lesion_name}: train={lvt_str}, val={lvv_str};\t"

            message += "\n"
            self.logger.info(message)
            self.log_info(fl_ctx, message)

            # Log summary of NaN occurrences for this epoch
            nan_summary = f"Epoch {epoch + 1} NaN Summary:\n"
            has_nan = False
            for metric in ["f1-score", "precision", "recall"]:
                for lesion_name in self._lesions.get_lesion_list():
                    train_values = training_metrics_[metric][lesion_name]["train"]
                    train_nans = sum(1 for x in train_values if np.isnan(x))
                    train_valid = len([x for x in train_values if not np.isnan(x)])

                    if epoch % self.validate_every == 0:
                        val_values = training_metrics_[metric][lesion_name]["val"]
                        val_nans = sum(1 for x in val_values if np.isnan(x))
                        val_valid = len([x for x in val_values if not np.isnan(x)])
                    else:
                        val_nans = 0
                        val_valid = 0

                    if train_nans > 0 or val_nans > 0:
                        has_nan = True
                        train_total = len(train_values)
                        val_total = len(val_values) if epoch % self.validate_every == 0 else 0

                        train_avg = training_metrics[metric][lesion_name]["train"][-1]
                        val_avg = training_metrics[metric][lesion_name]["val"][-1]

                        # Format with 'N/A (all batches NaN)' if NaN, otherwise show the value
                        train_avg_str = "N/A (all batches NaN)" if np.isnan(train_avg) else f"{train_avg:.4f}"
                        val_avg_str = "N/A (all batches NaN)" if np.isnan(val_avg) else f"{val_avg:.4f}"

                        nan_summary += (
                            f"  {lesion_name} {metric}: {train_nans}/{train_total} train batches had NaN "
                            f"({train_valid} valid), {val_nans}/{val_total} val batches had NaN ({val_valid} valid)\n"
                            f"    -> Averaged from valid batches: train={train_avg_str}, val={val_avg_str}\n"
                        )

            if has_nan:
                self.logger.info(nan_summary)
                self.log_info(fl_ctx, nan_summary)
            # Send metrics over to FLIP
            round = global_round * (self._epochs) + epoch + 1

            # Send loss metrics - convert NaN to 0.0
            train_loss = training_metrics["loss"]["train"][-1]
            val_loss = training_metrics["loss"]["val"][-1]

            if np.isnan(train_loss):
                self.logger.warning("TRAIN_LOSS is NaN (no valid batches) - sending 0.0")
                train_loss = 0.0

            if np.isnan(val_loss):
                self.logger.warning("VAL_LOSS is NaN (no valid batches) - sending 0.0")
                val_loss = 0.0

            send_metrics_value(
                label="TRAIN_LOSS",
                round=round,
                value=train_loss,
                fl_ctx=fl_ctx,
                flip=self.flip,
            )
            send_metrics_value(
                label="VAL_LOSS",
                round=round,
                value=val_loss,
                fl_ctx=fl_ctx,
                flip=self.flip,
            )

            for metric in ["f1-score", "precision", "recall"]:
                for lesion_name in self._lesions.get_lesion_list():
                    train_value = training_metrics[metric][lesion_name]["train"][-1]
                    val_value = training_metrics[metric][lesion_name]["val"][-1]

                    # Convert NaN to 0.0 before sending
                    if np.isnan(train_value):
                        self.logger.warning(
                            f"TRAIN-{metric.upper()} for {lesion_name} is NaN (no valid batches) - sending 0.0"
                        )
                        train_value = 0.0

                    if np.isnan(val_value):
                        self.logger.warning(
                            f"VAL-{metric.upper()} for {lesion_name} is NaN (no valid batches) - sending 0.0"
                        )
                        val_value = 0.0

                    send_metrics_value(
                        label=f"{'train'.upper()}-{metric.upper()}",
                        round=round,
                        value=train_value,
                        fl_ctx=fl_ctx,
                        flip=self.flip,
                    )
                    send_metrics_value(
                        label=f"{'val'.upper()}-{metric.upper()}",
                        round=round,
                        value=val_value,
                        fl_ctx=fl_ctx,
                        flip=self.flip,
                    )

    def execute(
        self,
        task_name: str,
        shareable: Shareable,
        fl_ctx: FLContext,
        abort_signal: Signal,
    ) -> Shareable:
        if task_name == self._train_task_name:
            global_round = shareable.get_header(AppConstants.CURRENT_ROUND)
            self._ensure_site_data_loaded(fl_ctx)

            # Get model weights
            dxo = from_shareable(shareable)

            # Ensure data kind is weights.
            if not dxo.data_kind == DataKind.WEIGHTS:
                self.log_error(
                    fl_ctx,
                    f"data_kind expected WEIGHTS but got {dxo.data_kind} instead.",
                )
                return make_reply(ReturnCode.BAD_TASK_DATA)

            # Convert weights to tensor../ Run training
            torch_weights = {k: torch.as_tensor(v) for k, v in dxo.data.items()}
            self.local_train(fl_ctx, torch_weights, abort_signal, global_round)

            # Check the abort_signal after training.
            # local_train returns early if abort_signal is triggered.
            if abort_signal.triggered:
                return make_reply(ReturnCode.TASK_ABORTED)

            # Save the local model after training.
            self.save_local_model(fl_ctx)

            # Memory cleanup after training round
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            _log_memory_usage(self.logger, f"[AFTER ROUND {global_round}]")

            # Get the new state dict and send as weights
            new_weights = self.model.state_dict()
            outgoing_dxo = get_model_weights_diff(dxo.data, new_weights, self._n_iterations)
            return outgoing_dxo.to_shareable()

        elif task_name == self._submit_model_task_name:
            # Load local model
            ml = self.load_local_model(fl_ctx)

            # Get the model parameters and create dxo from it
            dxo = model_learnable_to_dxo(ml)
            return dxo.to_shareable()
        else:
            return make_reply(ReturnCode.TASK_UNKNOWN)

    def save_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            os.makedirs(models_dir)
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        ml = make_model_learnable(self.model.state_dict(), {})
        self.persistence_manager.update(ml)
        torch.save(self.persistence_manager.to_persistence_dict(), model_path)
        if self._use_teacher_student and self.teacher_model is not None:
            teacher_path = model_path + ".teacher.pt"
            torch.save(self.teacher_model.state_dict(), teacher_path)

    def load_local_model(self, fl_ctx: FLContext):
        run_dir = fl_ctx.get_engine().get_workspace().get_run_dir(fl_ctx.get_prop(ReservedKey.RUN_NUM))
        models_dir = os.path.join(run_dir, PTConstants.PTModelsDir)
        if not os.path.exists(models_dir):
            return None
        model_path = os.path.join(models_dir, PTConstants.PTLocalModelName)

        self.persistence_manager = PTModelPersistenceFormatManager(
            data=torch.load(model_path), default_train_conf=self._default_train_conf
        )
        ml = self.persistence_manager.to_model_learnable(exclude_vars=self._exclude_vars)
        teacher_path = model_path + ".teacher.pt"
        if self._use_teacher_student and self.teacher_model is not None and os.path.exists(teacher_path):
            self.teacher_model.load_state_dict(torch.load(teacher_path, map_location=self.device))
            self.teacher_model.eval()
            self._teacher_initialized_from_global = True
        return ml
