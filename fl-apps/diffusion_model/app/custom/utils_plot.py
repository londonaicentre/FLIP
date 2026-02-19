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

import torch

from flip.constants import FlipConstants

if FlipConstants.LOCAL_DEV:
    # Matplotlib not available in production
    import matplotlib.pyplot as plt

    def plot_ae_images(images: torch.Tensor, save_path: str):
        def plot_row(axes, images, is2d, batch_id, vmin, vmax):
            if is2d:
                axes[0].imshow(images[0, batch_id, 0, ...], cmap="grey")  # gt
                axes[0].set_title("Ground Truth / Reconstruction", color="cyan")
                axes[1].imshow(images[1, batch_id, 0, ...], cmap="grey")  # rec
            else:
                axes[0].imshow(
                    images[0, batch_id, 0, ..., images[0, batch_id, 0, ...].shape[-1] // 2],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # rec/axial
                axes[1].imshow(
                    images[1, batch_id, 0, ..., images[1, batch_id, 0, ...].shape[-1] // 2],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # rec/axial
                axes[2].imshow(
                    images[0, batch_id, 0, :, images[0, batch_id, 0, ...].shape[-2] // 2, :],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # gt/coronal
                axes[3].imshow(
                    images[1, batch_id, 0, :, images[1, batch_id, 0, ...].shape[-2] // 2, :],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # rec/coronal
                axes[4].imshow(
                    images[0, batch_id, 0, images[0, batch_id, 0, ...].shape[-3] // 2, ...],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # gt/sagittal
                axes[5].imshow(
                    images[1, batch_id, 0, images[1, batch_id, 0, ...].shape[-3] // 2, ...],
                    cmap="grey",
                    vmin=vmin,
                    vmax=vmax,
                )  # rec/sagittal
            return axes

        v_max = images.max()
        v_min = images.min()
        n_rows = images.shape[1]
        n_cols = 6 if len(images.shape) == 6 else 2
        is2d = len(images.shape) == 5
        plt.style.use("dark_background")
        fig, ax = plt.subplots(ncols=n_cols, nrows=n_rows, figsize=(n_cols * 2, n_rows * 3))
        for _ax in ax.flatten():
            _ax.set_axis_off()
        for _ax in ax.flatten():
            _ax.set_facecolor("black")

        for row_id in range(n_rows):
            if n_rows == 1:
                ax = plot_row(ax, images, is2d, row_id, v_min, v_max)
            else:
                ax[row_id, :] = plot_row(ax[row_id, :], images, is2d, row_id, v_min, v_max)

        plt.tight_layout()
        plt.savefig(save_path)
        plt.close("all")

    def plot_dm_images(images: torch.Tensor, save_path: str):
        def plot_row(axes, images, is2d, batch_id, vmin, vmax):
            if is2d:
                axes[0].imshow(images[batch_id, ...], cmap="grey")  # gt
                axes[0].set_title("Sample", color="cyan")
            else:
                axes[0].imshow(
                    images[batch_id, ..., images[batch_id, ...].shape[-1] // 2], cmap="grey", vmin=vmin, vmax=vmax
                )  # axial
                axes[1].imshow(
                    images[batch_id, :, images[batch_id, ...].shape[-2] // 2, :], cmap="grey", vmin=vmin, vmax=vmax
                )  # coronal
                axes[2].imshow(
                    images[batch_id, images[batch_id, ...].shape[-3] // 2, ...], cmap="grey", vmin=vmin, vmax=vmax
                )  # sagittal
            return axes

        v_max = images.max()
        v_min = images.min()
        n_rows = images.shape[0]
        n_cols = 3 if len(images.shape) == 5 else 1
        is2d = len(images.shape) == 3
        plt.style.use("dark_background")
        fig, ax = plt.subplots(ncols=n_cols, nrows=n_rows, figsize=(n_cols * 3.3, n_rows * 2))
        for _ax in ax.flatten():
            _ax.set_axis_off()
        for _ax in ax.flatten():
            _ax.set_facecolor("black")
        if n_cols == 1:
            ax[0].imshow(images[0, 0, ...], cmap="grey", vmin=v_min, vmax=v_max)
            ax[0].set_title("Sample", color="cyan")
        else:
            for row_id in range(n_rows):
                if n_rows == 1:
                    ax = plot_row(ax, images[:, 0, ...], is2d, row_id, v_min, v_max)
                else:
                    ax[row_id, :] = plot_row(ax[row_id, :], images[:, 0, ...], is2d, row_id, v_min, v_max)

        plt.tight_layout()
        plt.savefig(save_path)
        plt.close("all")
