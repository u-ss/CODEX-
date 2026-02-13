# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - Image Hash
ROIハッシュ差分（dHash/pHash）
"""

from dataclasses import dataclass
from typing import Optional, Tuple
import hashlib


@dataclass(frozen=True)
class ROI:
    """関心領域（Region of Interest）"""
    name: str
    x: int
    y: int
    w: int
    h: int
    
    def as_tuple(self) -> Tuple[int, int, int, int]:
        """(x, y, w, h) タプル"""
        return (self.x, self.y, self.w, self.h)


@dataclass(frozen=True)
class ImageHashes:
    """画像ハッシュ"""
    dhash: Optional[str] = None
    phash: Optional[str] = None


def compute_dhash_simple(pixels: list[int], width: int = 9, height: int = 8) -> str:
    """
    dHash（差分ハッシュ）の簡易実装
    
    Args:
        pixels: グレースケールピクセル値（width x height）
        width: 幅（9推奨）
        height: 高さ（8推奨）
    
    Returns:
        64ビットハッシュ（16文字hex）
    """
    if len(pixels) < width * height:
        return ""
    
    hash_bits = []
    for y in range(height):
        for x in range(width - 1):
            idx = y * width + x
            left = pixels[idx]
            right = pixels[idx + 1]
            hash_bits.append('1' if left > right else '0')
    
    # 64ビット → 16文字hex
    hash_int = int(''.join(hash_bits), 2)
    return format(hash_int, '016x')


def compute_simple_hash(data: bytes) -> str:
    """簡易ハッシュ（MD5の先頭16文字）"""
    return hashlib.md5(data).hexdigest()[:16]


def diff_hash(h1: str, h2: str) -> int:
    """
    ハミング距離
    
    Returns:
        異なるビット数（0=同一）
    """
    if not h1 or not h2:
        return 64  # 最大距離
    
    if len(h1) != len(h2):
        return 64
    
    try:
        v1 = int(h1, 16)
        v2 = int(h2, 16)
        return bin(v1 ^ v2).count('1')
    except ValueError:
        return 64


def is_similar(h1: str, h2: str, threshold: int = 12) -> bool:
    """類似判定"""
    return diff_hash(h1, h2) <= threshold


@dataclass(frozen=True)
class ROIHashResult:
    """ROIハッシュ結果"""
    roi_name: str
    hashes: ImageHashes
    changed: bool = False
    distance: int = 0


def compare_roi_hashes(
    prev: dict[str, ImageHashes],
    cur: dict[str, ImageHashes],
    threshold: int = 12,
) -> dict[str, ROIHashResult]:
    """
    ROIハッシュ比較
    
    Returns:
        ROI名 → 比較結果
    """
    results = {}
    
    for name, cur_hash in cur.items():
        prev_hash = prev.get(name)
        
        if prev_hash is None:
            # 新規ROI
            results[name] = ROIHashResult(
                roi_name=name,
                hashes=cur_hash,
                changed=True,
                distance=64,
            )
        else:
            # 比較
            distance = diff_hash(
                prev_hash.dhash or "",
                cur_hash.dhash or ""
            )
            results[name] = ROIHashResult(
                roi_name=name,
                hashes=cur_hash,
                changed=distance > threshold,
                distance=distance,
            )
    
    return results


def has_any_change(results: dict[str, ROIHashResult]) -> bool:
    """いずれかのROIが変化したか"""
    return any(r.changed for r in results.values())


def all_stable(results: dict[str, ROIHashResult]) -> bool:
    """全ROIが安定しているか"""
    return all(not r.changed for r in results.values())
