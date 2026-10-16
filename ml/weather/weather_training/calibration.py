"""Phép calibration dùng chung training/inference, không import training runtime."""

import numpy as np


def logit(probabilities):
    probabilities = np.clip(probabilities, 1e-6, 1 - 1e-6)
    return np.log(probabilities / (1 - probabilities))


def calibrate(probabilities, calibration):
    scores = np.clip(calibration["coefficient"] * logit(probabilities) + calibration["intercept"], -50, 50)
    return 1 / (1 + np.exp(-scores))
