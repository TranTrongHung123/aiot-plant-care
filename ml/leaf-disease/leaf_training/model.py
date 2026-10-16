"""Kiến trúc và preprocessing dùng chung training/inference."""

import torch
from PIL import Image, ImageOps
from torchvision import transforms
from torchvision.models import MobileNet_V3_Large_Weights, mobilenet_v3_large

PREPROCESSING = {
    "color_mode": "RGB", "exif_transpose": True, "resize_shorter": 232,
    "crop_size": 224, "interpolation": "bilinear", "antialias": True,
    "scale": 1 / 255, "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
}


def build_model(pretrained=True):
    weights = MobileNet_V3_Large_Weights.IMAGENET1K_V2 if pretrained else None
    model = mobilenet_v3_large(weights=weights)
    model.classifier[-1] = torch.nn.Linear(model.classifier[-1].in_features, 10)
    return model


def make_transform(training=False, config=None):
    cfg = config or PREPROCESSING
    if cfg != PREPROCESSING:
        raise ValueError("Preprocessing chưa được hỗ trợ bởi phiên bản code này")
    steps = ([transforms.RandomResizedCrop(cfg["crop_size"], scale=(0.8, 1.0), antialias=True),
              transforms.RandomHorizontalFlip(), transforms.RandomVerticalFlip(),
              transforms.RandomRotation(15), transforms.ColorJitter(0.1, 0.1, 0.1)] if training else
             [transforms.Resize(cfg["resize_shorter"], antialias=True),
              transforms.CenterCrop(cfg["crop_size"])])
    return transforms.Compose([*steps, transforms.ToTensor(),
                               transforms.Normalize(cfg["mean"], cfg["std"])])


def open_rgb(path):
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")
