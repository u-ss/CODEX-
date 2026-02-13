#!/usr/bin/env python3
"""PDCA-CHATGPT-DESKTOP-004 (R4) テスト: Dialogue Quality

受入基準:
1. 各ターンで unknowns が1つ以上明示される
2. 次質問は goal keyword を含む
3. 連続2ターンで unknowns が減らない場合、戦略切替
4. consult prompt markdown path が毎ターン保存される（既存実装済み）
"""
from __future__ import annotations

import sys
from pathlib import Path

# テスト対象モジュールへのパスを追加
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "エージェント" / "PDCAエージェント" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import chatgpt_desktop_dialogue as dialogue


# --- Test: _get_recent_unknowns_counts ---

def test_get_recent_unknowns_counts_empty():
    """historyが空の場合は空リストを返す"""
    result = dialogue._get_recent_unknowns_counts([], n=2)
    assert result == [], f"Expected [], got {result}"


def test_get_recent_unknowns_counts_single():
    """1エントリの場合は1要素リスト"""
    history = [{"turn": 1, "unknowns_count": 3}]
    result = dialogue._get_recent_unknowns_counts(history, n=2)
    assert result == [3], f"Expected [3], got {result}"


def test_get_recent_unknowns_counts_multiple():
    """複数エントリから直近nを取得"""
    history = [
        {"turn": 1, "unknowns_count": 5},
        {"turn": 2, "unknowns_count": 3},
        {"turn": 3, "unknowns_count": 3},
    ]
    result = dialogue._get_recent_unknowns_counts(history, n=2)
    assert result == [3, 3], f"Expected [3, 3], got {result}"


def test_get_recent_unknowns_counts_ignores_non_dict():
    """dictでないエントリはスキップ"""
    history = [
        {"turn": 1, "unknowns_count": 5},
        "not_a_dict",
        {"turn": 3, "unknowns_count": 2},
    ]
    result = dialogue._get_recent_unknowns_counts(history, n=2)
    assert result == [5, 2], f"Expected [5, 2], got {result}"


# --- Test: strategy_for_turn (適応型) ---

def test_strategy_for_turn_normal_rotation():
    """停滞なしの場合は通常ローテーション"""
    assert dialogue.strategy_for_turn(1) == "direct"
    assert dialogue.strategy_for_turn(2) == "example"
    assert dialogue.strategy_for_turn(3) == "compare"
    assert dialogue.strategy_for_turn(4) == "hypothesis"
    assert dialogue.strategy_for_turn(5) == "deep_dive"
    # 範囲外は最後の戦略を使用
    assert dialogue.strategy_for_turn(10) == "deep_dive"


def test_strategy_for_turn_stagnation_override():
    """unknownsが停滞している場合、次の戦略に切替"""
    # Turn 2で停滞 → "example" から "compare" に切替
    stagnant_history = [
        {"turn": 1, "unknowns_count": 3},
        {"turn": 2, "unknowns_count": 3},  # 減少なし
    ]
    result = dialogue.strategy_for_turn(2, history=stagnant_history)
    assert result != "example", f"停滞時に戦略切替が発生しなかった: {result}"
    assert result == "compare", f"Expected 'compare', got {result}"


def test_strategy_for_turn_no_override_when_decreasing():
    """unknownsが減少している場合は通常ローテーション"""
    decreasing_history = [
        {"turn": 1, "unknowns_count": 5},
        {"turn": 2, "unknowns_count": 3},  # 減少あり
    ]
    result = dialogue.strategy_for_turn(2, history=decreasing_history)
    assert result == "example", f"Expected 'example', got {result}"


def test_strategy_for_turn_stagnation_increase():
    """unknownsが増加している場合も戦略切替"""
    increasing_history = [
        {"turn": 1, "unknowns_count": 2},
        {"turn": 2, "unknowns_count": 4},  # 増加
    ]
    result = dialogue.strategy_for_turn(2, history=increasing_history)
    assert result == "compare", f"Expected 'compare', got {result}"


# --- Test: next_question にgoal keywordが含まれる ---

def test_next_question_contains_goal():
    """生成された質問にgoal文字列が含まれることを検証"""
    goal = "Antigravityログ基盤の改善"
    question, strategy = dialogue.next_question(goal, [], 1)
    assert "Antigravityログ基盤の改善" in question or "Goal" in question, \
        f"質問にgoalが含まれていない: {question}"


def test_next_question_with_unknowns_uses_first():
    """unknownsがある場合、最初のunknownを使って質問を生成"""
    goal = "テスト目標"
    history = [
        {"unknowns": ["スキーマ設計", "KI連携方式"]}
    ]
    question, strategy = dialogue.next_question(goal, history, 1)
    assert "スキーマ設計" in question, f"最初のunknownが質問に含まれていない: {question}"


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
