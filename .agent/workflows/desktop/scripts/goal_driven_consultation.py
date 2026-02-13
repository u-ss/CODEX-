# -*- coding: utf-8 -*-
"""
Desktop Control v5.1.0 - Goal-Driven ChatGPT Consultation

目標駆動型のChatGPT相談ループを実装。
v5.1.0: AdaptiveSelector・FSM統合、Antigravity委譲型に改修

使用モード:
1. single: 1往復のみ実行し、結果をJSONで返却（Antigravity委譲型）
2. loop: 従来の自動ループ（デフォルト）
"""

import json
import sys
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from playwright.sync_api import sync_playwright

# パスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.chatgpt.adaptive_selector import AdaptiveSelector, FALLBACK_SELECTORS
from integrations.chatgpt.generation_fsm import (
    wait_for_generation_sync,
    GenState,
    ChatGPTWaitConfig,
)
from integrations.chatgpt.state_monitor import (
    ChatGPTStateMonitor,
    ChatGPTState,
    StateSnapshot,
    SSVerifier,
)
from integrations.chatgpt.action_verifier import ActionVerifier, QWENClient, VerificationResult

CDP_PORT = 9223
USE_VERIFICATION = True  # DOM/URL検証を有効化
USE_QWEN_REASONING = False  # QWEN推論無効化（Antigravityが制御）
USE_STATE_MONITOR = True  # リアルタイム監視を有効化
# 注: singleモード推奨。loopモードは非推奨。


@dataclass
class ConsultationContext:
    """相談コンテキスト（目標・蓄積・未解決論点を管理）"""
    goal: str                                    # 相談目標
    max_rallies: int = 5                         # 最大ラリー数
    current_rally: int = 0                       # 現在のラリー番号
    history: list = field(default_factory=list) # {prompt, response} のリスト
    learned: list = field(default_factory=list) # これまでに分かったこと
    open_questions: list = field(default_factory=list)  # 未解決の論点
    goal_satisfied: bool = False                 # 目標達成フラグ
    
    def add_exchange(self, prompt: str, response: str) -> None:
        """1往復分の対話を蓄積"""
        self.history.append({
            "rally": self.current_rally,
            "prompt": prompt,
            "response": response,
            "timestamp": datetime.now().isoformat()
        })
    
    def summary_for_next_question(self) -> str:
        """次の質問生成用のコンテキストサマリ"""
        lines = [
            f"## 相談目標\n{self.goal}",
            f"\n## これまでに分かったこと ({len(self.learned)}件)"
        ]
        for i, item in enumerate(self.learned, 1):
            lines.append(f"{i}. {item}")
        
        lines.append(f"\n## 未解決の論点 ({len(self.open_questions)}件)")
        for i, q in enumerate(self.open_questions, 1):
            lines.append(f"{i}. {q}")
        
        lines.append(f"\n## 直近の回答（ラリー{self.current_rally}）")
        if self.history:
            lines.append(self.history[-1]["response"][:500] + "...")
        
        return "\n".join(lines)


@dataclass
class SingleQueryResult:
    """単発クエリの結果（Antigravity委譲用）"""
    success: bool
    question: str
    response: str
    error: Optional[str]
    chat_url: str
    elapsed_ms: int
    fsm_state: str
    verification: Optional[dict] = None  # SS検証結果


def open_new_chat(page, selector: AdaptiveSelector) -> bool:
    """
    新規チャットを開く（v5.2: URL優先）
    
    優先順位:
    1. URL直接遷移（最も確実）
    2. サイドバーリンク
    3. ボタンクリック
    """
    print("Opening new chat...")
    
    # 既に新規チャット画面なら何もしない
    if "/c/" not in page.url and "chatgpt.com" in page.url:
        print("Already on new chat page")
        return True
    
    # 方法1: URL直接遷移（最も確実）
    print("[NewChat] Navigating via URL...")
    try:
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        
        if "/c/" not in page.url:
            print("[NewChat] Success via URL navigation")
            return True
    except Exception as e:
        print(f"[NewChat] URL navigation failed: {e}")
    
    # 方法2: モデルパラメータ付きURL（リダイレクト対策）
    if "/c/" in page.url:
        print("[NewChat] Trying with model parameter...")
        try:
            page.goto("https://chatgpt.com/?model=gpt-4", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            
            if "/c/" not in page.url:
                print("[NewChat] Success via model URL")
                return True
        except Exception as e:
            print(f"[NewChat] Model URL failed: {e}")
    
    # 方法3: 新規チャットボタン（フォールバック）
    new_chat_result = selector.discover_new_chat_button()
    if new_chat_result.selector and new_chat_result.method != "fallback":
        try:
            btn = page.locator(new_chat_result.selector).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=5000)
                page.wait_for_timeout(2000)
                
                if "/c/" not in page.url:
                    print(f"[NewChat] Success via button ({new_chat_result.method})")
                    return True
        except Exception as e:
            print(f"[NewChat] Button click failed: {e}")
    
    # 結果判定
    success = "/c/" not in page.url
    print(f"[NewChat] Final result: {'success' if success else 'failed'}")
    return success


def ask_chatgpt_once_fsm(page, question: str) -> dict:
    """
    ChatGPTに1往復の質問を行う（FSM統合版）
    
    v5.1.1: 複数の確認手段で送信成功を判定
    1. DOM確認（メッセージ数の増加）
    2. URL確認（/c/ で会話ID付与）
    3. SS + QWEN分析（視覚的確認）
    """
    import time
    import mss
    import mss.tools
    import base64
    import requests
    start_time = time.time()
    
    # === 確認手段1: DOM確認 ===
    def verify_by_dom(page, initial_count: int) -> dict:
        """メッセージ数の増加で確認"""
        try:
            current_count = page.locator(FALLBACK_SELECTORS["assistant_message"]).count()
            if current_count > initial_count:
                return {"success": True, "method": "dom", "reason": f"メッセージ数増加: {initial_count}→{current_count}"}
            return {"success": False, "method": "dom", "reason": f"メッセージ数変化なし: {current_count}"}
        except Exception as e:
            return {"success": False, "method": "dom", "reason": str(e)}
    
    # === 確認手段2: URL確認 ===
    def verify_by_url(page, initial_url: str) -> dict:
        """URL変化（会話ID付与）で確認"""
        try:
            current_url = page.url
            if "/c/" in current_url and "/c/" not in initial_url:
                return {"success": True, "method": "url", "reason": f"会話ID付与: {current_url}"}
            if "/c/" in current_url:
                return {"success": True, "method": "url", "reason": "既存会話継続"}
            return {"success": False, "method": "url", "reason": "URL変化なし"}
        except Exception as e:
            return {"success": False, "method": "url", "reason": str(e)}
    
    # === SS + QWEN検証は無効化（qwen3:14bはテキスト専用モデル） ===
    # verify_by_qwenとcapture_both_monitorsは削除済み
    
    # === 合議判定（DOM + URL のみ） ===
    def aggregate_verification(results: list) -> dict:
        """複数確認結果を合議（DOM + URL）"""
        successes = [r for r in results if r.get("success")]
        total = len(results)
        passed = len(successes)
        
        # 2つ中1つ以上で成功（SS検証なし）
        final_success = passed >= 1
        
        return {
            "success": final_success,
            "passed": passed,
            "total": total,
            "methods": [r["method"] for r in successes],
            "details": results,
        }
    
    try:
        selector = AdaptiveSelector(page)
        
        # 送信前の状態を記録
        try:
            initial_msg_count = page.locator(FALLBACK_SELECTORS["assistant_message"]).count()
        except Exception:
            initial_msg_count = 0  # 新規チャットでは0
        initial_url = page.url
        
        # 入力欄待機
        textarea = page.locator("#prompt-textarea, textarea[placeholder*='Message']")
        textarea.wait_for(state="visible", timeout=15000)
        
        # 質問送信
        textarea.fill(question)
        page.wait_for_timeout(500)
        textarea.press("Enter")
        print("[SEND] Message sent, waiting for response...")
        
        page.wait_for_timeout(1000)
        
        # === v5.2: StateMonitorでリアルタイム監視 ===
        if USE_STATE_MONITOR:
            print("[Monitor] Starting real-time monitoring...")
            monitor = ChatGPTStateMonitor(
                page,
                poll_interval_ms=500,
                stable_window_ms=2000,
            )
            success_gen, final_snapshot = monitor.wait_for_generation_complete(timeout_ms=120000)
            
            if not success_gen:
                elapsed = int((time.time() - start_time) * 1000)
                verification = None
                if USE_VERIFICATION:
                    print("[VERIFY] Generation failed, running verification...")
                    results = [verify_by_dom(page, initial_msg_count), verify_by_url(page, initial_url)]
                    # SS + QWEN検証削除（qwen3:14bはテキスト専用）
                    verification = aggregate_verification(results)
                    print(f"[VERIFY] Aggregate: {verification['passed']}/{verification['total']} passed")
                return {
                    "success": False, "response": "", 
                    "error": f"Generation failed (state={final_snapshot.state.value if final_snapshot else 'unknown'})",
                    "elapsed_ms": elapsed, "fsm_state": final_snapshot.state.value if final_snapshot else "error", 
                    "verification": verification, "monitor_state": final_snapshot.state.value if final_snapshot else None,
                }
        else:
            # レガシーFSM（フォールバック）
            print("[FSM] Waiting for generation (legacy)...")
            cfg = ChatGPTWaitConfig(stable_window_ms=2500, stall_timeout_ms=45000)
            success_gen, fsm = wait_for_generation_sync(page, cfg)
            
            if not success_gen:
                elapsed = int((time.time() - start_time) * 1000)
                verification = None
                if USE_VERIFICATION:
                    print("[VERIFY] Generation failed, running verification...")
                    results = [verify_by_dom(page, initial_msg_count), verify_by_url(page, initial_url)]
                    # SS + QWEN検証削除（qwen3:14bはテキスト専用）
                    verification = aggregate_verification(results)
                    print(f"[VERIFY] Aggregate: {verification['passed']}/{verification['total']} passed")
                return {
                    "success": False, "response": "", "error": f"Generation failed (state={fsm.state.value})",
                    "elapsed_ms": elapsed, "fsm_state": fsm.state.value, "verification": verification,
                }
        
        page.wait_for_timeout(500)  # 短い安定待機（StateMonitorが安定判定済み）
        
        # 回答取得
        response_locator = page.locator(FALLBACK_SELECTORS["assistant_message"]).last
        response_text = response_locator.inner_text()
        elapsed = int((time.time() - start_time) * 1000)
        
        # === 複数確認手段で検証 ===
        verification = None
        if USE_VERIFICATION:
            print("[VERIFY] Running multi-method verification...")
            results = []
            
            # 1. DOM確認
            dom_result = verify_by_dom(page, initial_msg_count)
            results.append(dom_result)
            print(f"  [DOM] {dom_result['success']}: {dom_result['reason']}")
            
            # 2. URL確認
            url_result = verify_by_url(page, initial_url)
            results.append(url_result)
            print(f"  [URL] {url_result['success']}: {url_result['reason']}")
            
            # SS + QWEN検証削除（qwen3:14bはテキスト専用モデルのため）
            
            # 合議
            verification = aggregate_verification(results)
            print(f"[VERIFY] Final: {verification['passed']}/{verification['total']} passed → {'SUCCESS' if verification['success'] else 'FAILED'}")
        
        # 状態取得（StateMonitor or FSM）
        if USE_STATE_MONITOR:
            state_value = final_snapshot.state.value if final_snapshot else "complete"
        else:
            state_value = fsm.state.value if 'fsm' in dir() else "complete"
        
        return {
            "success": True, "response": response_text, "error": None,
            "elapsed_ms": elapsed, "fsm_state": state_value, "verification": verification,
        }
        
    except Exception as e:
        elapsed = int((time.time() - start_time) * 1000)
        return {
            "success": False,
            "response": "",
            "error": str(e),
            "elapsed_ms": elapsed,
            "fsm_state": "error",
        }


def run_single_query(question: str, open_new: bool = True, context_file: Optional[str] = None) -> SingleQueryResult:
    """
    単発クエリを実行（Antigravity委譲型）
    
    Antigravityがこの関数を呼び出し、結果を受け取って次の質問を決定する。
    
    Args:
        question: 送信する質問
        open_new: 新規チャットを開くか
        context_file: 前回のコンテキストファイル（オプション）
    
    Returns:
        SingleQueryResult: 結果
    """
    import time
    start_time = time.time()
    
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = browser.contexts[0]
        
        # ChatGPTページを探す
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        
        if not page:
            return SingleQueryResult(
                success=False,
                question=question,
                response="",
                error="ChatGPTページが見つかりません",
                chat_url="",
                elapsed_ms=int((time.time() - start_time) * 1000),
                fsm_state="error",
            )
        
        page.bring_to_front()
        
        # セッション確認
        if "/auth/login" in page.url:
            return SingleQueryResult(
                success=False,
                question=question,
                response="",
                error="セッション切れ：手動でログインしてください",
                chat_url=page.url,
                elapsed_ms=int((time.time() - start_time) * 1000),
                fsm_state="error",
            )
        
        selector = AdaptiveSelector(page)
        
        # 新規チャット
        if open_new:
            if not open_new_chat(page, selector):
                print("Warning: Could not confirm new chat opened")
        
        # 質問送信
        result = ask_chatgpt_once_fsm(page, question)
        
        elapsed = int((time.time() - start_time) * 1000)
        return SingleQueryResult(
            success=result["success"],
            question=question,
            response=result["response"],
            error=result["error"],
            chat_url=page.url,
            elapsed_ms=elapsed,
            fsm_state=result["fsm_state"],
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return SingleQueryResult(
            success=False,
            question=question,
            response="",
            error=str(e),
            chat_url="",
            elapsed_ms=int((time.time() - start_time) * 1000),
            fsm_state="error",
        )
    finally:
        p.stop()


def generate_initial_question(goal: str) -> str:
    """初期質問を生成"""
    return f"""あなたは専門家です。以下の目標について相談させてください。

## 相談目標
{goal}

## 質問
1. この目標を達成するために、まず何を考慮すべきですか？
2. 重要な設計判断や決定ポイントは何ですか？
3. 見落としやすい注意点はありますか？"""


def _fallback_question() -> str:
    """フォールバック用のテンプレート質問"""
    return """回答ありがとうございます。

前の回答で触れられた内容について、さらに具体的に教えてください。
特に実装面での詳細や、ベストプラクティスがあれば知りたいです。"""


def run_goal_driven_consultation(goal: str, max_rallies: int = 5) -> ConsultationContext:
    """
    目標駆動型の相談ループを実行（従来の自動ループモード）
    
    注意: このモードでは質問生成がテンプレートベースになる。
    より高品質な相談には single モードでAntigravity委譲を推奨。
    """
    ctx = ConsultationContext(goal=goal, max_rallies=max_rallies)
    
    print(f"=== Goal-Driven Consultation ===")
    print(f"Goal: {goal}")
    print(f"Max Rallies: {max_rallies}")
    print("=" * 40)
    
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
        context = browser.contexts[0]
        
        # ChatGPTページを探す
        page = None
        for pg in context.pages:
            if "chatgpt.com" in pg.url:
                page = pg
                break
        
        if not page:
            print("Error: ChatGPTページが見つかりません")
            return ctx
        
        print(f"Found ChatGPT page: {page.url}")
        page.bring_to_front()
        
        # セッション確認
        if "/auth/login" in page.url:
            print("Error: セッション切れ")
            return ctx
        
        selector = AdaptiveSelector(page)
        
        # === 新規チャットを開く ===
        if not open_new_chat(page, selector):
            print("Warning: Could not confirm new chat opened")
        
        print(f"Ready: {page.url}")
        
        # 初期質問生成
        current_question = generate_initial_question(goal)
        
        for rally_num in range(1, max_rallies + 1):
            ctx.current_rally = rally_num
            print(f"\n{'='*50}")
            print(f"Rally {rally_num}/{max_rallies}")
            print(f"{'='*50}")
            print(f"Question preview: {current_question[:100]}...")
            
            # 1. 質問送信・回答取得（FSM統合）
            result = ask_chatgpt_once_fsm(page, current_question)
            
            if not result["success"]:
                print(f"Error: {result['error']}")
                break
            
            response = result["response"]
            print(f"Response received ({len(response)} chars, FSM={result['fsm_state']})")
            
            # 2. 対話を蓄積
            ctx.add_exchange(current_question, response)
            
            # 3. 次の質問生成（v5.1.1: QWEN推論 or テンプレート）
            if rally_num >= max_rallies:
                ctx.goal_satisfied = True
                print("\n✅ 最大ラリー数到達")
                break
            
            # QWEN推論モード
            if USE_QWEN_REASONING:
                try:
                    print("[QWEN] Generating next question...")
                    qwen = QWENClient()
                    qwen_result = qwen.generate_next_question(
                        goal=goal,
                        conversation_history=ctx.history,
                        last_response=response,
                    )
                    
                    if qwen_result.get("success") and qwen_result.get("question"):
                        current_question = qwen_result["question"]
                        
                        # コンテキスト更新
                        if qwen_result.get("learned"):
                            ctx.learned.extend(qwen_result["learned"])
                        if qwen_result.get("open_questions"):
                            ctx.open_questions = qwen_result["open_questions"]
                        if qwen_result.get("goal_satisfied"):
                            ctx.goal_satisfied = True
                            print(f"\n✅ QWEN: 目標達成と判定")
                            break
                        
                        print(f"[QWEN] Next question: {current_question[:80]}...")
                        print(f"[QWEN] Reasoning: {qwen_result.get('reasoning', 'N/A')[:100]}")
                    else:
                        # フォールバック
                        print(f"[QWEN] Failed, using template: {qwen_result.get('error', 'Unknown')}")
                        current_question = _fallback_question()
                        
                except Exception as qe:
                    print(f"[QWEN] Error: {qe}, using template")
                    current_question = _fallback_question()
            else:
                # テンプレートモード（従来互換）
                current_question = _fallback_question()
                print("Next question generated (template-based)")
        
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # ログ保存
        save_consultation_log(ctx)
        p.stop()
    
    return ctx


def save_consultation_log(ctx: ConsultationContext) -> None:
    """相談ログを保存"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON
    json_file = log_dir / f"goal_consultation_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "goal": ctx.goal,
            "max_rallies": ctx.max_rallies,
            "completed_rallies": ctx.current_rally,
            "goal_satisfied": ctx.goal_satisfied,
            "learned": ctx.learned,
            "open_questions": ctx.open_questions,
            "history": ctx.history
        }, f, ensure_ascii=False, indent=2)
    print(f"\nJSON: {json_file}")
    
    # Markdown
    md_file = log_dir / f"goal_consultation_{timestamp}.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(f"# 目標駆動型相談ログ\n\n")
        f.write(f"**日時**: {timestamp}\n")
        f.write(f"**目標**: {ctx.goal}\n")
        f.write(f"**ラリー数**: {ctx.current_rally}/{ctx.max_rallies}\n")
        f.write(f"**目標達成**: {'✅' if ctx.goal_satisfied else '❌'}\n\n")
        
        for entry in ctx.history:
            f.write(f"## Rally {entry['rally']}\n\n")
            f.write(f"### 質問\n{entry['prompt']}\n\n")
            f.write(f"### 回答\n{entry['response']}\n\n")
            f.write("---\n\n")
    print(f"Markdown: {md_file}")


def save_single_result(result: SingleQueryResult) -> Path:
    """単発クエリ結果を保存 + セッションログに追記"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 個別ログ（従来通り）
    json_file = log_dir / f"single_query_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(asdict(result), f, ensure_ascii=False, indent=2)
    
    # === セッションログに追記 ===
    session_id = extract_session_id(result.chat_url)
    if session_id:
        append_to_session_log(log_dir, session_id, result, timestamp)
    
    return json_file


def extract_session_id(chat_url: str) -> Optional[str]:
    """chat_urlからセッションIDを抽出"""
    # https://chatgpt.com/c/6985c742-92ac-83a3-b88b-5fde66c1f84f
    import re
    match = re.search(r'/c/([a-f0-9-]+)', chat_url)
    return match.group(1) if match else None


def append_to_session_log(log_dir: Path, session_id: str, result: SingleQueryResult, timestamp: str) -> None:
    """セッションログに追記"""
    session_file = log_dir / f"session_{session_id}.json"
    
    # 既存ログを読み込み
    if session_file.exists():
        with open(session_file, "r", encoding="utf-8") as f:
            session_log = json.load(f)
    else:
        session_log = {
            "session_id": session_id,
            "chat_url": result.chat_url,
            "created_at": timestamp,
            "entries": []
        }
    
    # 新しいエントリを追加
    session_log["entries"].append({
        "timestamp": timestamp,
        "question": result.question,
        "response": result.response,
        "success": result.success,
        "error": result.error,
        "elapsed_ms": result.elapsed_ms,
        "fsm_state": result.fsm_state,
    })
    session_log["updated_at"] = timestamp
    session_log["total_entries"] = len(session_log["entries"])
    
    # 保存
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session_log, f, ensure_ascii=False, indent=2)
    
    print(f"Session log updated: {session_file} (entries: {len(session_log['entries'])})")


def main():
    parser = argparse.ArgumentParser(description="目標駆動型ChatGPT相談")
    parser.add_argument("--goal", "-g", help="相談目標（loopモード用）")
    parser.add_argument("--question", "-q", help="質問（singleモード用）")
    parser.add_argument("--max-rallies", "-n", type=int, default=5, help="最大ラリー数")
    parser.add_argument("--mode", choices=["single", "loop"], default="single",
                        help="実行モード: single=1往復(推奨), loop=自動ループ(非推奨)")
    parser.add_argument("--no-new-chat", action="store_true", help="新規チャットを開かない")
    parser.add_argument("--context-file", help="前回コンテキストファイル")
    args = parser.parse_args()
    
    if args.mode == "single":
        # 単発モード（Antigravity委譲型）
        if not args.question:
            print("Error: --question is required for single mode")
            sys.exit(1)
        
        print(f"=== Single Query Mode ===")
        print(f"Question: {args.question[:100]}...")
        
        result = run_single_query(
            question=args.question,
            open_new=not args.no_new_chat,
            context_file=args.context_file,
        )
        
        # 結果を保存
        log_file = save_single_result(result)
        
        # 結果を標準出力にも出力（Antigravityが読み取る用）
        print(f"\n=== Result ===")
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        print(f"\nLog saved: {log_file}")
        
        sys.exit(0 if result.success else 1)
    
    else:
        # ループモード（非推奨・後方互換）
        print("⚠️  警告: loopモードは非推奨です。singleモードを使用してください。")
        print("    Antigravityが質問生成・飽和判定を制御するsingleモードを推奨します。")
        print("")
        
        if not args.goal:
            print("Error: --goal is required for loop mode")
            sys.exit(1)
        
        ctx = run_goal_driven_consultation(args.goal, args.max_rallies)
        
        print(f"\n=== 相談完了 ===")
        print(f"Goal: {ctx.goal}")
        print(f"Rallies: {ctx.current_rally}/{ctx.max_rallies}")
        print(f"Goal Satisfied: {ctx.goal_satisfied}")


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        main()
    else:
        raise SystemExit(run_logged_main("desktop", "goal_driven_consultation", main))
