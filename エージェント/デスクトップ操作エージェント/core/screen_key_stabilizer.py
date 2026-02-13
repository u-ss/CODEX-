"""
screen_key Stabilizer（近傍マッチ + クラスタリング）

目的: 画面識別の揺れを吸収し、キャッシュと学習を効かせる

機能:
- 近いscreen_keyを同一扱いにする距離関数
- モーダル/通知/ツールチップを「ノイズ」として分離
- 複合シグナル化（app_id + window_class + ui_tree_signature + url + modal_flag）

ChatGPT 5.2フィードバック（2026-02-05）より
"""

import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher


@dataclass
class ScreenKeyComponents:
    """screen_keyの構成要素"""
    
    app_id: str                      # プロセス名（例: chrome.exe）
    window_class: str                # ウィンドウクラス
    window_title: Optional[str] = None
    url_path: Optional[str] = None   # URL正規化（ドメイン + パス）
    ui_tree_signature: Optional[str] = None  # UIツリーのハッシュ
    modal_flag: bool = False         # モーダル表示中か
    dpi_bucket: int = 100            # DPIバケット（96=100, 120=125等）
    monitor_id: int = 0              # モニターID
    
    # ノイズフラグ
    has_tooltip: bool = False
    has_notification: bool = False
    
    def to_composite_key(self) -> str:
        """複合キーを生成"""
        parts = [
            self.app_id,
            self.window_class,
            self.url_path or "",
            "modal" if self.modal_flag else "",
        ]
        return "|".join(filter(None, parts))
    
    def to_coarse_key(self) -> str:
        """粗いキー（アプリ+クラス）"""
        return f"{self.app_id}|{self.window_class}"
    
    def to_mid_key(self) -> str:
        """中間キー（アプリ+クラス+URL）"""
        if self.url_path:
            return f"{self.app_id}|{self.window_class}|{self.url_path}"
        return self.to_coarse_key()
    
    def to_fine_key(self) -> str:
        """細かいキー（全要素）"""
        parts = [
            self.app_id,
            self.window_class,
            self.url_path or "",
            self.ui_tree_signature or "",
            f"m{self.modal_flag}",
            f"dpi{self.dpi_bucket}",
        ]
        return "|".join(parts)


class ScreenKeyStabilizer:
    """screen_key安定化"""
    
    def __init__(self, similarity_threshold: float = 0.8):
        self.similarity_threshold = similarity_threshold
        self.cluster_cache: dict[str, list[str]] = {}  # 代表キー -> 同一クラスタのキー
        self.key_to_cluster: dict[str, str] = {}       # キー -> 代表キー
        
        # ノイズパターン（除外）
        self.noise_patterns = [
            r"tooltip",
            r"popup",
            r"notification",
            r"toast",
            r"ツールチップ",
            r"通知",
        ]
    
    def normalize_url(self, url: str) -> str:
        """URLを正規化"""
        if not url:
            return ""
        
        # クエリパラメータと動的パスを除去
        # 例: https://chatgpt.com/c/abc123-def456 → chatgpt.com/c/*
        
        # プロトコル除去
        url = re.sub(r"^https?://", "", url)
        
        # クエリパラメータ除去
        url = url.split("?")[0]
        
        # UUID/ランダムIDを*に置換
        url = re.sub(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", "*", url)
        url = re.sub(r"/[a-f0-9]{20,}/", "/*/", url)
        url = re.sub(r"/\d+(/|$)", "/*/", url)
        
        return url
    
    def build_key(
        self,
        app_id: str,
        window_class: str,
        window_title: Optional[str] = None,
        url: Optional[str] = None,
        ui_tree_hash: Optional[str] = None,
        is_modal: bool = False,
        dpi: int = 96,
        monitor: int = 0
    ) -> ScreenKeyComponents:
        """screen_keyコンポーネントを構築"""
        
        # DPIをバケット化
        dpi_bucket = round(dpi / 24) * 25  # 96→100, 120→125, 144→150
        
        # ノイズ検出
        has_tooltip = False
        has_notification = False
        
        if window_title:
            title_lower = window_title.lower()
            for pattern in self.noise_patterns:
                if re.search(pattern, title_lower, re.IGNORECASE):
                    if "tooltip" in pattern or "ツールチップ" in pattern:
                        has_tooltip = True
                    else:
                        has_notification = True
        
        return ScreenKeyComponents(
            app_id=app_id.lower(),
            window_class=window_class,
            window_title=window_title,
            url_path=self.normalize_url(url) if url else None,
            ui_tree_signature=ui_tree_hash[:8] if ui_tree_hash else None,
            modal_flag=is_modal,
            dpi_bucket=dpi_bucket,
            monitor_id=monitor,
            has_tooltip=has_tooltip,
            has_notification=has_notification,
        )
    
    def similarity(self, key1: str, key2: str) -> float:
        """2つのキーの類似度を計算"""
        return SequenceMatcher(None, key1, key2).ratio()
    
    def find_cluster(self, key: str) -> Optional[str]:
        """キーが属するクラスタの代表キーを探す"""
        
        # 完全一致
        if key in self.key_to_cluster:
            return self.key_to_cluster[key]
        
        # 近傍マッチ
        for representative, members in self.cluster_cache.items():
            if self.similarity(key, representative) >= self.similarity_threshold:
                return representative
        
        return None
    
    def register_key(self, key: str) -> str:
        """キーを登録し、代表キーを返す"""
        
        # 既存クラスタを探す
        cluster = self.find_cluster(key)
        
        if cluster:
            # 既存クラスタに追加
            if key not in self.cluster_cache[cluster]:
                self.cluster_cache[cluster].append(key)
            self.key_to_cluster[key] = cluster
            return cluster
        else:
            # 新規クラスタ
            self.cluster_cache[key] = [key]
            self.key_to_cluster[key] = key
            return key
    
    def get_stable_key(self, components: ScreenKeyComponents) -> str:
        """安定したキーを取得"""
        
        # ノイズを除外した状態でキー生成
        if components.has_tooltip or components.has_notification:
            # ノイズがある場合は粗いキーを使用
            raw_key = components.to_coarse_key()
        else:
            raw_key = components.to_mid_key()
        
        # クラスタ登録して代表キーを返す
        return self.register_key(raw_key)
    
    def is_same_screen(
        self, 
        comp1: ScreenKeyComponents, 
        comp2: ScreenKeyComponents
    ) -> bool:
        """2つの画面が同じか判定"""
        
        # モーダル状態が違えば別画面
        if comp1.modal_flag != comp2.modal_flag:
            return False
        
        # アプリが違えば別画面
        if comp1.app_id != comp2.app_id:
            return False
        
        # 安定キーで比較
        key1 = self.get_stable_key(comp1)
        key2 = self.get_stable_key(comp2)
        
        # 同じクラスタか
        cluster1 = self.find_cluster(key1)
        cluster2 = self.find_cluster(key2)
        
        return cluster1 == cluster2
    
    def get_cluster_stats(self) -> dict:
        """クラスタ統計"""
        return {
            "total_clusters": len(self.cluster_cache),
            "total_keys": len(self.key_to_cluster),
            "clusters": {k: len(v) for k, v in self.cluster_cache.items()}
        }


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("screen_key Stabilizer テスト")
    print("=" * 60)
    
    stabilizer = ScreenKeyStabilizer(similarity_threshold=0.8)
    
    # テストケース1: 同じページの異なるセッションID
    print("\n--- ケース1: ChatGPT異なるセッション ---")
    comp1 = stabilizer.build_key(
        app_id="brave.exe",
        window_class="Chrome_WidgetWin_1",
        url="https://chatgpt.com/c/abc12345-def6-7890-ghij-klmnopqrstuv"
    )
    comp2 = stabilizer.build_key(
        app_id="brave.exe",
        window_class="Chrome_WidgetWin_1",
        url="https://chatgpt.com/c/xyz98765-uvw4-3210-abcd-efghijklmnop"
    )
    
    key1 = stabilizer.get_stable_key(comp1)
    key2 = stabilizer.get_stable_key(comp2)
    print(f"Key1: {key1}")
    print(f"Key2: {key2}")
    print(f"同じ画面: {stabilizer.is_same_screen(comp1, comp2)}")
    
    # テストケース2: 異なるページ
    print("\n--- ケース2: 異なるサイト ---")
    comp3 = stabilizer.build_key(
        app_id="brave.exe",
        window_class="Chrome_WidgetWin_1",
        url="https://google.com/search?q=test"
    )
    key3 = stabilizer.get_stable_key(comp3)
    print(f"Key3: {key3}")
    print(f"comp1と同じ: {stabilizer.is_same_screen(comp1, comp3)}")
    
    # テストケース3: モーダル表示
    print("\n--- ケース3: モーダル表示 ---")
    comp4 = stabilizer.build_key(
        app_id="brave.exe",
        window_class="Chrome_WidgetWin_1",
        url="https://chatgpt.com/c/abc123",
        is_modal=True
    )
    key4 = stabilizer.get_stable_key(comp4)
    print(f"Key4 (modal): {key4}")
    print(f"comp1と同じ: {stabilizer.is_same_screen(comp1, comp4)}")
    
    # テストケース4: ツールチップ（ノイズ）
    print("\n--- ケース4: ツールチップ表示 ---")
    comp5 = stabilizer.build_key(
        app_id="brave.exe",
        window_class="Chrome_WidgetWin_1",
        url="https://chatgpt.com/c/abc123",
        window_title="ChatGPT - tooltip"
    )
    key5 = stabilizer.get_stable_key(comp5)
    print(f"Key5 (tooltip): {key5}")
    print(f"ノイズフラグ: tooltip={comp5.has_tooltip}")
    
    # 統計
    print("\n--- クラスタ統計 ---")
    stats = stabilizer.get_cluster_stats()
    print(f"クラスタ数: {stats['total_clusters']}")
    print(f"キー数: {stats['total_keys']}")
    for cluster, count in stats['clusters'].items():
        print(f"  {cluster}: {count}件")
    
    print("\n" + "=" * 60)
    print("テスト完了")
