# -*- coding: utf-8 -*-
"""
Desktop Control v5.0.0-alpha - Planner Module
自然言語指示 → Intent → ActionSpec[] 変換

設計方針:
- 逐次plan_next()方式（1〜3手ずつ返す）
- action_type少数固定
- target責務はExecutor寄り（Plannerはセレクタのヒント）
- 学習機能: 成功した操作をテンプレートとして保存
"""

from __future__ import annotations
import re
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Callable
from enum import Enum
from pathlib import Path
from datetime import datetime

from .action_spec import (
    ActionSpec, VerifyCheck, WaitSpec, RiskSpec,
    RiskLevel, VerifyType, WaitKind,
    navigate_action, fill_action, click_action, press_action, wait_action,
)
from .runtime_paths import get_template_store_path



class IntentVerb(str, Enum):
    """意図の動詞"""
    SEARCH = "search"       # 検索
    ASK = "ask"             # 質問
    NAVIGATE = "navigate"   # ナビゲート
    CLICK = "click"         # クリック
    FILL = "fill"           # 入力
    READ = "read"           # 読み取り
    UNKNOWN = "unknown"


class TargetApp(str, Enum):
    """対象アプリ"""
    GOOGLE = "google"
    CHATGPT = "chatgpt"
    PERPLEXITY = "perplexity"
    BROWSER = "browser"     # 汎用ブラウザ
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """
    正規化された意図
    
    自然言語指示から抽出された構造化された意図。
    """
    verb: IntentVerb
    target_app: TargetApp
    query: str = ""
    url: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Observation:
    """
    現在の観測状態
    
    Executorから受け取る現在のUI状態。
    """
    url: str = ""
    title: str = ""
    app: str = ""
    error: Optional[str] = None
    last_action_success: bool = True
    last_failure_kind: Optional[str] = None
    last_failure_reason: Optional[str] = None


@dataclass
class PlanResult:
    """
    plan_next()の結果
    
    次に実行すべき1〜3アクションのリスト。
    """
    actions: List[ActionSpec]
    is_complete: bool = False
    reasoning: str = ""


# ================== Intent抽出パターン ==================

INTENT_PATTERNS: List[tuple[str, IntentVerb, TargetApp]] = [
    # Google検索
    (r"(?:google|グーグル)で(.+)(?:を)?検索", IntentVerb.SEARCH, TargetApp.GOOGLE),
    (r"(.+)(?:を)?(?:google|グーグル)で検索", IntentVerb.SEARCH, TargetApp.GOOGLE),
    (r"(.+)(?:を)?ググ(?:って|る)", IntentVerb.SEARCH, TargetApp.GOOGLE),
    
    # ChatGPT
    (r"(?:chatgpt|チャットgpt)(?:で|に)(.+)(?:を)?(?:聞い|質問|相談)", IntentVerb.ASK, TargetApp.CHATGPT),
    (r"(.+)(?:を)?(?:chatgpt|チャットgpt)(?:で|に)(?:聞い|質問|相談)", IntentVerb.ASK, TargetApp.CHATGPT),
    
    # Perplexity
    (r"(?:perplexity|パープレキシティ)で(.+)(?:を)?検索", IntentVerb.SEARCH, TargetApp.PERPLEXITY),
    (r"(.+)(?:を)?(?:perplexity|パープレキシティ)で検索", IntentVerb.SEARCH, TargetApp.PERPLEXITY),
    
    # 汎用ナビゲート
    (r"(https?://\S+)(?:を)?(?:開い|アクセス)", IntentVerb.NAVIGATE, TargetApp.BROWSER),
    (r"(.+)(?:を)?開(?:い|く)", IntentVerb.NAVIGATE, TargetApp.BROWSER),
]


def extract_intent(instruction: str) -> Intent:
    """
    自然言語指示からIntentを抽出
    
    Args:
        instruction: ユーザーの自然言語指示
        
    Returns:
        抽出されたIntent
    """
    instruction_lower = instruction.lower()
    
    for pattern, verb, app in INTENT_PATTERNS:
        match = re.search(pattern, instruction, re.IGNORECASE)
        if match:
            query = match.group(1).strip() if match.groups() else ""
            return Intent(
                verb=verb,
                target_app=app,
                query=query,
                confidence=0.9,
            )
    
    # フォールバック: 単純なキーワードマッチ
    if "検索" in instruction:
        return Intent(verb=IntentVerb.SEARCH, target_app=TargetApp.GOOGLE, query=instruction, confidence=0.5)
    if "chatgpt" in instruction_lower or "チャット" in instruction:
        return Intent(verb=IntentVerb.ASK, target_app=TargetApp.CHATGPT, query=instruction, confidence=0.5)
    
    return Intent(verb=IntentVerb.UNKNOWN, target_app=TargetApp.UNKNOWN, query=instruction, confidence=0.3)


# ================== ActionSpecテンプレート ==================

def template_google_search(query: str) -> List[ActionSpec]:
    """Google検索のActionSpec列"""
    return [
        navigate_action("https://www.google.com"),
        fill_action("textarea[name='q']", query),
        press_action("Enter"),
        ActionSpec(
            layer="cdp",
            action_type="wait",
            wait=WaitSpec(kind=WaitKind.ELEMENT_VISIBLE, target="#search", timeout_ms=10000),
            verify=VerifyCheck(type=VerifyType.URL_CONTAINS, target="search?q="),
            description="Wait for search results",
        ),
    ]


def template_chatgpt_ask(query: str) -> List[ActionSpec]:
    """ChatGPT質問のActionSpec列"""
    return [
        navigate_action("https://chatgpt.com", wait_for="textarea"),
        fill_action("#prompt-textarea", query),
        press_action("Enter"),
        ActionSpec(
            layer="cdp",
            action_type="wait",
            wait=WaitSpec(
                kind=WaitKind.ELEMENT_VISIBLE,
                target="button[data-testid='send-button']",
                timeout_ms=180000,
            ),
            verify=VerifyCheck(type=VerifyType.ELEMENT_VISIBLE, target="button[data-testid='send-button']"),
            risk=RiskSpec(level=RiskLevel.MEDIUM, reason="外部LLMへの送信"),
            description="Wait for ChatGPT response",
        ),
    ]


def template_perplexity_search(query: str) -> List[ActionSpec]:
    """Perplexity検索のActionSpec列"""
    return [
        navigate_action("https://www.perplexity.ai"),
        fill_action("textarea", query),
        press_action("Enter"),
        ActionSpec(
            layer="cdp",
            action_type="wait",
            wait=WaitSpec(kind=WaitKind.NETWORK_IDLE, timeout_ms=30000),
            description="Wait for Perplexity response",
        ),
    ]


TEMPLATES: Dict[tuple[IntentVerb, TargetApp], Callable[[str], List[ActionSpec]]] = {
    (IntentVerb.SEARCH, TargetApp.GOOGLE): template_google_search,
    (IntentVerb.ASK, TargetApp.CHATGPT): template_chatgpt_ask,
    (IntentVerb.SEARCH, TargetApp.PERPLEXITY): template_perplexity_search,
}


# ================== 学習機能（TemplateStore） ==================

# デフォルトの保存先
DEFAULT_TEMPLATE_STORE_PATH = get_template_store_path()

# スキーマバージョン（互換性管理用）
SCHEMA_VERSION = "1.1.0"

# 保存ゲート設定
SAVE_GATE_MIN_SUCCESS = 2       # 最低成功回数
SAVE_GATE_MIN_SUCCESS_RATE = 0.7  # 最低成功率

# 退役設定
RETIRE_AFTER_DAYS_UNUSED = 30   # 未使用日数で退役
RETIRE_MIN_FAIL_RATE = 0.5      # 失敗率がこれ以上で退役候補

DEFAULT_PERPLEXITY_EXE = os.getenv("AG_DESKTOP_PERPLEXITY_EXE", "Perplexity.exe")


@dataclass
class EnvironmentTags:
    """環境メタデータ"""
    os: str = ""                 # "windows", "macos", "linux"
    browser: str = ""            # "chrome", "firefox", "edge"
    locale: str = ""             # "ja-JP", "en-US"
    resolution: str = ""         # "1920x1080"
    app_version: str = ""        # 対象アプリのバージョン


@dataclass
class LearnedTemplate:
    """学習済みテンプレート（v1.1.0: 強化版）"""
    verb: str
    target_app: str
    pattern: str                    # 元の指示（パターンマッチ用）
    actions: List[Dict[str, Any]]   # ActionSpecのシリアライズ済みリスト
    
    # 統計
    success_count: int = 0
    fail_count: int = 0
    pending_count: int = 1          # 保存ゲート用（まだ確定前のカウント）
    
    # タイムスタンプ
    created_at: str = ""
    last_used_at: str = ""
    last_success_at: str = ""
    
    # 環境
    environment: Dict[str, str] = field(default_factory=dict)
    
    # ステータス
    confirmed: bool = False         # 保存ゲートを通過したか
    retired: bool = False           # 退役済みか
    retire_reason: str = ""
    
    # スキーマ
    schema_version: str = SCHEMA_VERSION




class TemplateStore:
    """
    学習済みテンプレートの保存・読み込み
    
    成功した操作をテンプレートとして保存し、次回から再利用する。
    """
    
    def __init__(self, store_path: Optional[Path] = None):
        self.store_path = store_path or DEFAULT_TEMPLATE_STORE_PATH
        self._templates: Dict[str, LearnedTemplate] = {}
        self._load()
    
    def _load(self) -> None:
        """保存済みテンプレートを読み込み（スキーマ互換対応）"""
        if self.store_path.exists():
            try:
                with open(self.store_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    
                    # v1.1.0形式: {schema_version, templates}
                    if "schema_version" in raw and "templates" in raw:
                        data = raw["templates"]
                    else:
                        # 旧形式: {key: template}
                        data = raw
                    
                    for key, item in data.items():
                        # 旧スキーマのデフォルト値を補完
                        item.setdefault("pending_count", 1)
                        item.setdefault("last_success_at", "")
                        item.setdefault("environment", {})
                        item.setdefault("confirmed", True)  # 旧データは確定済み扱い
                        item.setdefault("retired", False)
                        item.setdefault("retire_reason", "")
                        item.setdefault("schema_version", SCHEMA_VERSION)
                        self._templates[key] = LearnedTemplate(**item)
            except Exception:
                self._templates = {}
    
    def _save(self) -> None:
        """テンプレートを原子的に保存（temp→rename）"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": SCHEMA_VERSION,
            "templates": {key: asdict(t) for key, t in self._templates.items()}
        }
        # 原子的書き込み: 一時ファイルに書いてからリネーム
        temp_path = self.store_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        temp_path.replace(self.store_path)
    
    def _make_key(self, verb: IntentVerb, target_app: TargetApp) -> str:
        """キー生成"""
        return f"{verb.value}:{target_app.value}"
    
    def has_template(self, intent: Intent) -> bool:
        """
        利用可能な学習済みテンプレートがあるか
        
        確定済み（confirmed=True）かつ退役していない（retired=False）のみTrue
        """
        key = self._make_key(intent.verb, intent.target_app)
        if key not in self._templates:
            return False
        template = self._templates[key]
        return template.confirmed and not template.retired
    
    def get_actions(self, intent: Intent) -> Optional[List[ActionSpec]]:
        """
        学習済みテンプレートからActionSpec列を取得
        
        確定済み・非退役のテンプレートのみ返す
        """
        key = self._make_key(intent.verb, intent.target_app)
        if key not in self._templates:
            return None
        
        template = self._templates[key]
        
        # 保存ゲート: 確定していないか退役済みなら使用しない
        if not template.confirmed or template.retired:
            return None
        
        template.last_used_at = datetime.now().isoformat()
        self._save()
        
        # ActionSpecを復元（queryを置換）
        actions = []
        for action_dict in template.actions:
            # queryプレースホルダを実際のクエリに置換
            action_dict_copy = json.loads(json.dumps(action_dict))  # deep copy
            self._replace_query_placeholder(action_dict_copy, intent.query)
            actions.append(self._dict_to_action_spec(action_dict_copy))
        
        return actions
    
    def _replace_query_placeholder(self, d: Dict, query: str) -> None:
        """辞書内の{{QUERY}}プレースホルダを置換"""
        for key, value in d.items():
            if isinstance(value, str) and "{{QUERY}}" in value:
                d[key] = value.replace("{{QUERY}}", query)
            elif isinstance(value, dict):
                self._replace_query_placeholder(value, query)
    
    def _dict_to_action_spec(self, d: Dict) -> ActionSpec:
        """辞書からActionSpecを復元"""
        # ネストした型を復元
        wait_dict = d.pop("wait", {})
        verify_dict = d.pop("verify", {})
        risk_dict = d.pop("risk", {})
        
        wait = WaitSpec(
            kind=WaitKind(wait_dict.get("kind", "none")),
            target=wait_dict.get("target", ""),
            timeout_ms=wait_dict.get("timeout_ms", 30000),
            poll_ms=wait_dict.get("poll_ms", 500),
        )
        verify = VerifyCheck(
            type=VerifyType(verify_dict.get("type", "none")),
            target=verify_dict.get("target", ""),
            expected=verify_dict.get("expected", ""),
            timeout_ms=verify_dict.get("timeout_ms", 5000),
        )
        risk = RiskSpec(
            level=RiskLevel(risk_dict.get("level", "low")),
            requires_approval=risk_dict.get("requires_approval", False),
            reason=risk_dict.get("reason", ""),
        )
        
        return ActionSpec(
            layer=d.get("layer", "cdp"),
            action_type=d.get("action_type", "wait"),
            target=d.get("target", ""),
            params=d.get("params", {}),
            wait=wait,
            verify=verify,
            risk=risk,
            description=d.get("description", ""),
            fallback_layer=d.get("fallback_layer"),
        )
    
    def learn(
        self,
        intent: Intent,
        actions: List[ActionSpec],
        success: bool = True,
        environment: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        操作を学習してテンプレートとして保存（保存ゲート対応）
        
        保存ゲート:
        - 新規テンプレートはpending状態で保存
        - SAVE_GATE_MIN_SUCCESS回成功で確定（confirmed=True）
        - 確定前は使用しない（has_templateで除外）
        
        Args:
            intent: 元のIntent
            actions: 実行したActionSpec列
            success: 成功したか
            environment: 環境メタデータ（OS/ブラウザ/ロケール等）
        """
        key = self._make_key(intent.verb, intent.target_app)
        now = datetime.now().isoformat()
        
        if key in self._templates:
            # 既存テンプレートの更新
            template = self._templates[key]
            
            if success:
                template.success_count += 1
                template.last_success_at = now
                
                # 保存ゲートチェック（まだ確定していない場合）
                if not template.confirmed:
                    if template.success_count >= SAVE_GATE_MIN_SUCCESS:
                        total = template.success_count + template.fail_count
                        if total > 0 and template.success_count / total >= SAVE_GATE_MIN_SUCCESS_RATE:
                            template.confirmed = True
            else:
                template.fail_count += 1
                
                # 退役チェック（確定済みでも失敗率が高いと退役）
                total = template.success_count + template.fail_count
                if total >= 5:  # 最低5回試行後に判定
                    fail_rate = template.fail_count / total
                    if fail_rate >= RETIRE_MIN_FAIL_RATE:
                        template.retired = True
                        template.retire_reason = f"失敗率が高い ({fail_rate:.0%})"
            
            template.last_used_at = now
        else:
            if not success:
                # 失敗した新規テンプレートは保存しない
                return
            
            # 新規テンプレート作成（queryを{{QUERY}}に置換）
            actions_serialized = []
            for action in actions:
                action_dict = self._action_spec_to_dict(action)
                self._replace_with_placeholder(action_dict, intent.query)
                actions_serialized.append(action_dict)
            
            template = LearnedTemplate(
                verb=intent.verb.value,
                target_app=intent.target_app.value,
                pattern=intent.query,
                actions=actions_serialized,
                success_count=1,
                fail_count=0,
                pending_count=1,
                created_at=now,
                last_used_at=now,
                last_success_at=now,
                environment=environment or self._get_current_environment(),
                confirmed=False,  # 保存ゲート: まだ確定していない
                retired=False,
                schema_version=SCHEMA_VERSION,
            )
            self._templates[key] = template
        
        self._save()
    
    def _get_current_environment(self) -> Dict[str, str]:
        """現在の環境情報を取得"""
        import platform
        import locale
        return {
            "os": platform.system().lower(),
            "locale": locale.getdefaultlocale()[0] or "unknown",
            "python_version": platform.python_version(),
        }

    
    def _action_spec_to_dict(self, action: ActionSpec) -> Dict[str, Any]:
        """ActionSpecを辞書に変換"""
        return {
            "layer": action.layer,
            "action_type": action.action_type,
            "target": action.target,
            "params": action.params,
            "wait": {
                "kind": action.wait.kind.value,
                "target": action.wait.target,
                "timeout_ms": action.wait.timeout_ms,
                "poll_ms": action.wait.poll_ms,
            },
            "verify": {
                "type": action.verify.type.value,
                "target": action.verify.target,
                "expected": action.verify.expected,
                "timeout_ms": action.verify.timeout_ms,
            },
            "risk": {
                "level": action.risk.level.value,
                "requires_approval": action.risk.requires_approval,
                "reason": action.risk.reason,
            },
            "description": action.description,
            "fallback_layer": action.fallback_layer,
        }
    
    def _replace_with_placeholder(self, d: Dict, query: str) -> None:
        """辞書内のqueryを{{QUERY}}プレースホルダに置換"""
        if not query:
            return
        for key, value in d.items():
            if isinstance(value, str) and query in value:
                d[key] = value.replace(query, "{{QUERY}}")
            elif isinstance(value, dict):
                self._replace_with_placeholder(value, query)
    
    def get_stats(self) -> Dict[str, Any]:
        """学習統計を取得（強化版）"""
        return {
            "schema_version": SCHEMA_VERSION,
            "total_templates": len(self._templates),
            "confirmed_count": sum(1 for t in self._templates.values() if t.confirmed and not t.retired),
            "pending_count": sum(1 for t in self._templates.values() if not t.confirmed),
            "retired_count": sum(1 for t in self._templates.values() if t.retired),
            "templates": [
                {
                    "key": key,
                    "success_count": t.success_count,
                    "fail_count": t.fail_count,
                    "confirmed": t.confirmed,
                    "retired": t.retired,
                    "retire_reason": t.retire_reason,
                    "environment": t.environment,
                    "created_at": t.created_at,
                    "last_used_at": t.last_used_at,
                }
                for key, t in self._templates.items()
            ]
        }



# グローバルインスタンス（遅延初期化）
_template_store: Optional[TemplateStore] = None


def get_template_store() -> TemplateStore:
    """TemplateStoreのシングルトンを取得"""
    global _template_store
    if _template_store is None:
        _template_store = TemplateStore()
    return _template_store


# ================== Planner本体 ==================

class Planner:
    """
    Desktop Agent Planner
    
    自然言語指示を解釈して、ActionSpec列を生成する。
    逐次plan_next()方式で、1〜3手ずつ返す。
    学習機能: 成功した操作をテンプレートとして保存し、次回から再利用。
    """
    
    def __init__(self, use_learning: bool = True):
        """
        Args:
            use_learning: 学習機能を使用するか
        """
        self._current_intent: Optional[Intent] = None
        self._pending_actions: List[ActionSpec] = []
        self._executed_count: int = 0
        self._use_learning = use_learning
        self._store = get_template_store() if use_learning else None
        self._from_learned: bool = False  # 学習済みテンプレートを使ったか
    
    def reset(self) -> None:
        """状態をリセット"""
        self._current_intent = None
        self._pending_actions = []
        self._executed_count = 0
        self._from_learned = False
    
    def plan_next(
        self,
        instruction: str,
        obs: Observation,
        max_actions: int = 3,
    ) -> PlanResult:
        """
        次のアクションを計画
        
        Args:
            instruction: ユーザーの自然言語指示
            obs: 現在の観測状態
            max_actions: 返すアクションの最大数
            
        Returns:
            PlanResult: 次に実行すべきアクションのリスト
        """
        # 新しい指示の場合、Intent抽出
        if self._current_intent is None or instruction != self._current_intent.query:
            self._current_intent = extract_intent(instruction)
            self._pending_actions = self._expand_template(self._current_intent)
            self._executed_count = 0
        
        # 前回失敗の場合、リトライ判断
        if not obs.last_action_success:
            if obs.last_failure_kind == "transient":
                # transientならリトライ
                pass
            elif obs.last_failure_kind == "deterministic":
                # deterministicなら再計画が必要（今は単純に継続）
                pass
        
        # 次のアクションを返す
        if not self._pending_actions:
            return PlanResult(actions=[], is_complete=True, reasoning="全アクション完了")
        
        actions_to_return = self._pending_actions[:max_actions]
        self._pending_actions = self._pending_actions[max_actions:]
        self._executed_count += len(actions_to_return)
        
        is_complete = len(self._pending_actions) == 0
        
        source = "(learned)" if self._from_learned else "(builtin)"
        
        return PlanResult(
            actions=actions_to_return,
            is_complete=is_complete,
            reasoning=f"Intent: {self._current_intent.verb.value} on {self._current_intent.target_app.value} {source}",
        )
    
    def _expand_template(self, intent: Intent) -> List[ActionSpec]:
        """IntentからActionSpec列を展開（学習済み優先→ビルトイン→LLM推論）"""
        # 1. 学習済みテンプレートを優先
        if self._use_learning and self._store and self._store.has_template(intent):
            actions = self._store.get_actions(intent)
            if actions:
                self._from_learned = True
                return actions
        
        # 2. ビルトインテンプレート
        key = (intent.verb, intent.target_app)
        if key in TEMPLATES:
            self._from_learned = False
            return TEMPLATES[key](intent.query)
        
        # 3. URL直接ナビゲーション
        if intent.verb == IntentVerb.NAVIGATE and intent.url:
            self._from_learned = False
            return [navigate_action(intent.url)]
        
        # 4. LLM推論フォールバック（ビルトインも学習済みもない場合）
        self._from_learned = False
        return self._llm_infer_actions(intent)
    
    def _llm_infer_actions(self, intent: Intent) -> List[ActionSpec]:
        """
        LLMを使ってIntent→ActionSpec列を推論
        
        ビルトインテンプレートも学習済みテンプレートもない場合に使用。
        推論結果は成功後に学習される。
        """
        # 推論用プロンプト生成
        prompt = self._build_llm_prompt(intent)
        
        # LLM呼び出し（簡易版：実際はAPIを呼ぶ）
        # 現状はヒューリスティックフォールバック
        actions = self._heuristic_fallback(intent)
        
        if actions:
            # 推論元を記録（デバッグ用）
            for action in actions:
                action.description = f"[LLM推論] {action.description}"
        
        return actions
    
    def _build_llm_prompt(self, intent: Intent) -> str:
        """LLM推論用プロンプトを構築"""
        return f"""
以下のユーザー操作を実行するためのActionSpec列を生成してください。

意図:
- 動詞: {intent.verb.value}
- 対象アプリ: {intent.target_app.value}
- クエリ: {intent.query}

ActionSpecの形式:
- layer: "cdp" | "uia" | "pixel"
- action_type: "navigate" | "fill" | "click" | "press" | "wait"
- target: セレクタまたは要素名
- params: 追加パラメータ

JSON形式で出力してください。
"""
    
    def _heuristic_fallback(self, intent: Intent) -> List[ActionSpec]:
        """
        ヒューリスティックフォールバック（LLM未接続時）
        
        基本的な操作パターンをルールベースで生成。
        """
        actions = []
        
        # アプリ起動（共通パターン）
        app_launch_commands = {
            TargetApp.PERPLEXITY: {
                "exe": DEFAULT_PERPLEXITY_EXE,
                "args": ["--remote-debugging-port=9224"],
            },
            TargetApp.CHATGPT: {
                "url": "https://chatgpt.com/",
            },
            TargetApp.GOOGLE: {
                "url": "https://www.google.com/",
            },
        }
        
        if intent.target_app in app_launch_commands:
            launch_info = app_launch_commands[intent.target_app]
            
            if "exe" in launch_info:
                # デスクトップアプリ起動
                actions.append(ActionSpec(
                    layer="uia",
                    action_type="launch",
                    target=launch_info["exe"],
                    params={"args": launch_info.get("args", [])},
                    description=f"{intent.target_app.value}アプリを起動",
                ))
                # フォーカス待機
                actions.append(wait_action(
                    kind=WaitKind.TITLE,
                    target=intent.target_app.value.capitalize(),
                    timeout_ms=5000,
                ))
            elif "url" in launch_info:
                # ブラウザで開く
                actions.append(navigate_action(
                    url=launch_info["url"],
                    description=f"{intent.target_app.value}を開く",
                ))
        
        # 動詞に応じたアクション
        if intent.verb == IntentVerb.SEARCH and intent.query:
            # 検索入力
            actions.append(fill_action(
                selector="textarea, input[type='text'], input[type='search']",
                value=intent.query,
                description=f"検索ボックスに「{intent.query}」を入力",
            ))
            actions.append(press_action(
                key="Enter",
                description="Enterキーで検索実行",
            ))
        
        elif intent.verb == IntentVerb.ASK and intent.query:
            # 質問入力
            actions.append(fill_action(
                selector="textarea, input[type='text']",
                value=intent.query,
                description=f"「{intent.query}」を入力",
            ))
            actions.append(press_action(
                key="Enter",
                description="送信",
            ))
        
        return actions
    
    def learn(self, success: bool = True, actions: Optional[List[ActionSpec]] = None) -> None:
        """
        現在のタスクを学習
        
        成功した場合、現在のIntentと操作をテンプレートとして保存。
        
        Args:
            success: 成功したか
            actions: 学習するアクション列（Noneの場合は最後に実行したもの）
        """
        if not self._use_learning or not self._store:
            return
        if not self._current_intent:
            return
        
        # ビルトインテンプレートは学習しない（既にある）
        key = (self._current_intent.verb, self._current_intent.target_app)
        if key in TEMPLATES:
            return
        
        # 学習対象のアクションを決定
        if actions is None:
            # 既に展開済みのテンプレートから復元（簡易実装）
            actions = self._expand_template(self._current_intent)
        
        if actions:
            self._store.learn(self._current_intent, actions, success)
    
    @property
    def current_intent(self) -> Optional[Intent]:
        """現在処理中のIntent"""
        return self._current_intent
    
    @property
    def from_learned(self) -> bool:
        """学習済みテンプレートを使用したか"""
        return self._from_learned


# ================== ユーティリティ ==================

def plan_simple(instruction: str, use_learning: bool = True) -> List[ActionSpec]:
    """
    単純な1回呼び出し用のヘルパー
    
    Args:
        instruction: 自然言語指示
        use_learning: 学習機能を使用するか
        
    Returns:
        ActionSpec列（全て）
    """
    planner = Planner(use_learning=use_learning)
    result = planner.plan_next(instruction, Observation(), max_actions=100)
    return result.actions


def learn_from_actions(
    instruction: str,
    actions: List[ActionSpec],
    success: bool = True,
) -> None:
    """
    外部から学習を実行するヘルパー
    
    Antigravityが俺（AI）が考えて実行したアクションを学習させる用。
    
    Args:
        instruction: 元の指示
        actions: 実行したアクション列
        success: 成功したか
    """
    intent = extract_intent(instruction)
    store = get_template_store()
    store.learn(intent, actions, success)

