"""
理由テキスト保存・管理モジュール

画像ごとの「好きな理由」「嫌いな理由」をJSON形式で永続化する。
"""

import json
from pathlib import Path
from typing import Optional


class ReasonStore:
    """画像ごとの理由テキストを管理するクラス。"""

    def __init__(self, storage_path: str | Path = "training_data/reasons.json"):
        self.storage_path = Path(storage_path)
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    def _load(self):
        """JSONファイルから理由データを読み込む。"""
        if self.storage_path.exists():
            try:
                with open(self.storage_path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        """理由データをJSONファイルに保存する。"""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.storage_path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def save_reason(self, category: str, filename: str, reason: str):
        """理由を保存する。

        Args:
            category: カテゴリ名（好き/そうでもない/嫌い）
            filename: 画像ファイル名
            reason: 理由テキスト
        """
        if category not in self._data:
            self._data[category] = {}
        self._data[category][filename] = reason.strip()
        self._save()

    def get_reason(self, category: str, filename: str) -> Optional[str]:
        """理由を取得する。"""
        return self._data.get(category, {}).get(filename)

    def get_all_reasons(self) -> dict[str, dict[str, str]]:
        """全理由を取得する。"""
        return dict(self._data)

    def delete_reason(self, category: str, filename: str) -> bool:
        """理由を削除する。"""
        if category in self._data and filename in self._data[category]:
            del self._data[category][filename]
            if not self._data[category]:
                del self._data[category]
            self._save()
            return True
        return False

    def get_reasons_by_category(self, category: str) -> dict[str, str]:
        """カテゴリ別の理由を取得する。"""
        return dict(self._data.get(category, {}))

    def get_reason_texts(self) -> dict[str, list[str]]:
        """カテゴリ別の理由テキストリストを返す（言語化分析用）。"""
        result = {}
        for category, reasons in self._data.items():
            texts = [text for text in reasons.values() if text.strip()]
            if texts:
                result[category] = texts
        return result

    def clear_all(self):
        """全理由をクリアする。"""
        self._data = {}
        self._save()
