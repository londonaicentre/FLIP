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

"""NVFLARE Client API script for the FLIP latent diffusion tutorial.

Trains a ``DiffusionModelUNet`` inside a **frozen** autoencoder's latent space, on 3-D brain MRI, as
an ordinary single-stage FedAvg job: one ``train`` task and one ``validate`` task, dispatched on
``flare.is_train()`` / ``flare.is_evaluate()``.

**Conditioning.** A brain MRI study is four co-registered sequences (FLAIR, T1w, T1Gd, T2w), and the
model is told which one it is denoising: the modality is one-hot encoded and fed to the UNet as a
length-1 cross-attention sequence (``modality.py``). That is what makes one model able to generate a
T2w *or* a T1Gd volume on demand rather than an average of the four. Two knobs in ``config.json``
switch the whole thing, and they move together:

* ``MODALITIES`` — which sequences enter the cohort at all;
* ``net_config.diffusion_model.with_conditioning`` / ``cross_attention_dim`` — whether the model is
  told, and over how many classes.

Conditioned on all four is ``MODALITIES`` of length 4 with ``with_conditioning: true`` and
``cross_attention_dim: 4``. The unconditional single-sequence form is ``["T1w"]`` with
``with_conditioning: false`` and ``cross_attention_dim: 0``. Anything else is a mismatch, and the
parity test in fl-tutorials/tests/ refuses it — an unconditioned model quietly trained on four
mixed sequences is a plausible-looking way to get blur.

The autoencoder is not trained here. It arrives as an uploaded checkpoint declared by
``SERVER_CHECKPOINT`` in ``config.json``, which the FL server loads into the round-0 global model and
broadcasts — so this script never reads the checkpoint file, it simply receives autoencoder weights
along with the diffusion model's at round 0. Thereafter ``AGGREGATE_ONLY_REGEX`` keeps only
``diffusion_model.*`` on the wire, and the client rebuilds the full model from its cached round-0
broadcast.

Because the autoencoder is fixed for the whole run, this script freezes it explicitly
(``requires_grad_(False)`` + ``eval()``). The previous two-stage job left it *implicitly* frozen —
merely absent from the optimizer — which meant gradients accumulated on its parameters every step
and were never applied, and training ran it in ``train()`` mode while validation ran it in
``eval()``. Freezing is numerically neutral here (``AutoencoderKL`` uses GroupNorm and no dropout)
and removes both wrinkles.

The denoising maths is unchanged from that job's ``train_dm`` phase.
"""

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import nvflare.client as flare
import torch
from plot_utils import samples_enabled, save_grid
from flip import FLIP
from flip.constants import FlipConstants, ResourceType
from latent_utils import build_inferer
from modality import modality_of, one_hot_condition
from models import get_model
from monai.data import DataLoader, Dataset
from monai.networks.schedulers import DDPMScheduler
from nvflare.client.tracking import SummaryWriter
from torch.amp import GradScaler, autocast
from transforms import get_brain_mri_transforms

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project_id", type=str, default="")
    return parser.parse_args()


def load_query() -> str:
    """Read the cohort query from the client app config.

    NVFlare's TaskScriptRunner does a naive whitespace split on task_script_args, so the SQL query
    (which can contain spaces) is plumbed via the top-level ``query`` key in
    ``config/config_fed_client.json`` rather than as a CLI flag. In dev/simulator mode this is
    ignored by ``flip.get_dataframe``.
    """
    client_cfg = Path(__file__).parent.parent / "config" / "config_fed_client.json"
    if client_cfg.exists():
        try:
            return json.loads(client_cfg.read_text()).get("query", "")
        except Exception:
            return ""
    return ""


def load_config() -> dict:
    """Load the user-supplied config.json that sits next to this script."""
    config_path = Path(__file__).parent.resolve() / "config.json"
    with open(config_path) as f:
        return json.load(f)


def split_for_site(items: list[dict], site_name: str) -> list[dict]:
    """Give each simulated site half the cohort, splitting on accession rather than on items.

    Local/simulator runs point every client at the same DEV dataset, so without this both sites
    train on identical data; in production each trust's data-access API already scopes the cohort to
    its own. The NVFLARE simulator names its clients "site-1"/"site-2" — normalise so the split
    actually applies (as arkplus_fine_tuning's data_utils does). Production trust names never match,
    so real runs are returned unsplit.

    Splitting on the *accession* keeps a study's sequences together: halving the item list would cut
    through the middle of a study and give one site a brain's T1w and the other its T2w.
    """
    normalised = site_name.replace("-", "")
    if normalised not in ("site1", "site2"):
        return items
    accessions = list(dict.fromkeys(item["accession_id"] for item in items))
    half = len(accessions) // 2
    mine = set(accessions[:half] if normalised == "site1" else accessions[half:])
    return [item for item in items if item["accession_id"] in mine]


def batch_accumulation_step(batch_size: int) -> int:
    """Accumulate gradients up to an effective batch of 8 when the configured batch is smaller."""
    if batch_size < 8:
        return 8 // batch_size
    return 1


def batch_condition(batch: dict, config: dict, device: torch.device) -> torch.Tensor | None:
    """The cross-attention condition for a batch, or None when this run is unconditional.

    Returns None whenever ``with_conditioning`` is off, which is what the inferer expects for an
    unconditional model — passing a context tensor to a UNet built without cross-attention layers
    raises rather than being ignored, so the two must be read from the same place.
    """
    if not config["net_config"]["diffusion_model"]["with_conditioning"]:
        return None
    return one_hot_condition(batch["modality_index"], len(config["MODALITIES"]), device)


def freeze_autoencoder(model: torch.nn.Module) -> None:
    """Freeze the supplied autoencoder: no gradients, always eval mode.

    The autoencoder is fixed for the whole run (it comes from ``SERVER_CHECKPOINT`` and is excluded
    from aggregation by ``AGGREGATE_ONLY_REGEX``), so there is no reason to build a gradient graph
    through it or to let its normalisation behave differently between training and validation.
    """
    model.autoencoder.requires_grad_(False)
    model.autoencoder.eval()


class LatentDiffusionTrainer:
    """Holds the model, loss, optimizer, scheduler, and data for latent diffusion training.

    One instance lives for the whole ``flare.is_running()`` loop, so the optimizer state persists
    across global rounds.
    """

    def __init__(self, config: dict, project_id: str, query: str):
        self.config = config
        self.project_id = project_id

        self.params_diffusion = {"lr": config["LR_DM"], "epochs": config.get("LOCAL_ROUNDS", 5)}

        # Model creation
        self.model = get_model()
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        freeze_autoencoder(self.model)

        self.losses_dm = {"loss": torch.nn.functional.mse_loss}
        self.optimizers_dm = {
            "optimizer": torch.optim.Adam(
                self.model.diffusion_model.parameters(), lr=self.params_diffusion["lr"], weight_decay=1e-6, amsgrad=True
            ),
            "scheduler": DDPMScheduler(
                num_train_timesteps=1000,
                schedule="scaled_linear_beta",
                clip_sample=False,
                prediction_type="epsilon",
                beta_start=0.0015,
                beta_end=0.0195,
            ),
        }

        # Data loading
        self.flip = FLIP()
        dataframe = self.flip.get_dataframe(project_id=project_id, query=query)
        self.train_items, self.val_items = self.build_datalist(dataframe)

        # Per-site split for local/simulator runs where every client reads the same DEV dataset;
        # in production each trust's data-access API already scopes the cohort to its own data.
        # The NVFLARE simulator names its clients "site-1"/"site-2"; normalise so the split actually applies
        # (as arkplus_fine_tuning's data_utils does). Production trust names never match, so real runs are unsplit.
        self.site_name = flare.get_site_name()
        self.train_items = split_for_site(self.train_items, self.site_name)

        self._train_dataset = Dataset(self.train_items, transform=get_brain_mri_transforms())
        self._val_dataset = Dataset(self.val_items, transform=get_brain_mri_transforms(is_validation=True))

    def build_datalist(self, dataframe) -> tuple[list, list]:
        """Fetch each accession's NIfTI series and split into train/validation lists.

        One study yields one sample per configured modality — four, unless ``MODALITIES`` narrows
        the run to fewer. Each sample carries the modality label and its index in ``MODALITIES``
        alongside the path; ``transforms.py`` passes both through untouched, and the collate turns
        the index into a batch tensor.

        **The split is by accession.** The four series of one study are four views of the same
        brain, so a file-wise split would leak a subject across the boundary and make the validation
        score meaningless. Whole studies go to one side or the other.
        """
        modalities = self.config["MODALITIES"]
        by_accession: dict[str, list[dict]] = {}

        for accession_id in dataframe["accession_id"]:
            try:
                accession_folder_path = self.flip.get_by_accession_number(
                    self.project_id,
                    accession_id,
                    resource_type=[
                        ResourceType.NIFTI,
                    ],
                )
            except Exception as err:
                logger.info(f"Could not get image data folder path for {accession_id}: {err}")
                continue

            items = []
            for image in sorted(accession_folder_path.rglob("input_*.nii.gz")):
                modality = modality_of(image, modalities)
                if modality is None:
                    # A sequence this run excludes, not an error: MODALITIES is the cohort filter.
                    continue
                items.append(
                    {
                        "image": str(image),
                        "accession_id": str(accession_id),
                        "modality": modality,
                        "modality_index": modalities.index(modality),
                    }
                )
            if items:
                by_accession[str(accession_id)] = items

        found = sum(len(items) for items in by_accession.values())
        logger.info(
            f"Found {found} volume(s) across {len(by_accession)} accession(s), "
            f"modalities {modalities}."
        )
        if not by_accession:
            raise RuntimeError(
                f"No volumes matched MODALITIES={modalities}. The cohort's files are named "
                "input_<modality>_<case>.nii.gz — check that the configured labels are the ones "
                "the data actually carries."
            )

        # Validation / train splits, whole accessions at a time.
        accessions = list(by_accession)
        val_accessions = accessions[: int(self.config["VAL_SPLIT"] * len(accessions))]
        train_accessions = accessions[len(val_accessions) :]
        return (
            [item for accession in train_accessions for item in by_accession[accession]],
            [item for accession in val_accessions for item in by_accession[accession]],
        )

    def make_loaders(self, batch_size: int, shuffle: bool = True) -> tuple[DataLoader, DataLoader]:
        train_loader = DataLoader(self._train_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        val_loader = DataLoader(self._val_dataset, batch_size=batch_size, shuffle=shuffle, num_workers=1)
        return train_loader, val_loader

    def val_loader(self, batch_size: int) -> DataLoader:
        return DataLoader(self._val_dataset, batch_size=batch_size, shuffle=False, num_workers=1)

    def load_weights(self, weights: dict[str, torch.Tensor]) -> None:
        self.model.load_state_dict(state_dict=weights, strict=False)
        # The broadcast carries the autoencoder at round 0; re-assert the freeze afterwards so a
        # load can never quietly re-enable grads or training mode on it.
        freeze_autoencoder(self.model)

    def train(self, writer: SummaryWriter, global_round: int) -> int:
        """One ``train`` round: local diffusion-model epochs. Returns the iteration count."""
        accumulation_step = batch_accumulation_step(self.config["BATCH_SIZE"])
        train_loader, val_loader = self.make_loaders(self.config["BATCH_SIZE"])
        self.model.diffusion_model.to(device=self.device)
        self.model.autoencoder.to(device=self.device)

        inferer, ldm_latent_shape = build_inferer(
            self.model,
            self.optimizers_dm["scheduler"],
            self.config["spatial_shape"],
            next(iter(train_loader))["image"],
            self.device,
            self.config.get("LATENT_SCALE_FACTOR"),
        )
        scaler = GradScaler()

        # Basic training
        train_loss = []
        val_loss = []
        epochs = self.params_diffusion["epochs"]
        for epoch in range(epochs):
            self.model.diffusion_model.train()
            batch_acc_counter = 0
            train_loss_epoch = 0

            for batch in train_loader:
                images = batch["image"].to(self.device)
                condition = batch_condition(batch, self.config, self.device)

                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(
                        [images.shape[0]]
                        + [self.model.autoencoder.encoder.blocks[-1].out_channels]
                        + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    noise_pred = inferer(
                        inputs=images,
                        diffusion_model=self.model.diffusion_model,
                        autoencoder_model=self.model.autoencoder,
                        noise=noise,
                        timesteps=timesteps,
                        condition=condition,
                        mode="crossattn",
                    )
                    loss = self.losses_dm["loss"](noise.float(), noise_pred.float())

                if True in torch.isnan(loss) or True in torch.isnan(noise_pred):
                    logger.error("Found NaN on training loss; stopping training on site.")
                    raise RuntimeError("NaN loss during train")

                scaler.scale(loss).backward()
                batch_acc_counter += 1
                if batch_acc_counter == accumulation_step:
                    scaler.step(self.optimizers_dm["optimizer"])
                    scaler.update()
                    batch_acc_counter = 0
                    self.optimizers_dm["optimizer"].zero_grad(set_to_none=True)
                train_loss_epoch += loss.item()

            train_loss.append(train_loss_epoch / max(1, len(train_loader)))

            # Validation loss
            val_loss_epoch = 0
            for batch in val_loader:
                images = batch["image"].to(self.device)
                condition = batch_condition(batch, self.config, self.device)
                self.model.diffusion_model.eval()
                with autocast(enabled=False, device_type=self.device.type):
                    noise = torch.randn(
                        [images.shape[0]]
                        + [self.model.autoencoder.encoder.blocks[-1].out_channels]
                        + ldm_latent_shape
                    ).to(self.device)
                    timesteps = torch.randint(
                        0,
                        self.optimizers_dm["scheduler"].num_train_timesteps,
                        (images.shape[0],),
                        device=self.device,
                    ).long()
                    with torch.no_grad():
                        noise_pred = inferer(
                            inputs=images,
                            diffusion_model=self.model.diffusion_model,
                            autoencoder_model=self.model.autoencoder,
                            noise=noise,
                            timesteps=timesteps,
                            condition=condition,
                            mode="crossattn",
                        )
                    val_loss_epoch += self.losses_dm["loss"](noise.float(), noise_pred.float()).item()

            val_loss.append(val_loss_epoch / max(1, len(val_loader)))

            logger.info(f"Epoch {epoch + 1} / {epochs};\n Total loss DM: {np.mean(train_loss)}")

            step = global_round * epochs + epoch + 1
            writer.add_scalar("Total loss DM@epoch", float(np.mean(train_loss)), global_step=step)
            writer.add_scalar("Validation loss DM@epoch", float(np.mean(val_loss)), global_step=step)

        return epochs * len(train_loader)


def validate(
    model: torch.nn.Module,
    test_loader: DataLoader,
    scheduler: DDPMScheduler,
    config: dict,
    device: torch.device,
    writer: SummaryWriter,
) -> float:
    """Score the aggregated diffusion model on the local held-out split.

    Args:
        model (torch.nn.Module): The composite LDM network with the broadcast global weights loaded.
        test_loader (DataLoader): Held-out data loader.
        scheduler (DDPMScheduler): The DDPM noise scheduler.
        config (dict): The user app config (``config.json``).
        device (torch.device): Device to run on.
        writer (SummaryWriter): NVFLARE Client API metrics writer.

    Returns:
        float: Mean noise-prediction MSE over the held-out split.
    """
    loss_fn = torch.nn.functional.mse_loss

    model.diffusion_model.to(device=device)
    model.autoencoder.to(device=device)
    inferer, ldm_latent_shape = build_inferer(
        model,
        scheduler,
        config["spatial_shape"],
        next(iter(test_loader))["image"],
        device,
        config.get("LATENT_SCALE_FACTOR"),
    )

    model.diffusion_model.eval()
    model.autoencoder.eval()

    val_loss = []
    for batch in test_loader:
        images = batch["image"].to(device)
        condition = batch_condition(batch, config, device)
        with autocast(enabled=False, device_type=device.type):
            with torch.no_grad():
                noise = torch.randn(
                    [images.shape[0]] + [model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
                ).to(device)
                timesteps = torch.randint(
                    0, scheduler.num_train_timesteps, (images.shape[0],), device=device
                ).long()
                logger.info(f"Sizes: images = {images.shape}, noise = {noise.shape}")
                noise_pred = inferer(
                    inputs=images,
                    diffusion_model=model.diffusion_model,
                    autoencoder_model=model.autoencoder,
                    noise=noise,
                    timesteps=timesteps,
                    condition=condition,
                    mode="crossattn",
                )
            val_loss.append(loss_fn(noise.float(), noise_pred.float()).item())

    # Sampling is the only honest read on a diffusion model: the noise-prediction MSE above barely
    # moves between a model that generates radiographs and one that generates texture. It also
    # exercises the frozen autoencoder's DECODER, which nothing else in this job touches — so a
    # mis-loaded checkpoint (tolerated by strict=False) shows up here as noise and nowhere else.
    # Run it when either the dev sanity check or client-local debug images are asked for; it is a full
    # reverse diffusion (num_train_timesteps steps), so it stays off the per-epoch path.
    if FlipConstants.LOCAL_DEV or samples_enabled(config):
        modalities = config["MODALITIES"]
        conditioned = config["net_config"]["diffusion_model"]["with_conditioning"]
        # Sample ONE volume per modality when conditioned, so the grid answers the question
        # conditioning exists to answer: do these columns actually differ? A batch of samples all
        # drawn under the same condition cannot show that. Unconditioned, one sample is the lot.
        sample_count = len(modalities) if conditioned else 1
        how = "conditioned" if conditioned else "unconditional"
        logger.info(f"[DEBUG]: Sampling {sample_count} volume(s) ({how})...")
        noise = torch.randn(
            [sample_count] + [model.autoencoder.encoder.blocks[-1].out_channels] + ldm_latent_shape
        ).to(device)
        conditioning = (
            one_hot_condition(torch.arange(sample_count), len(modalities), device) if conditioned else None
        )
        # save_intermediates=False: LatentDiffusionInferer would otherwise retain the whole denoising
        # trajectory (one decoded tensor per timestep) and the second return value is discarded.
        sampled_images = inferer.sample(
            input_noise=noise,
            conditioning=conditioning,
            diffusion_model=model.diffusion_model,
            scheduler=scheduler,
            save_intermediates=False,
            autoencoder_model=model.autoencoder,
        )
        logger.info(f"Sampled images shape: {sampled_images.detach().cpu().numpy().shape}")
        # One grid column per modality, in MODALITIES order — the filename says so, since the grid
        # itself carries no labels.
        name = f"samples_{'_'.join(modalities)}" if conditioned else "samples"
        save_grid({"sample": sampled_images}, name, config, site_name=flare.get_site_name())

    mean_val_loss = float(np.mean(val_loss)) if val_loss else float("nan")
    logger.info(f"Validation DM: {mean_val_loss}")

    writer.add_scalar("Total loss DM (val)", mean_val_loss, global_step=0)

    return mean_val_loss


def to_torch_weights(input_model: flare.FLModel) -> dict[str, torch.Tensor]:
    return {k: torch.as_tensor(v) for k, v in input_model.params.items()}


def send_weight_diff(original_params: dict, model: torch.nn.Module, n_iterations: int) -> None:
    """Send the weight diff for a completed training round.

    Built one tensor at a time to avoid holding a second full copy of the model in RAM (mirrors
    ``flip.utils.get_model_weights_diff``). The diff covers the whole composite model, but the
    ``KeepOnlyVars`` result filter (wired from ``AGGREGATE_ONLY_REGEX``) drops everything outside
    ``diffusion_model.*`` before it goes on the wire — which also keeps the frozen autoencoder's
    zero diffs out of the DP filter's percentile cutoff, where they would otherwise flatten it.
    """
    diff = {}
    for k, v in model.state_dict().items():
        new_arr = v.detach().cpu().numpy()
        diff[k] = new_arr - np.asarray(original_params[k])
        del new_arr

    flare.send(
        flare.FLModel(
            params=diff,
            params_type="DIFF",
            meta={"NUM_STEPS_CURRENT_ROUND": n_iterations},
        )
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    args = parse_args()
    config = load_config()

    flare.init()
    writer = SummaryWriter()

    trainer = LatentDiffusionTrainer(config, project_id=args.project_id, query=load_query())

    while flare.is_running():
        input_model = flare.receive()
        if input_model is None:
            break

        weights = to_torch_weights(input_model)
        global_round = input_model.current_round or 0

        if flare.is_train():
            logger.info(f"[LDM trainer] received train task (round {global_round})")
            trainer.load_weights(weights)
            n_iterations = trainer.train(writer, global_round)
            send_weight_diff(input_model.params, trainer.model, n_iterations)

        elif flare.is_evaluate():
            trainer.model.load_state_dict({k: v.to(trainer.device) for k, v in weights.items()}, strict=False)
            freeze_autoencoder(trainer.model)
            test_loader = trainer.val_loader(config["BATCH_SIZE"])
            val_loss = validate(
                trainer.model, test_loader, trainer.optimizers_dm["scheduler"], config, trainer.device, writer
            )
            logger.info(f"Validating the aggregated diffusion model on {flare.get_site_name()}'s data: {val_loss}")
            flare.send(flare.FLModel(metrics={"val_loss": val_loss}))

        else:
            logger.warning("Received unknown task; ignoring.")


if __name__ == "__main__":
    main()
