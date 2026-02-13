"""
画像特徴抽出モジュール

画像からカラーヒストグラム、明るさ、彩度、コントラスト、
エッジ密度、アスペクト比などの特徴量を抽出する。
"""

import numpy as np
from PIL import Image, ImageStat, ImageFilter
from pathlib import Path
from typing import Union
import json


def extract_features(image_path: Union[str, Path]) -> dict:
    """画像から特徴量を抽出する。

    Args:
        image_path: 画像ファイルのパス

    Returns:
        特徴量の辞書
    """
    img = Image.open(image_path).convert("RGB")

    features = {}

    # 基本情報
    features["width"] = img.width
    features["height"] = img.height
    features["aspect_ratio"] = img.width / img.height

    # RGB統計
    stat = ImageStat.Stat(img)
    features["mean_r"] = stat.mean[0] / 255.0
    features["mean_g"] = stat.mean[1] / 255.0
    features["mean_b"] = stat.mean[2] / 255.0
    features["stddev_r"] = stat.stddev[0] / 255.0
    features["stddev_g"] = stat.stddev[1] / 255.0
    features["stddev_b"] = stat.stddev[2] / 255.0

    # HSV変換して色相・彩度・明度を分析
    hsv = img.convert("HSV")
    h_data = np.array(hsv.getchannel("H")).flatten()
    s_data = np.array(hsv.getchannel("S")).flatten()
    v_data = np.array(hsv.getchannel("V")).flatten()

    features["mean_hue"] = float(np.mean(h_data)) / 255.0
    features["mean_saturation"] = float(np.mean(s_data)) / 255.0
    features["mean_brightness"] = float(np.mean(v_data)) / 255.0
    features["stddev_hue"] = float(np.std(h_data)) / 255.0
    features["stddev_saturation"] = float(np.std(s_data)) / 255.0
    features["stddev_brightness"] = float(np.std(v_data)) / 255.0

    # 色相ヒストグラム（12ビン = 30度刻み）
    h_hist, _ = np.histogram(h_data, bins=12, range=(0, 256))
    h_hist_norm = h_hist / h_hist.sum() if h_hist.sum() > 0 else h_hist
    for i, val in enumerate(h_hist_norm):
        features[f"hue_bin_{i}"] = float(val)

    # 明るさヒストグラム（8ビン）
    v_hist, _ = np.histogram(v_data, bins=8, range=(0, 256))
    v_hist_norm = v_hist / v_hist.sum() if v_hist.sum() > 0 else v_hist
    for i, val in enumerate(v_hist_norm):
        features[f"brightness_bin_{i}"] = float(val)

    # コントラスト（明度の標準偏差）
    features["contrast"] = float(np.std(v_data)) / 255.0

    # エッジ密度（Sobelフィルタ）
    gray = img.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_data = np.array(edges).flatten()
    features["edge_density"] = float(np.mean(edge_data)) / 255.0
    features["edge_stddev"] = float(np.std(edge_data)) / 255.0

    # テクスチャ（ラプラシアンの分散 = シャープネス指標）
    laplacian = gray.filter(ImageFilter.Kernel(
        size=(3, 3),
        kernel=[-1, -1, -1, -1, 8, -1, -1, -1, -1],
        scale=1,
        offset=128
    ))
    lap_data = np.array(laplacian).flatten().astype(float)
    features["sharpness"] = float(np.var(lap_data)) / (255.0 ** 2)

    # カラフルさ（RGBチャンネルの標準偏差の平均）
    features["colorfulness"] = (
        features["stddev_r"] + features["stddev_g"] + features["stddev_b"]
    ) / 3.0

    # 暖色比率（色相 0-30 or 210-255 = 赤〜黄〜オレンジ系）
    warm_mask = (h_data < 30) | (h_data > 210)
    features["warm_ratio"] = float(np.sum(warm_mask)) / len(h_data)

    # 寒色比率（色相 90-170 = 青〜緑系）
    cool_mask = (h_data >= 90) & (h_data <= 170)
    features["cool_ratio"] = float(np.sum(cool_mask)) / len(h_data)

    # ドミナントカラー（最頻色相ビン）
    features["dominant_hue_bin"] = int(np.argmax(h_hist_norm))

    return features


def features_to_vector(features: dict) -> np.ndarray:
    """特徴量辞書を固定長ベクトルに変換する。

    Args:
        features: extract_features() の戻り値

    Returns:
        特徴量ベクトル（numpy配列）
    """
    # ベクトル化する特徴量のキーリスト（順序固定）
    keys = get_feature_keys()
    return np.array([features.get(k, 0.0) for k in keys], dtype=np.float64)


def get_feature_keys() -> list[str]:
    """特徴量ベクトルのキーリストを返す（順序固定）。"""
    keys = [
        "aspect_ratio",
        "mean_r", "mean_g", "mean_b",
        "stddev_r", "stddev_g", "stddev_b",
        "mean_hue", "mean_saturation", "mean_brightness",
        "stddev_hue", "stddev_saturation", "stddev_brightness",
    ]
    # 色相ヒストグラム 12ビン
    keys += [f"hue_bin_{i}" for i in range(12)]
    # 明るさヒストグラム 8ビン
    keys += [f"brightness_bin_{i}" for i in range(8)]
    keys += [
        "contrast",
        "edge_density", "edge_stddev",
        "sharpness",
        "colorfulness",
        "warm_ratio", "cool_ratio",
        "dominant_hue_bin",
    ]
    return keys
