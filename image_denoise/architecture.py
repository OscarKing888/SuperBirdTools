"""NAFNet-SIDD 推理架构；仅在载入模型时导入本模块。

Copyright (c) 2022 megvii-model. All Rights Reserved.
Layer normalization adapted from BasicSR, Copyright 2018-2020 BasicSR Authors.
Adapted from NAFNet commit 2b4af71ebe098a92a75910c233a3965a3e93ede4.
Changes: removed training/local-conversion dependencies, inference-only layer
normalization, immutable SIDD defaults and formatting. See NOTICE.txt and
THIRD_PARTY_LICENSE.txt for the complete upstream notices and licenses.
"""
from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F


class LayerNorm2d(nn.Module):
    """沿通道归一化，与官方权重的参数名称、形状和计算顺序一致。"""

    def __init__(self, channels: int, eps: float = 1e-6):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(channels))
        self.bias = nn.Parameter(torch.zeros(channels))
        self.eps = eps

    def forward(self, x):
        mean = x.mean(1, keepdim=True)
        variance = (x - mean).pow(2).mean(1, keepdim=True)
        normalized = (x - mean) / (variance + self.eps).sqrt()
        return self.weight.view(1, -1, 1, 1) * normalized + self.bias.view(1, -1, 1, 1)


class SimpleGate(nn.Module):
    def forward(self, x):
        first, second = x.chunk(2, dim=1)
        return first * second


class NAFBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        expanded = channels * 2
        self.conv1 = nn.Conv2d(channels, expanded, 1)
        self.conv2 = nn.Conv2d(expanded, expanded, 3, padding=1, groups=expanded)
        self.conv3 = nn.Conv2d(channels, channels, 1)
        self.sca = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Conv2d(channels, channels, 1))
        self.sg = SimpleGate()
        self.conv4 = nn.Conv2d(channels, expanded, 1)
        self.conv5 = nn.Conv2d(channels, channels, 1)
        self.norm1 = LayerNorm2d(channels)
        self.norm2 = LayerNorm2d(channels)
        self.dropout1 = nn.Identity()
        self.dropout2 = nn.Identity()
        self.beta = nn.Parameter(torch.zeros(1, channels, 1, 1))
        self.gamma = nn.Parameter(torch.zeros(1, channels, 1, 1))

    def forward(self, inp):
        x = self.sg(self.conv2(self.conv1(self.norm1(inp))))
        x = self.dropout1(self.conv3(x * self.sca(x)))
        residual = inp + x * self.beta
        x = self.conv5(self.sg(self.conv4(self.norm2(residual))))
        return residual + self.dropout2(x) * self.gamma


class NAFNet(nn.Module):
    """默认参数严格对应作者的 NAFNet-SIDD-width64 测试配置。"""

    def __init__(
        self, img_channel=3, width=64, middle_blk_num=12,
        enc_blk_nums=(2, 2, 4, 8), dec_blk_nums=(2, 2, 2, 2),
    ):
        super().__init__()
        self.intro = nn.Conv2d(img_channel, width, 3, padding=1)
        self.ending = nn.Conv2d(width, img_channel, 3, padding=1)
        self.encoders = nn.ModuleList()
        self.decoders = nn.ModuleList()
        self.downs = nn.ModuleList()
        self.ups = nn.ModuleList()
        channels = width
        for count in enc_blk_nums:
            self.encoders.append(nn.Sequential(*(NAFBlock(channels) for _ in range(count))))
            self.downs.append(nn.Conv2d(channels, channels * 2, 2, 2))
            channels *= 2
        self.middle_blks = nn.Sequential(*(NAFBlock(channels) for _ in range(middle_blk_num)))
        for count in dec_blk_nums:
            self.ups.append(nn.Sequential(
                nn.Conv2d(channels, channels * 2, 1, bias=False), nn.PixelShuffle(2),
            ))
            channels //= 2
            self.decoders.append(nn.Sequential(*(NAFBlock(channels) for _ in range(count))))
        self.padder_size = 2 ** len(self.encoders)

    def check_image_size(self, x):
        height, width = x.shape[-2:]
        return F.pad(x, (0, (-width) % self.padder_size, 0, (-height) % self.padder_size))

    def forward(self, inp):
        height, width = inp.shape[-2:]
        padded = self.check_image_size(inp)
        x = self.intro(padded)
        skips = []
        for encoder, down in zip(self.encoders, self.downs):
            x = encoder(x)
            skips.append(x)
            x = down(x)
        x = self.middle_blks(x)
        for decoder, up, skip in zip(self.decoders, self.ups, reversed(skips)):
            x = decoder(up(x) + skip)
        return (self.ending(x) + padded)[:, :, :height, :width]
