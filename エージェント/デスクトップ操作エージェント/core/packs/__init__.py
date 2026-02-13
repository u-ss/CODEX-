# -*- coding: utf-8 -*-
"""
Packs - アプリ固有プリセットパック

各アプリ（Excel, Outlook, freee, 弥生等）のlocator候補と回復手順を定義。
"""

from .contracts import AppPack, LocatorCandidate

__all__ = [
    "AppPack",
    "LocatorCandidate",
]
