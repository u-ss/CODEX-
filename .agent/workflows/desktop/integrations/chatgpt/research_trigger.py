# -*- coding: utf-8 -*-
"""
Research Trigger v1.0.0

ChatGPT対話中にWEB検索が必要かを判断するロジック。
Phase 1: Hard Gate辞書（即検索/即非検索）

使用方法:
    from integrations.chatgpt.research_trigger import ResearchTrigger
    trigger = ResearchTrigger()
    should_search, reason, details = trigger.evaluate(user_query, assistant_response)
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime


@dataclass
class TriggerResult:
    """判定結果"""
    should_search: bool
    reason: str
    gate_type: str  # "hard_on", "hard_off", "soft", "none"
    score: float = 0.0
    matched_patterns: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class ResearchTrigger:
    """
    WEB検索トリガー判定
    
    Phase 1: Hard Gate（キーワード辞書）
    - 即検索: 時間依存 + 固有名詞、高リスク領域
    - 即非検索: 設計/創作系、ユーザー明示拒否
    """
    
    # === Hard Gate: 即検索（ON）===
    
    # 時間依存キーワード（鮮度重要）
    FRESHNESS_PATTERNS = [
        r"最新|直近|最近|いま|今|現在|本日|今日|今週|今月",
        r"昨夜|昨日|明日|さっき|たった今",
        r"\b(latest|recent|currently|as of|today|yesterday|tomorrow|this week|now)\b",
    ]
    
    # 変更・障害・価格・規約（変動が前提）
    CHANGE_PATTERNS = [
        r"アップデート|リリース|変更|更新|廃止|移行|終了",
        r"障害|ダウン|復旧|不具合|バグ|緊急",
        r"価格|料金|課金|見積|コスト|値段|無料|有料",
        r"規約|利用規約|ポリシー|ガイドライン|SLA",
        r"(update|release|changelog|breaking change|incident|outage|pricing|policy|terms)",
    ]
    
    # 高リスク領域（原則一次ソース確認）
    RISK_PATTERNS = [
        r"法律|法令|規制|条例|判例|弁護士|違法|適法|免許|届出",
        r"税務|確定申告|控除|納税",
        r"医療|診断|治療|薬|副作用|用量",
        r"投資|株価|為替|仮想通貨|利回り",
        r"脆弱性|CVE|ゼロデイ|exploit",
        r"(security advisory|vulnerability|cve-\d+)",
    ]
    
    # 固有名詞・バージョン・URL（具体的対象）
    ENTITY_PATTERNS = [
        r"https?://\S+",  # URL
        r"\bv?\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?\b",  # v1.2.3
        r"\b(RFC|ISO|IEC|JIS|CVE|NIST)-?\d{1,4}(?:-\d{3,6})?\b",  # RFC/ISO/CVE
        r"#\d+\b",  # Issue番号
        r"\b(issue|pr|pull request)\s*#?\d+\b",  # Issue/PR
    ]
    
    # === Hard Gate: 即非検索（OFF）===
    
    # クリエイティブ/設計/文章生成
    CREATIVE_PATTERNS = [
        r"設計|アーキテクチャ|構成|方針|パターン|ベストプラクティス",
        r"抽象化|要件定義|PRD|ADR",
        r"アイデア|案出し|ブレスト|ネーミング",
        r"文章|校正|言い換え|要約|翻訳",
        r"サンプルコード|雛形|テンプレ|疑似コード",
    ]
    
    # ユーザー明示拒否
    NO_SEARCH_EXPLICIT = [
        r"検索(しないで|不要|なしで|使わずに)",
        r"ブラウズ(しないで|不要)",
        r"web(なしで|不要)",
        r"手元の情報だけで|一般論で|概念だけで|オフラインで",
    ]
    
    # ログ/デバッグ優先（検索より解析）
    LOCAL_INPUT_PATTERNS = [
        r"(ログ|log|スタックトレース|stack trace|エラーメッセージ|error message)",
        r"(例外|traceback|再現手順|repro)",
    ]
    
    # 不確実性フレーズ（回答から検出）
    UNCERTAINTY_PATTERNS = [
        r"たぶん|おそらく|かも|可能性|一般的|場合による|状況による",
        r"記憶では|はず|と思う|確証|断言",
        r"要確認|未確認|不明|わからない|公式|一次ソース",
    ]
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Args:
            config: カスタム設定（閾値、パターン上書き等）
        """
        self.config = config or {}
        
        # 正規表現をコンパイル
        self._freshness_re = self._compile_patterns(self.FRESHNESS_PATTERNS)
        self._change_re = self._compile_patterns(self.CHANGE_PATTERNS)
        self._risk_re = self._compile_patterns(self.RISK_PATTERNS)
        self._entity_re = self._compile_patterns(self.ENTITY_PATTERNS)
        self._creative_re = self._compile_patterns(self.CREATIVE_PATTERNS)
        self._no_search_re = self._compile_patterns(self.NO_SEARCH_EXPLICIT)
        self._local_input_re = self._compile_patterns(self.LOCAL_INPUT_PATTERNS)
        self._uncertainty_re = self._compile_patterns(self.UNCERTAINTY_PATTERNS)
    
    def _compile_patterns(self, patterns: List[str]) -> re.Pattern:
        """パターンリストを1つの正規表現にコンパイル"""
        combined = "|".join(f"({p})" for p in patterns)
        return re.compile(combined, re.IGNORECASE)
    
    def _find_matches(self, pattern: re.Pattern, text: str) -> List[str]:
        """マッチした文字列をリストで返す"""
        return [m.group() for m in pattern.finditer(text)]
    
    def evaluate(
        self,
        user_query: str,
        assistant_response: str = "",
    ) -> TriggerResult:
        """
        WEB検索が必要か判定
        
        Args:
            user_query: ユーザーの質問
            assistant_response: ChatGPTの回答（任意）
        
        Returns:
            TriggerResult: 判定結果
        """
        combined_text = f"{user_query} {assistant_response}"
        matched_patterns = []
        
        # === Hard Gate OFF（即非検索）===
        
        # 1. ユーザー明示拒否
        no_search_matches = self._find_matches(self._no_search_re, user_query)
        if no_search_matches:
            return TriggerResult(
                should_search=False,
                reason="user_explicit_decline",
                gate_type="hard_off",
                matched_patterns=no_search_matches,
            )
        
        # 2. クリエイティブ/設計系（固有名詞なしの場合）
        creative_matches = self._find_matches(self._creative_re, user_query)
        entity_matches = self._find_matches(self._entity_re, user_query)
        
        if creative_matches and not entity_matches:
            return TriggerResult(
                should_search=False,
                reason="creative_or_design",
                gate_type="hard_off",
                matched_patterns=creative_matches,
            )
        
        # === Hard Gate ON（即検索）===
        
        # 3. 高リスク領域
        risk_matches = self._find_matches(self._risk_re, combined_text)
        if risk_matches:
            matched_patterns.extend(risk_matches)
            return TriggerResult(
                should_search=True,
                reason="high_risk_domain",
                gate_type="hard_on",
                matched_patterns=risk_matches,
                score=1.0,
            )
        
        # 4. 時間依存 + 固有名詞
        freshness_matches = self._find_matches(self._freshness_re, combined_text)
        change_matches = self._find_matches(self._change_re, combined_text)
        
        has_freshness = bool(freshness_matches or change_matches)
        has_entity = bool(entity_matches)
        
        if has_freshness and has_entity:
            matched_patterns.extend(freshness_matches)
            matched_patterns.extend(change_matches)
            matched_patterns.extend(entity_matches)
            return TriggerResult(
                should_search=True,
                reason="freshness_with_entity",
                gate_type="hard_on",
                matched_patterns=matched_patterns,
                score=0.9,
            )
        
        # 5. 不確実性シグナル（回答から検出）+ 固有名詞
        if assistant_response:
            uncertainty_matches = self._find_matches(self._uncertainty_re, assistant_response)
            if uncertainty_matches and has_entity:
                matched_patterns.extend(uncertainty_matches)
                matched_patterns.extend(entity_matches)
                return TriggerResult(
                    should_search=True,
                    reason="uncertainty_with_entity",
                    gate_type="hard_on",
                    matched_patterns=matched_patterns,
                    score=0.8,
                )
        
        # === デフォルト: 検索なし ===
        return TriggerResult(
            should_search=False,
            reason="no_trigger",
            gate_type="none",
            matched_patterns=[],
        )
    
    def generate_search_hints(self, result: TriggerResult, user_query: str) -> Dict[str, Any]:
        """
        検索が必要な場合、/research向けのヒントを生成
        
        Returns:
            {
                "query_hints": ["検索クエリ候補1", ...],
                "what_to_search": ["official_docs", "release_notes", ...],
                "freshness_requirement": "<=90days" or None
            }
        """
        if not result.should_search:
            return {}
        
        hints = {
            "query_hints": [],
            "what_to_search": [],
            "freshness_requirement": None,
        }
        
        # エンティティ抽出
        entities = self._find_matches(self._entity_re, user_query)
        
        # 理由に応じたヒント
        if result.reason == "high_risk_domain":
            hints["what_to_search"] = ["official_docs", "security_advisory"]
            hints["query_hints"] = [f"{e} official documentation" for e in entities[:2]]
        
        elif result.reason == "freshness_with_entity":
            hints["what_to_search"] = ["release_notes", "status_page", "pricing"]
            hints["freshness_requirement"] = "<=90days"
            hints["query_hints"] = [f"{e} latest update 2026" for e in entities[:2]]
        
        elif result.reason == "uncertainty_with_entity":
            hints["what_to_search"] = ["official_docs", "faq"]
            hints["query_hints"] = [f"{e} documentation" for e in entities[:2]]
        
        return hints


def _cli_main() -> int:
    import json  # noqa: F401  # debug print用

    trigger = ResearchTrigger()

    test_cases = [
        ("OpenAI APIの最新の料金を教えて", ""),
        ("CVE-2026-12345の影響範囲は？", ""),
        ("v1.2.3のリリースノートを確認したい", ""),
        ("マイクロサービスの設計方針について教えて", ""),
        ("このコードの要約をお願い", ""),
        ("検索しないで一般論で教えて", ""),
        ("Playwrightのセレクタ仕様は？", "正確には公式ドキュメントを確認してください。おそらく..."),
    ]

    print("=== Research Trigger Test ===\n")
    for query, response in test_cases:
        result = trigger.evaluate(query, response)
        print(f"Q: {query[:50]}...")
        print(f"   → search={result.should_search}, reason={result.reason}")
        print(f"   → patterns={result.matched_patterns[:3]}")
        print()
    return 0


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve()
    for _parent in _repo_root.parents:
        _shared_dir = _parent / ".agent" / "workflows" / "shared"
        if _shared_dir.exists():
            if str(_shared_dir) not in sys.path:
                sys.path.insert(0, str(_shared_dir))
            break
    from workflow_logging_hook import run_logged_main as _run_logged_main
    raise SystemExit(
        _run_logged_main("desktop", "research_trigger", _cli_main, phase_name="RESEARCH_TRIGGER_CLI")
    )

