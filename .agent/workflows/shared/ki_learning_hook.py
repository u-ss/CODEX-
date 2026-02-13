# -*- coding: utf-8 -*-
"""
KI Learning Integration Hook

エージェント（/desktop, /code）から KI Learning Agent を安定して利用するための共通モジュール。
環境変数 > 相対パス > Null Client のフォールバック機構を提供。

使用例:
    from ki_learning_hook import get_learning_client, report_action_outcome, check_risks
    
    # クライアント取得（失敗時は None）
    client = get_learning_client()
    
    # 簡易記録
    report_action_outcome('/desktop', 'click_send', 'SUCCESS', latency_ms=150)
    
    # リスク参照
    risks = check_risks('/desktop', 'click_send', signature_key)
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 型ヒント用（実際のクラスは動的インポート）
LearningClient = Any
AgentEvent = Any


def _find_knowledge_path() -> Optional[Path]:
    """
    KI Learning ライブラリのパスを探索
    
    優先順位:
    1. 環境変数 ANTIGRAVITY_KNOWLEDGE_PATH
    2. デフォルトパス（~/.gemini/antigravity/knowledge）
    3. None（見つからない場合）
    """
    # 1. 環境変数
    env_path = os.environ.get('ANTIGRAVITY_KNOWLEDGE_PATH')
    if env_path:
        p = Path(env_path)
        if (p / 'learning').exists():
            return p

    # 2. リポジトリローカル（開発/検証向け）
    #    antigravity/knowledge を使える場合はそれを優先する。
    try:
        repo_local = Path.cwd() / 'knowledge'
        if (repo_local / 'learning').exists():
            return repo_local
    except Exception:
        pass

    # 3. デフォルトパス
    default_path = Path.home() / '.gemini' / 'antigravity' / 'knowledge'
    if (default_path / 'learning').exists():
        return default_path
    
    # 4. 見つからない
    return None


def _ensure_import() -> bool:
    """
    KI Learning ライブラリをインポート可能にする
    
    Returns:
        True: インポート可能、False: 不可
    """
    knowledge_path = _find_knowledge_path()
    if not knowledge_path:
        return False
    
    path_str = str(knowledge_path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)
    
    return True


def get_learning_client():
    """
    KI Learning クライアントを取得（Null-safe）
    
    Returns:
        LearningClient or None（ライブラリ不在時）
    """
    if not _ensure_import():
        return None
    
    try:
        from learning import get_client
        return get_client()
    except ImportError:
        return None
    except Exception:
        return None


# ============================================================
# 失敗分類の自動バケット化 (gap_ki_failure_enrichment)
# error_type/root_cause が unknown のときにパターンマッチで分類
# ============================================================

_FAILURE_BUCKETS: list[tuple[str, str, list[str]]] = [
    # (error_type, root_cause, キーワードリスト)
    ("timeout", "no_ack", ["timeout", "no_ack", "deadline", "タイムアウト"]),
    ("timeout", "thinking_timeout", ["thinking", "生成中", "generating"]),
    ("ui_not_found", "selector_stale", ["not_found", "no_edit_found", "control_not_found", "selector"]),
    ("ui_not_found", "window_missing", ["no_window", "window_not_found", "ウィンドウ"]),
    ("click_failed", "misclick", ["click_failed", "hotspot", "クリック"]),
    ("process_error", "process_mismatch", ["process_mismatch", "process_not_running", "プロセス"]),
    ("network", "connection", ["network", "connection", "http", "ネットワーク"]),
    ("parse_error", "json_parse", ["json", "parse", "decode", "パース"]),
    ("permission", "policy_block", ["permission", "denied", "policy", "権限"]),
    ("config_error", "invalid_config", ["config", "設定", "schema", "validation"]),
    ("clipboard", "clipboard_failed", ["clipboard", "クリップボード"]),
    ("verification", "unverified", ["unverified", "no_strong_signal", "検証"]),
]


def _classify_failure(
    error_type: str = "",
    root_cause: str = "",
    **context: Any,
) -> tuple[str, str]:
    """
    失敗の error_type/root_cause を自動バケット分類する。

    既に明確な値が設定されている場合はそのまま返す。
    unknown / 空 の場合のみパターンマッチで推定する。
    """
    et = (error_type or "").strip().lower()
    rc = (root_cause or "").strip().lower()

    # 既に有意な値がある場合はそのまま
    if et and et != "unknown" and rc and rc != "unknown":
        return error_type, root_cause

    # マッチ対象テキストを構築
    search_text = f"{et} {rc}"
    for extra_key in ("error", "reason", "err", "why", "message"):
        if extra_key in context:
            search_text += f" {context[extra_key]}"
    search_text = search_text.lower()

    # パターンマッチ
    for bucket_et, bucket_rc, keywords in _FAILURE_BUCKETS:
        if any(kw in search_text for kw in keywords):
            final_et = error_type if (et and et != "unknown") else bucket_et
            final_rc = root_cause if (rc and rc != "unknown") else bucket_rc
            return final_et, final_rc

    # 分類不能
    return error_type or "unknown", root_cause or "unknown"


def report_action_outcome(
    agent: str,
    intent_class: str,
    outcome: str,  # 'SUCCESS' | 'FAILURE' | 'PARTIAL'
    **kwargs
) -> bool:
    """
    アクション結果を記録
    
    Args:
        agent: エージェント名（'/desktop', '/code'）
        intent_class: インテントクラス（'click_send', 'test_execution'）
        outcome: 結果（'SUCCESS', 'FAILURE', 'PARTIAL'）
        **kwargs: 追加情報（error_type, root_cause, fix, latency_ms など）
    
    Returns:
        True: 記録成功、False: 失敗（ライブラリ不在など）
    """
    client = get_learning_client()
    if not client:
        return False
    
    try:
        from learning import AgentEvent, make_signature_key, TargetSnapshot, EnvSnapshot
        
        # 失敗時: 自動バケット分類で unknown を低減
        if outcome in ('FAILURE', 'PARTIAL'):
            classified_et, classified_rc = _classify_failure(
                error_type=kwargs.get('error_type', ''),
                root_cause=kwargs.get('root_cause', ''),
                **{k: v for k, v in kwargs.items()
                   if k not in ('error_type', 'root_cause')},
            )
            kwargs['error_type'] = classified_et
            kwargs['root_cause'] = classified_rc
        
        # 基本イベント作成
        evt = AgentEvent(
            agent=agent,
            intent=kwargs.get('intent', intent_class),
            intent_class=intent_class,
            outcome=outcome,
        )
        
        # オプション項目
        if 'error_type' in kwargs:
            evt.error_type = kwargs['error_type']
        if 'root_cause' in kwargs:
            evt.root_cause = kwargs['root_cause']
        if 'fix' in kwargs:
            evt.fix = kwargs['fix']
        if 'confidence' in kwargs:
            evt.confidence = kwargs['confidence']
        if 'signature_key' in kwargs:
            evt.signature_key = kwargs['signature_key']
        
        # 記録
        client.report_outcome(evt)
        return True
    
    except Exception:
        return False


def check_risks(
    agent: str,
    intent_class: str,
    signature_key: str,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    過去の失敗リスクを参照
    
    Args:
        agent: エージェント名
        intent_class: インテントクラス
        signature_key: 署名キー
        top_k: 上位件数
    
    Returns:
        リスク情報リスト（空リストの場合はリスクなし or ライブラリ不在）
    """
    client = get_learning_client()
    if not client:
        return []
    
    try:
        risks = client.get_risks(
            signature_key=signature_key,
            intent_class=intent_class,
            top_k=top_k
        )
        return risks if risks else []
    except Exception:
        return []


def get_best_locators(
    agent: str,
    intent_class: str,
    signature_key: str,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    過去の成功パターンから最適なlocator候補を取得
    
    Args:
        agent: エージェント名
        intent_class: インテントクラス
        signature_key: 署名キー
        top_k: 上位件数
    
    Returns:
        locator候補リスト
    """
    client = get_learning_client()
    if not client:
        return []
    
    try:
        locators = client.get_best_locators(
            signature_key=signature_key,
            intent_class=intent_class,
            top_k=top_k
        )
        return locators if locators else []
    except Exception:
        return []


# ============================================================
# screen_key 生成ロジック (v2.0)
# GPT-5.2 相談結果に基づく優先順位: URL > UIA > title
# ============================================================

import re
import hashlib


def normalize_url(url: str) -> str:
    """URL を正規化
    
    正規化ルール:
    - スキーム/ホスト小文字化
    - 末尾スラッシュ統一
    - utm_*, gclid 等トラッキング除去
    - ID類は :id 置換（例: /user/12345 → /user/:id）
    
    Args:
        url: 元のURL
    
    Returns:
        正規化されたURL
    """
    if not url:
        return ""
    
    # スキーム/ホスト小文字化
    url = url.lower()
    
    # トラッキングパラメータ除去
    tracking_params = ['utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 
                       'utm_content', 'gclid', 'fbclid', 'ref', 'source']
    for param in tracking_params:
        url = re.sub(rf'[?&]{param}=[^&]*', '', url)
    
    # 空のクエリ文字列を削除
    url = re.sub(r'\?$', '', url)
    url = re.sub(r'\?&', '?', url)
    
    # ID類を :id に置換（連続する数字や UUID）
    url = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/:id', url)
    url = re.sub(r'/\d{4,}', '/:id', url)
    
    # 末尾スラッシュ統一
    if not url.endswith('/') and '?' not in url:
        url = url.rstrip('/') + '/'
    
    return url


def normalize_title(title: str) -> str:
    """タイトルを正規化
    
    正規化ルール:
    - 数字 → :num
    - メールアドレス → :email
    - 日時 → :date
    - 最大100文字
    
    Args:
        title: 元のタイトル
    
    Returns:
        正規化されたタイトル
    """
    if not title:
        return ""
    
    # メールアドレス
    title = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', ':email', title)
    
    # 日時パターン（YYYY-MM-DD, YYYY/MM/DD など）
    title = re.sub(r'\d{4}[-/]\d{2}[-/]\d{2}', ':date', title)
    title = re.sub(r'\d{2}:\d{2}(:\d{2})?', ':time', title)
    
    # 連続する数字
    title = re.sub(r'\d{3,}', ':num', title)
    
    # 空白正規化
    title = re.sub(r'\s+', ' ', title).strip()
    
    # 最大100文字
    if len(title) > 100:
        title = title[:100]
    
    return title


def hash_uia_fingerprint(elements: List[Dict]) -> str:
    """UIA 要素リストからフィンガープリントを生成
    
    Args:
        elements: UIA 要素のリスト（AutomationId, ControlType 等を含む）
    
    Returns:
        16文字のハッシュ
    """
    if not elements:
        return "unknown"
    
    # 安定した識別子のみを使用
    stable_parts = []
    for elem in elements[:5]:  # 代表的な5要素まで
        auto_id = elem.get('AutomationId', '')
        ctrl_type = elem.get('ControlType', '')
        if auto_id:
            stable_parts.append(f"{ctrl_type}:{auto_id}")
    
    if not stable_parts:
        return "unknown"
    
    fingerprint = "|".join(stable_parts)
    return hashlib.sha256(fingerprint.encode()).hexdigest()[:16]


def make_screen_key(
    process: str,
    win_class: str,
    url: Optional[str] = None,
    uia_elements: Optional[List[Dict]] = None,
    title: Optional[str] = None
) -> str:
    """screen_key を生成（優先順位: URL > UIA > title）
    
    形式: process|win_class|identifier
    
    Args:
        process: プロセス名（例: chrome.exe）
        win_class: ウィンドウクラス（例: chrome_widgetwin_1）
        url: ブラウザURL（CDP等で取得可能な場合）
        uia_elements: UIA 要素リスト（ネイティブアプリ向け）
        title: ウィンドウタイトル（フォールバック）
    
    Returns:
        screen_key 文字列
    """
    # プロセス名とクラス名を小文字化
    process = (process or "unknown").lower()
    win_class = (win_class or "unknown").lower()
    
    # 優先順位に従って identifier を決定
    if url:
        identifier = normalize_url(url)
    elif uia_elements:
        identifier = hash_uia_fingerprint(uia_elements)
    elif title:
        identifier = normalize_title(title)
    else:
        identifier = "unknown"
    
    return f"{process}|{win_class}|{identifier}"


# ============================================================
# CircuitBreaker 統合
# ============================================================

def get_circuit_breaker():
    """CircuitBreaker インスタンスを取得
    
    Returns:
        CircuitBreaker or None（インポート失敗時）
    """
    try:
        from circuit_breaker import CircuitBreaker
        return CircuitBreaker()
    except ImportError:
        # 同ディレクトリからのインポートを試行
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "circuit_breaker",
                Path(__file__).parent / "circuit_breaker.py"
            )
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module.CircuitBreaker()
        except Exception:
            pass
    return None


# エクスポート
__all__ = [
    'get_learning_client',
    'report_action_outcome',
    'check_risks',
    'get_best_locators',
    # v2.0 追加
    'normalize_url',
    'normalize_title',
    'hash_uia_fingerprint',
    'make_screen_key',
    'get_circuit_breaker',
]
