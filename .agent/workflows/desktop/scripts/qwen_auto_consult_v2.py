# -*- coding: utf-8 -*-
"""
QWEN Auto Consultation Loop v2.1.0

Phase制ステートマシン + DoD駆動の自律ChatGPT対話システム。
GPT-5.2との相談結果に基づく抜本的な設計改善版。

v2.1.0:
- 新規ブラウザセッション起動による並列実行対応
- --use-cdp: 既存CDP接続を使用（オプション）
- --session-file: ログイン状態の保存/復元

v2.0.0:
- Phase A-E制ステートマシン（Alignment→Options→Critique→Decision→DoD）
- DoD（Definition of Done）駆動の収束判定
- 質問候補生成+採点+選抜
- Claims正規化パイプライン

使用例:
# 並列実行可能モード（デフォルト）
python qwen_auto_consult_v2.py --goal "システム設計の壁打ち" --theme design

# 既存CDPブラウザを使用
python qwen_auto_consult_v2.py --goal "システム設計の壁打ち" --use-cdp
"""

import json
import sys
import argparse
import time
import requests
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from playwright.sync_api import sync_playwright

# パスを追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from integrations.chatgpt.adaptive_selector import AdaptiveSelector, FALLBACK_SELECTORS
from integrations.chatgpt.state_monitor import ChatGPTStateMonitor

CDP_PORT = 9223
QWEN_MODEL = "qwen3:14b"
OLLAMA_URL = "http://localhost:11434"

# v2.1.0: セッション保存先
DEFAULT_SESSION_FILE = Path.home() / ".antigravity" / "chatgpt_session.json"


# ============================================================
# Phase定義とDoD
# ============================================================

PHASE_CONFIG = {
    "A": {
        "name": "Alignment",
        "description": "問題・制約・前提の確定",
        "max_rallies": 5,
        "dod_ids": ["A1_problem", "A2_metrics", "A3_constraints", "A4_assumptions", "A5_scope"],
    },
    "B": {
        "name": "Broad options",
        "description": "選択肢の発散・比較軸整備",
        "max_rallies": 10,
        "dod_ids": ["B1_options_3plus", "B2_tradeoffs", "B3_criteria_5plus"],
    },
    "C": {
        "name": "Critique",
        "description": "リスク・検証計画・収束準備",
        "max_rallies": 10,
        "dod_ids": ["C1_top_candidates", "C2_risks_5plus", "C3_experiments", "C4_open_questions_5max"],
    },
    "D": {
        "name": "Decision & Plan",
        "description": "推奨案・実行計画・フォールバック",
        "max_rallies": 8,
        "dod_ids": ["D1_recommended", "D2_rationale", "D3_rollout", "D4_fallback"],
    },
    "E": {
        "name": "DoD package",
        "description": "最終サマリー・残課題整理",
        "max_rallies": 5,
        "dod_ids": ["E1_summary", "E2_remaining_5max"],
    },
}

PHASE_ORDER = ["A", "B", "C", "D", "E"]


@dataclass
class ConsultConfig:
    """相談設定 v2.1"""
    goal: str
    theme: str = "design"  # design / debug / research
    initial_question: Optional[str] = None
    required_topics: List[str] = field(default_factory=list)
    max_rallies: int = 50
    stop_on_no_progress: int = 3  # N回連続でDoD進捗なしなら停止
    # v2.1.0: ブラウザ設定
    use_cdp: bool = False  # True: 既存CDP接続、False: 新規ブラウザ
    cdp_port: int = 9223
    session_file: Optional[Path] = None
    save_session: bool = False


@dataclass
class ConsultState:
    """相談状態 v2.0"""
    # Phase管理
    phase: str = "A"
    phase_rally_count: int = 0
    total_rally: int = 0
    
    # DoD追跡
    dod_satisfied: List[str] = field(default_factory=list)
    dod_missing: List[str] = field(default_factory=lambda: PHASE_CONFIG["A"]["dod_ids"].copy())
    dod_evidence: List[Dict] = field(default_factory=list)
    
    # 成果物
    artifacts: Dict = field(default_factory=lambda: {
        "problem_statement": None,
        "success_metrics": [],
        "constraints": [],
        "assumptions": [],
        "options": [],
        "decision_criteria": [],
        "risks": [],
        "experiments": [],
        "recommended_option": None,
        "rollout_plan": [],
        "fallback_plan": [],
        "final_summary": None,
    })
    
    # Claims（正規化済み）
    claims: List[Dict] = field(default_factory=list)
    
    # 対話履歴
    open_questions: List[str] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    
    # 進捗追跡
    no_progress_count: int = 0
    saturated: bool = False
    saturation_reason: str = ""


# ============================================================
# QWENヘルパー
# ============================================================

def qwen_generate(prompt: str, temperature: float = 0.4, timeout: int = 180) -> str:
    """QWENでテキスト生成"""
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": QWEN_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature}
            },
            timeout=timeout,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
        return ""
    except Exception as e:
        print(f"[QWEN] Error: {e}")
        return ""


def parse_json_response(raw: str) -> Dict:
    """QWENの出力からJSONを抽出"""
    try:
        # コードフェンス内JSON
        json_match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # フェンスなしJSON
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
    except Exception as e:
        print(f"[QWEN] JSON parse error: {e}")
    return {}


# ============================================================
# Phase制プロンプト生成
# ============================================================

COMMON_HEADER = """あなたは「設計壁打ち」自動運転の司令塔です。
仕事: (1)設計成果物(artifacts)を更新し、(2)DoD達成まで質問を組み立て、(3)フェーズ遷移を提案。

重要:
- 出力は必ずJSON形式のみ（```json ``` で囲む）
- 推測が必要なときは assumptions に「仮定」として置いて前進
- ChatGPTが質問した場合: わかる範囲で回答し、不明点は仮定で進める
- next_question は「最も価値の高い1つ」に絞る
"""

OUTPUT_SCHEMA = """{
  "phase": "A|B|C|D|E",
  "phase_action": "stay|advance|regress",
  "phase_transition_reason": "string or null",
  "dod_satisfied": ["満たしたDoD ID"],
  "dod_missing": ["未達のDoD ID"],
  "artifact_patch": {"更新するartifactキー": "新しい値"},
  "new_claims": [{"text": "知見", "topic": "関連トピック"}],
  "open_questions": ["未解決の質問"],
  "next_question": "ChatGPTへの次の質問"
}"""


def build_phase_prompt(
    config: ConsultConfig,
    state: ConsultState,
    last_response: str,
) -> str:
    """フェーズ別プロンプトを構築"""
    
    phase_info = PHASE_CONFIG[state.phase]
    
    # 履歴サマリー（直近3件）
    history_summary = ""
    for entry in state.history[-3:]:
        history_summary += f"--- Rally {entry['rally']} ---\n"
        history_summary += f"Q: {entry.get('question', '')[:150]}...\n"
        history_summary += f"A: {entry.get('response', '')[:300]}...\n\n"
    
    prompt = f"""{COMMON_HEADER}

# Phase
現在フェーズ: {state.phase}（{phase_info['name']}）
フェーズ説明: {phase_info['description']}
フェーズ内ラリー: {state.phase_rally_count}/{phase_info['max_rallies']}

# Goal
{config.goal}

# Required Topics
{json.dumps(config.required_topics, ensure_ascii=False)}

# Current Artifacts
{json.dumps(state.artifacts, ensure_ascii=False, indent=2)}

# DoD Status
- 達成済み: {state.dod_satisfied}
- 未達: {state.dod_missing}

# Phase {state.phase} DoD定義
{phase_info['dod_ids']}

# Recent History
{history_summary}

# Latest ChatGPT Response
{last_response[:2000]}

# Task
1) last_response から artifacts を更新する（artifact_patchで差分を返す）
2) DoDの達成/未達を判定し、dod_satisfied/dod_missingを更新
3) フェーズ遷移が必要か判定（advance/stay/regress）
4) 次にChatGPTへ投げる最重要の質問を1つ生成

# Output JSON Schema
```json
{OUTPUT_SCHEMA}
```
"""
    return prompt


# ============================================================
# Phase制ステップ実行
# ============================================================

def qwen_step(
    config: ConsultConfig,
    state: ConsultState,
    last_response: str,
) -> Dict[str, Any]:
    """QWENに1ステップ分析させる（Phase制）"""
    
    prompt = build_phase_prompt(config, state, last_response)
    raw = qwen_generate(prompt)
    result = parse_json_response(raw)
    
    if not result:
        # フォールバック
        return {
            "phase": state.phase,
            "phase_action": "stay",
            "phase_transition_reason": None,
            "dod_satisfied": state.dod_satisfied,
            "dod_missing": state.dod_missing,
            "artifact_patch": {},
            "new_claims": [],
            "open_questions": state.open_questions,
            "next_question": "前の回答について、もう少し具体的に教えてください。",
        }
    
    return result


def apply_step_result(state: ConsultState, result: Dict) -> bool:
    """ステップ結果をstateに適用。進捗があればTrueを返す"""
    
    had_progress = False
    
    # DoD更新
    new_satisfied = result.get("dod_satisfied", [])
    for dod_id in new_satisfied:
        if dod_id not in state.dod_satisfied:
            state.dod_satisfied.append(dod_id)
            had_progress = True
    
    state.dod_missing = result.get("dod_missing", state.dod_missing)
    
    # Artifacts更新
    patch = result.get("artifact_patch", {})
    for key, value in patch.items():
        if key in state.artifacts:
            state.artifacts[key] = value
            had_progress = True
    
    # Claims追加
    new_claims = result.get("new_claims", [])
    for claim in new_claims:
        # 重複チェック（簡易版）
        if not any(c.get("text") == claim.get("text") for c in state.claims):
            state.claims.append({
                "text": claim.get("text", ""),
                "topic": claim.get("topic", ""),
                "rally": state.total_rally,
                "phase": state.phase,
            })
            had_progress = True
    
    # Open questions更新
    state.open_questions = result.get("open_questions", state.open_questions)
    
    # フェーズ遷移
    phase_action = result.get("phase_action", "stay")
    if phase_action == "advance":
        current_idx = PHASE_ORDER.index(state.phase)
        if current_idx < len(PHASE_ORDER) - 1:
            state.phase = PHASE_ORDER[current_idx + 1]
            state.phase_rally_count = 0
            state.dod_missing = PHASE_CONFIG[state.phase]["dod_ids"].copy()
            print(f"  📍 Phase遷移: {PHASE_ORDER[current_idx]} → {state.phase}")
            had_progress = True
    elif phase_action == "regress":
        current_idx = PHASE_ORDER.index(state.phase)
        if current_idx > 0:
            state.phase = PHASE_ORDER[current_idx - 1]
            state.phase_rally_count = 0
            print(f"  ⚠️ Phase後退: {PHASE_ORDER[current_idx]} → {state.phase}")
    
    return had_progress


# ============================================================
# ChatGPT通信
# ============================================================

def ask_chatgpt(page, question: str) -> Dict[str, Any]:
    """ChatGPTに1往復の質問"""
    start_time = time.time()
    
    try:
        # 入力欄
        textarea = page.locator("#prompt-textarea, textarea[placeholder*='Message']")
        textarea.wait_for(state="visible", timeout=15000)
        
        # 送信
        textarea.fill(question)
        page.wait_for_timeout(500)
        textarea.press("Enter")
        page.wait_for_timeout(1000)
        
        # StateMonitorで監視
        monitor = ChatGPTStateMonitor(page, poll_interval_ms=500, stable_window_ms=2000)
        success, snapshot = monitor.wait_for_generation_complete(timeout_ms=180000)
        
        if not success:
            return {"success": False, "response": "", "error": "Generation timeout"}
        
        page.wait_for_timeout(500)
        
        # 回答取得
        response_locator = page.locator(FALLBACK_SELECTORS["assistant_message"]).last
        response_text = response_locator.inner_text()
        
        return {
            "success": True,
            "response": response_text,
            "elapsed_ms": int((time.time() - start_time) * 1000),
        }
        
    except Exception as e:
        return {
            "success": False,
            "response": "",
            "error": str(e),
            "elapsed_ms": int((time.time() - start_time) * 1000)
        }


def open_new_chat(page) -> bool:
    """新規チャットを開く"""
    print("[NewChat] Navigating via URL...")
    try:
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        return "/c/" not in page.url
    except Exception as e:
        print(f"[NewChat] Failed: {e}")
        return False


# ============================================================
# メインループ
# ============================================================

def run_consultation_v2(config: ConsultConfig) -> ConsultState:
    """Phase制自動対話ループ実行"""
    state = ConsultState()
    
    print(f"\n{'='*60}")
    print(f"🤖 QWEN Auto Consultation v2.1.0 (Phase制)")
    print(f"{'='*60}")
    print(f"Goal: {config.goal}")
    print(f"Theme: {config.theme}")
    print(f"Max Rallies: {config.max_rallies}")
    print(f"Mode: {'CDP接続' if config.use_cdp else '新規ブラウザ'}")
    print(f"{'='*60}\n")
    
    p = sync_playwright().start()
    browser = None
    context = None
    should_close_browser = False
    
    try:
        if config.use_cdp:
            # v2.0互換: 既存CDP接続を使用
            print(f"[Browser] Connecting to CDP: http://127.0.0.1:{config.cdp_port}")
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{config.cdp_port}")
            context = browser.contexts[0]
            
            # ChatGPTページを探す
            page = None
            for pg in context.pages:
                if "chatgpt.com" in pg.url:
                    page = pg
                    break
            
            if not page:
                print("Error: ChatGPTページが見つかりません")
                return state
            
            print(f"Found ChatGPT: {page.url}")
            page.bring_to_front()
        else:
            # v2.1.0: 既存Chromeプロファイルを使って新規ウィンドウを起動
            # これにより並列実行が可能かつログイン状態を保持
            import os
            import tempfile
            import shutil
            
            should_close_browser = True
            
            # Chromeのユーザーデータディレクトリを取得
            chrome_user_data = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")
            
            print(f"[Browser] Launching with Chrome profile: {chrome_user_data}")
            
            # ブラウザ起動オプション
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            
            # 一時ディレクトリに必要なCookie/ストレージをコピー
            temp_profile = Path(tempfile.mkdtemp(prefix="qwen_chrome_"))
            print(f"[Browser] Creating temp profile: {temp_profile}")
            
            # Default プロファイルのCookie等をコピー
            default_profile = Path(chrome_user_data) / "Default"
            if default_profile.exists():
                for item in ["Cookies", "Login Data", "Web Data", "Local Storage", "Session Storage"]:
                    src = default_profile / item
                    if src.exists():
                        dst = temp_profile / "Default" / item
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            if src.is_dir():
                                shutil.copytree(src, dst, dirs_exist_ok=True)
                            else:
                                shutil.copy2(src, dst)
                        except Exception as e:
                            print(f"[Browser] Warning: Could not copy {item}: {e}")
            
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(temp_profile),
                headless=False,
                channel="chrome",
                args=launch_args,
            )
            browser = context  # persistent_contextはcontextとbrowserが同一
            
            page = context.new_page()
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            
            # ログイン確認（コピーしたCookieで自動ログインできているはず）
            if "auth" in page.url or "login" in page.url:
                print("\n⚠️ ChatGPTにログインしてください。ログイン後、Enterを押してください...")
                input()
                page.wait_for_timeout(2000)
        
        # 新規チャット
        open_new_chat(page)
        
        # 初期質問
        current_question = config.initial_question or f"""設計相談をお願いします。

## 目標
{config.goal}

## カバーしたいトピック
{', '.join(config.required_topics) if config.required_topics else 'なし（自由に提案してください）'}

## 質問
1. この目標を達成するために、まず何を明確にすべきですか？
2. 問題の制約や前提として考慮すべきことは何ですか？"""
        
        # メインループ
        for rally in range(1, config.max_rallies + 1):
            state.total_rally = rally
            state.phase_rally_count += 1
            
            phase_info = PHASE_CONFIG[state.phase]
            
            print(f"\n{'='*50}")
            print(f"📍 Rally {rally}/{config.max_rallies} | Phase {state.phase}: {phase_info['name']}")
            print(f"{'='*50}")
            print(f"Question: {current_question[:80]}...")
            
            # ChatGPTに質問
            result = ask_chatgpt(page, current_question)
            
            if not result["success"]:
                print(f"❌ Error: {result.get('error', 'Unknown')}")
                break
            
            response = result["response"]
            print(f"✅ Response: {len(response)} chars ({result['elapsed_ms']}ms)")
            
            # 履歴に追加
            state.history.append({
                "rally": rally,
                "phase": state.phase,
                "question": current_question,
                "response": response,
                "timestamp": datetime.now().isoformat()
            })
            
            # QWEN分析（Phase制）
            print("[QWEN] Analyzing with phase-based prompt...")
            step_result = qwen_step(config, state, response)
            
            # 結果適用
            had_progress = apply_step_result(state, step_result)
            
            # 進捗表示
            print(f"  📊 DoD: {len(state.dod_satisfied)}/{len(state.dod_satisfied) + len(state.dod_missing)}")
            print(f"  📊 Claims: {len(state.claims)}")
            print(f"  📊 Phase進捗: {had_progress}")
            
            # 飽和判定
            # 1. Phase E完了
            if state.phase == "E" and len(state.dod_missing) == 0:
                state.saturated = True
                state.saturation_reason = "dod_complete"
                print(f"\n✅ 飽和点到達: DoD完了")
                break
            
            # 2. Phaseラリー上限
            if state.phase_rally_count >= phase_info["max_rallies"]:
                print(f"  ⚠️ Phase {state.phase} ラリー上限到達、強制遷移")
                current_idx = PHASE_ORDER.index(state.phase)
                if current_idx < len(PHASE_ORDER) - 1:
                    state.phase = PHASE_ORDER[current_idx + 1]
                    state.phase_rally_count = 0
                    state.dod_missing = PHASE_CONFIG[state.phase]["dod_ids"].copy()
            
            # 3. 進捗なし連続
            if not had_progress:
                state.no_progress_count += 1
                print(f"  ⚠️ No progress ({state.no_progress_count}/{config.stop_on_no_progress})")
                if state.no_progress_count >= config.stop_on_no_progress:
                    state.saturated = True
                    state.saturation_reason = "no_progress"
                    print(f"\n✅ 飽和点到達: {config.stop_on_no_progress}回連続進捗なし")
                    break
            else:
                state.no_progress_count = 0
            
            # 次の質問
            current_question = step_result.get("next_question", "続きをお願いします。")
            print(f"  ➡️ Next: {current_question[:60]}...")
        
        # 最大ラリー到達
        if not state.saturated and state.total_rally >= config.max_rallies:
            state.saturated = True
            state.saturation_reason = "max_rallies"
            print(f"\n⚠️ 最大ラリー数到達 ({config.max_rallies})")
        
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # v2.1.0: セッション保存
        if config.save_session and context and not config.use_cdp:
            session_file = config.session_file or DEFAULT_SESSION_FILE
            session_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                context.storage_state(path=str(session_file))
                print(f"[Session] Saved to: {session_file}")
            except Exception as e:
                print(f"[Session] Failed to save: {e}")
        
        save_consultation_log_v2(config, state)
        
        # v2.1.0: 新規ブラウザの場合はクローズ
        if should_close_browser and browser:
            try:
                browser.close()
            except:
                pass
        
        p.stop()
    
    return state


# ============================================================
# ログ保存
# ============================================================

def save_consultation_log_v2(config: ConsultConfig, state: ConsultState) -> Path:
    """相談ログを保存（v2形式）"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON
    json_file = log_dir / f"qwen_v2_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "version": "2.0.0",
            "config": asdict(config),
            "state": asdict(state),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n📁 JSON: {json_file}")
    
    # Markdown
    md_file = log_dir / f"qwen_v2_{timestamp}.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(f"# QWEN Auto Consultation v2.0\n\n")
        f.write(f"**日時**: {timestamp}\n")
        f.write(f"**目標**: {config.goal}\n")
        f.write(f"**ラリー数**: {state.total_rally}/{config.max_rallies}\n")
        f.write(f"**最終Phase**: {state.phase}\n")
        f.write(f"**飽和理由**: {state.saturation_reason or 'N/A'}\n\n")
        
        f.write(f"## Artifacts\n\n")
        f.write(f"```json\n{json.dumps(state.artifacts, ensure_ascii=False, indent=2)}\n```\n\n")
        
        f.write(f"## DoD Status\n\n")
        f.write(f"- 達成: {state.dod_satisfied}\n")
        f.write(f"- 未達: {state.dod_missing}\n\n")
        
        f.write(f"## Claims ({len(state.claims)}件)\n\n")
        for i, claim in enumerate(state.claims, 1):
            f.write(f"{i}. **[{claim.get('topic', 'N/A')}]** {claim.get('text', '')}\n")
        
        f.write(f"\n## 会話履歴\n\n")
        for entry in state.history:
            f.write(f"### Rally {entry['rally']} (Phase {entry.get('phase', '?')})\n\n")
            f.write(f"**Q**: {entry['question']}\n\n")
            f.write(f"**A**: {entry['response']}\n\n")
            f.write("---\n\n")
    
    print(f"📁 Markdown: {md_file}")
    return json_file


# ============================================================
# CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="QWEN Auto Consultation v2.1 (Phase制・並列実行対応)")
    parser.add_argument("--goal", "-g", required=True, help="相談目標")
    parser.add_argument("--topics", "-t", default="", help="必要トピック（カンマ区切り）")
    parser.add_argument("--theme", default="design", choices=["design", "debug", "research"], help="テーマ")
    parser.add_argument("--initial-question", "-q", help="初期質問（省略時は自動生成）")
    parser.add_argument("--max-rallies", "-n", type=int, default=50, help="最大ラリー数")
    parser.add_argument("--stop-on-no-progress", type=int, default=3, help="進捗なしで停止するまでの回数")
    
    # v2.1.0: ブラウザ関連オプション
    parser.add_argument("--use-cdp", action="store_true", 
                        help="既存CDP接続を使用（デフォルト: 新規ブラウザ起動）")
    parser.add_argument("--cdp-port", type=int, default=9223,
                        help="CDPポート（--use-cdp時のみ有効）")
    parser.add_argument("--session-file", type=Path, default=None,
                        help="セッション保存先ファイル")
    parser.add_argument("--save-session", action="store_true",
                        help="実行後にセッションを保存")
    
    args = parser.parse_args()
    
    config = ConsultConfig(
        goal=args.goal,
        theme=args.theme,
        initial_question=args.initial_question,
        required_topics=[t.strip() for t in args.topics.split(",") if t.strip()],
        max_rallies=args.max_rallies,
        stop_on_no_progress=args.stop_on_no_progress,
        # v2.1.0: ブラウザ設定
        use_cdp=args.use_cdp,
        cdp_port=args.cdp_port,
        session_file=args.session_file,
        save_session=args.save_session,
    )
    
    state = run_consultation_v2(config)
    
    print(f"\n{'='*60}")
    print(f"📊 Consultation Complete (v2.1)")
    print(f"{'='*60}")
    print(f"Rallies: {state.total_rally}/{config.max_rallies}")
    print(f"Final Phase: {state.phase}")
    print(f"DoD Satisfied: {len(state.dod_satisfied)}")
    print(f"Claims: {len(state.claims)}")
    print(f"Saturation: {state.saturation_reason or 'N/A'}")


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main
    except Exception:
        main()
    else:
        raise SystemExit(run_logged_main("desktop", "qwen_auto_consult_v2", main))
