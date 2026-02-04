# Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
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


import os

import sys
sys.path.append('.')

import os
import pandas as pd
from flnode.pipeline.opener import Opener
import nibabel as nib
import numpy as np
import matplotlib.pyplot as plt
import torch
from pathlib import Path
from sklearn.model_selection import train_test_split

class MonaiOpener(Opener):
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.image_dir = self.data_dir / 'images'
        self.label_dir = self.data_dir / 'labels'


    def get_image_and_label_list(self):
        # assumes image and label pairs are stored with identical filnames in data_dir/images and data_dir/labels
        image_files = list(self.image_dir.glob('*.nii*'))
        self.image_and_label_files = []
        self.num_unpaired = 0
        for im in image_files:
            label_file = self.label_dir / im.name
            if label_file.exists():
                self.image_and_label_files.append(
                    {'img': str(im),
                     'seg': str(label_file)})
            else:
                self.num_unpaired += 1
        self.num_total = len(self.image_and_label_files)

    def data_summary(self, logger):
        if not hasattr(self, 'image_and_label_files'):
            self.get_image_and_label_list()

        # get size of each image
        image_sizes = [nib.load(f['img']).shape for f in self.image_and_label_files]


        logger.info(f"Total paired image  and labels: {self.num_total}")
        logger.info(f"Total images with no label found: {self.num_unpaired}")
        mean_size = np.array(image_sizes).mean(axis=0).astype(np.int16)
        logger.info(f"Mean image size: {mean_size}")

        # # uncomment to take a quick peek at the data
        # num_to_plot=4
        # plt.subplots(2, num_to_plot, figsize=(8, 8))
        # for i, k in enumerate(np.random.randint(num_total, size=num_to_plot)):
        #     im = nib.load(self.image_and_label_files[k]['img']).get_fdata()
        #     seg = nib.load(self.image_and_label_files[k]['seg']).get_fdata()
        #     plt.subplot(2, num_to_plot, i +1 )
        #     if im.ndim == 3:
        #         data_slice = np.s_[:,:,im.shape[2]//2]
        #     elif im.ndim == 4:
        #         data_slice = np.s_[:,:,im.shape[2]//2, 0]
        #     plt.imshow(im[data_slice], cmap="gray", vmin=-15, vmax=100)
        #     plt.subplot(2, num_to_plot, i + num_to_plot + 1 )
        #     plt.imshow(seg[data_slice], cmap="gray")
        # plt.tight_layout()
        # plt.show()

    def get_x_y(self, frac_val, frac_test):
        if not hasattr(self, 'image_and_label_files'):
            self.get_image_and_label_list()

        random_state = 0
        train, val_and_test = train_test_split(self.image_and_label_files, train_size=1 - frac_val - frac_test, random_state=random_state)
        val, test = train_test_split(val_and_test, train_size=frac_val/ (frac_val+frac_test), random_state=random_state)

        return (train, val, test)

    def save_predictions(self, y_pred, path):
        with open(path, 'w') as fp:
            y_pred.to_csv(fp, index=False)

    def get_predictions(self, path):
        return pd.read_csv(path)

    def fake_X(self, n_samples):
        return []  # compute random fake data

    def fake_y(self, n_samples):
        return []  # compute random fake data

    def get_X(self, folders):
        return [
            # print(folders)
            folders
        ]

    def get_y(self, folders):
        return [
            folders
            # print(folders)
            # pd.read_csv(folders)#os.path.join(folders, 'y.csv'))
            # for folder in folders
        ]
