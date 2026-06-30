"""Evaluation transforms for Ark+ X-ray classification.

This is a thin wrapper around data_utils.get_xray_transforms for backward
compatibility with executor imports.
"""

from data_utils import get_xray_transforms as get_eval_transforms
