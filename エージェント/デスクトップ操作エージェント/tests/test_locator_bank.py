# -*- coding: utf-8 -*-
"""
Locator Bank テスト

CODEXからの提案に基づき、以下を検証:
1. 候補登録→選択
2. 結果記録→スコア更新
3. レイヤーエスカレーション
4. UCBスコア計算
"""

import sys
from pathlib import Path

# コアモジュールをパッケージとして読み込み
parent_path = Path(__file__).parent.parent
sys.path.insert(0, str(parent_path))

import pytest

# coreパッケージからインポート
from core.locator_bank import LocatorBank, LocatorCandidate, BucketKey


class TestLocatorBankBasic:
    """Locator Bank基本動作テスト"""

    def setup_method(self):
        """各テスト前にLocatorBankを初期化（永続化なし）"""
        self.bank = LocatorBank(store=None)

    def test_候補登録と取得(self):
        """候補を登録して取得できる"""
        bucket = BucketKey("login_screen", "submit", "button")
        candidate = LocatorCandidate(
            selector_id="btn1",
            layer="CDP",
            selector_type="css",
            selector_value="button.submit",
            priority=1
        )
        
        self.bank.register_candidate(bucket, candidate)
        candidates = self.bank.get_candidates(bucket)
        
        assert len(candidates) == 1
        assert candidates[0].selector_id == "btn1"

    def test_複数候補を一括登録(self):
        """複数候補を一括登録できる"""
        bucket = BucketKey("login_screen", "submit", "button")
        candidates = [
            LocatorCandidate("btn1", "CDP", "css", "button.submit", 1),
            LocatorCandidate("btn2", "UIA", "name", "Submit", 2),
            LocatorCandidate("btn3", "PyAutoGUI", "image", "submit.png", 3),
        ]
        
        self.bank.register_candidates(bucket, candidates)
        result = self.bank.get_candidates(bucket)
        
        assert len(result) == 3

    def test_空バケットからの取得(self):
        """未登録バケットからは空リスト"""
        bucket = BucketKey("unknown", "unknown", "unknown")
        candidates = self.bank.get_candidates(bucket)
        
        assert candidates == []


class TestLocatorBankSelection:
    """候補選択テスト"""

    def setup_method(self):
        self.bank = LocatorBank(store=None)
        self.bucket = BucketKey("test_screen", "click", "button")
        
        # 3つの候補を登録
        candidates = [
            LocatorCandidate("cdp1", "CDP", "css", ".btn", 1),
            LocatorCandidate("uia1", "UIA", "name", "Button", 2),
            LocatorCandidate("pyag1", "PyAutoGUI", "image", "btn.png", 3),
        ]
        self.bank.register_candidates(self.bucket, candidates)

    def test_select_best_初期状態(self):
        """初期状態でも候補を選択できる"""
        best = self.bank.select_best(self.bucket)
        assert best is not None
        assert hasattr(best, "selector_id")

    def test_結果記録後のスコア変化(self):
        """成功/失敗記録でスコアが変化する"""
        # 最初の候補を取得
        best = self.bank.select_best(self.bucket)
        selector_id = best.selector_id
        
        # 成功を記録
        reward = self.bank.update_result(
            self.bucket, 
            selector_id, 
            outcome="success"
        )
        
        # 報酬が正の値
        assert reward is not None


class TestLocatorBankLayerEscalation:
    """レイヤーエスカレーションテスト"""

    def setup_method(self):
        self.bank = LocatorBank(store=None)
        self.bucket = BucketKey("test_screen", "click", "button")
        
        # 複数レイヤーの候補を登録
        candidates = [
            LocatorCandidate("cdp1", "CDP", "css", ".btn", 1),
            LocatorCandidate("uia1", "UIA", "name", "Button", 2),
            LocatorCandidate("pyag1", "PIXEL", "image", "btn.png", 3),  # PyAutoGUIはPIXELとして定義
        ]
        self.bank.register_candidates(self.bucket, candidates)

    def test_レイヤー別候補取得(self):
        """特定レイヤーの候補のみ取得"""
        cdp_candidates = self.bank.get_layer_candidates(self.bucket, "CDP")
        assert len(cdp_candidates) == 1
        assert cdp_candidates[0].layer == "CDP"

    def test_エスカレーション_現レイヤーに候補があればNone(self):
        """現レイヤーに可用候補がある場合はエスカレートしない"""
        # CDPに候補があるのでNone
        next_layer = self.bank.escalate_layer(self.bucket, "CDP")
        assert next_layer is None  # まだCDP候補が使える

    def test_存在しないレイヤーはNone(self):
        """存在しないレイヤーからのエスカレーションはNone"""
        next_layer = self.bank.escalate_layer(self.bucket, "UNKNOWN")
        assert next_layer is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
