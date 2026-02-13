# -*- coding: utf-8 -*-
"""
QWEN Auto Consultation Loop v1.1.0

Antigravityがタスク・飽和条件を設定し、QWENがChatGPTと長時間対話を実行。
100ターン等の壁打ち用スクリプト。

v1.1.0: 飽和判定改善
- ChatGPT質問検出（末尾に質問があれば飽和ブロック）
- 最小ラリー数で早期終了防止
- トピック深度チェック強化

使用例:
python qwen_auto_consult.py --goal "システム設計の壁打ち" --topics "アーキテクチャ,技術選定,リスク対策" --max-rallies 100
"""

import json
import sys
import argparse
import time
import requests
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


@dataclass
class ConsultConfig:
    """相談設定"""
    goal: str
    initial_question: Optional[str] = None
    required_topics: List[str] = field(default_factory=list)
    min_claims: int = 5
    max_rallies: int = 100
    min_rallies: int = 5  # v1.1.0: 最小ラリー数（早期終了防止）
    stop_on_no_new_info: int = 3  # N回連続で新情報なしなら停止
    claims_per_topic: int = 2  # v1.1.0: トピック毎の最低claims数


@dataclass
class ConsultState:
    """相談状態"""
    current_rally: int = 0
    claims: List[Dict] = field(default_factory=list)
    covered_topics: List[str] = field(default_factory=list)
    open_questions: List[str] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    no_new_info_count: int = 0
    saturated: bool = False
    saturation_reason: str = ""


def qwen_generate(prompt: str, temperature: float = 0.5, timeout: int = 120) -> str:
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


def has_pending_question(response: str) -> bool:
    """
    v1.1.0: ChatGPTが質問を投げかけているか検出
    
    末尾に質問がある場合は飽和をブロックする
    """
    import re
    
    # 末尾400文字を確認
    tail = response[-400:] if len(response) > 400 else response
    
    # 質問パターン（日本語）
    question_patterns = [
        r'[？?](?:\s*$|\s*\n\s*$)',  # 末尾の?
        r'どちらを.{0,20}(選|主軸|優先)',  # 選択質問
        r'どのように.{0,20}(進|します|対応)',
        r'次に.{0,10}決め',
        r'お聞かせ(ください)?',
        r'教えて.{0,5}(ください|いただ)[。？?]?\s*$',
        r'いかがでしょうか',
        r'ご希望.{0,10}(は|を)',
        r'どう.{0,10}(お考え|思われ)',
    ]
    
    for pattern in question_patterns:
        if re.search(pattern, tail, re.IGNORECASE):
            return True
    
    return False


def qwen_analyze_response(
    goal: str,
    last_response: str,
    history: List[Dict],
    required_topics: List[str],
    covered_topics: List[str],
) -> Dict[str, Any]:
    """
    QWENでChatGPT回答を分析
    
    Returns:
        {
            "next_question": str,
            "new_claims": [{"text": str, "topic": str}],
            "newly_covered_topics": [str],
            "goal_satisfied": bool,
            "reasoning": str
        }
    """
    history_summary = ""
    for i, entry in enumerate(history[-3:], 1):
        history_summary += f"--- Rally {i} ---\n"
        history_summary += f"Q: {entry.get('question', '')[:150]}...\n"
        history_summary += f"A: {entry.get('response', '')[:300]}...\n\n"
    
    prompt = f"""あなたは目標達成のための分析アシスタントです。

## 相談目標
{goal}

## 必要なトピック（全てカバーすると飽和）
{json.dumps(required_topics, ensure_ascii=False)}

## 既にカバー済みのトピック
{json.dumps(covered_topics, ensure_ascii=False)}

## 直近の会話履歴
{history_summary}

## 最新の回答
{last_response[:1500]}

## タスク
1. 回答から新しい知見（claims）を抽出
2. 新たにカバーされたトピックを特定
3. 目標達成に最も効果的な次の質問を生成
4. 目標が達成されたか判定

回答は以下のJSON形式で：
```json
{{
    "next_question": "次に聞くべき質問",
    "new_claims": [
        {{"text": "知見1", "topic": "関連トピック"}},
        {{"text": "知見2", "topic": "関連トピック"}}
    ],
    "newly_covered_topics": ["新たにカバーされたトピック"],
    "goal_satisfied": false,
    "reasoning": "この判断の理由（1-2文）"
}}
```"""
    
    raw = qwen_generate(prompt, temperature=0.4)
    
    # JSON抽出
    try:
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # バッククォートなしのJSONも試す
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
    except Exception as e:
        print(f"[QWEN] JSON parse error: {e}")
    
    # フォールバック
    return {
        "next_question": "前の回答について、もう少し具体的に教えてください。",
        "new_claims": [],
        "newly_covered_topics": [],
        "goal_satisfied": False,
        "reasoning": "JSON解析失敗、フォールバック"
    }


def ask_chatgpt(page, question: str) -> Dict[str, Any]:
    """ChatGPTに1往復の質問"""
    start_time = time.time()
    
    try:
        # 送信前の状態
        try:
            initial_msg_count = page.locator(FALLBACK_SELECTORS["assistant_message"]).count()
        except Exception:
            initial_msg_count = 0
        
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
        success, snapshot = monitor.wait_for_generation_complete(timeout_ms=120000)
        
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
            "error": None
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


def run_auto_consultation(config: ConsultConfig) -> ConsultState:
    """QWEN駆動の自動対話ループ実行"""
    state = ConsultState()
    
    print(f"\n{'='*60}")
    print(f"🤖 QWEN Auto Consultation v1.0.0")
    print(f"{'='*60}")
    print(f"Goal: {config.goal}")
    print(f"Required Topics: {config.required_topics}")
    print(f"Max Rallies: {config.max_rallies}")
    print(f"{'='*60}\n")
    
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
            return state
        
        print(f"Found ChatGPT: {page.url}")
        page.bring_to_front()
        
        # 新規チャット
        if not open_new_chat(page):
            print("Warning: Could not open new chat")
        
        # 初期質問
        current_question = config.initial_question or f"""あなたは専門家です。以下の目標について相談させてください。

## 相談目標
{config.goal}

## カバーしたいトピック
{', '.join(config.required_topics)}

## 質問
1. この目標を達成するために、まず何を考慮すべきですか？
2. 各トピックについて、重要なポイントを教えてください。"""
        
        # メインループ
        for rally in range(1, config.max_rallies + 1):
            state.current_rally = rally
            print(f"\n{'='*50}")
            print(f"📍 Rally {rally}/{config.max_rallies}")
            print(f"{'='*50}")
            print(f"Question: {current_question[:80]}...")
            
            # ChatGPTに質問
            result = ask_chatgpt(page, current_question)
            
            if not result["success"]:
                print(f"❌ Error: {result['error']}")
                break
            
            response = result["response"]
            print(f"✅ Response: {len(response)} chars ({result['elapsed_ms']}ms)")
            
            # 履歴に追加
            state.history.append({
                "rally": rally,
                "question": current_question,
                "response": response,
                "timestamp": datetime.now().isoformat()
            })
            
            # QWEN分析
            print("[QWEN] Analyzing response...")
            analysis = qwen_analyze_response(
                goal=config.goal,
                last_response=response,
                history=state.history,
                required_topics=config.required_topics,
                covered_topics=state.covered_topics,
            )
            
            # claims追加
            for claim in analysis.get("new_claims", []):
                state.claims.append({
                    "text": claim.get("text", ""),
                    "topic": claim.get("topic", ""),
                    "rally": rally
                })
            
            # トピック更新
            for topic in analysis.get("newly_covered_topics", []):
                if topic not in state.covered_topics:
                    state.covered_topics.append(topic)
                    print(f"  📌 New topic covered: {topic}")
            
            # 進捗表示
            print(f"  📊 Claims: {len(state.claims)} | Topics: {len(state.covered_topics)}/{len(config.required_topics)}")
            print(f"  💭 Reasoning: {analysis.get('reasoning', 'N/A')[:100]}")
            
            # v1.1.0: 飽和判定（改良版）
            
            # 0. ChatGPT質問検出（飽和ブロック）
            pending_question = has_pending_question(response)
            if pending_question:
                print(f"  ❓ ChatGPTが質問中 - 飽和ブロック")
            
            # 0.5. 最小ラリーチェック（早期終了防止）
            if state.current_rally < config.min_rallies:
                print(f"  ⏳ 最小ラリー未達成 ({state.current_rally}/{config.min_rallies})")
                # 飽和判定をスキップ
            elif pending_question:
                # 質問がある間は飽和しない
                pass
            else:
                # 1. 目標達成
                if analysis.get("goal_satisfied"):
                    state.saturated = True
                    state.saturation_reason = "goal_satisfied"
                    print(f"\n✅ 飽和点到達: 目標達成")
                    break
                
                # 2. 全トピックカバー + 深度チェック
                if len(config.required_topics) > 0:
                    uncovered = set(config.required_topics) - set(state.covered_topics)
                    
                    # v1.1.0: トピック毎のclaims数チェック
                    topic_claim_counts = {}
                    for claim in state.claims:
                        t = claim.get("topic", "unknown")
                        topic_claim_counts[t] = topic_claim_counts.get(t, 0) + 1
                    
                    # 全トピックが十分な深さか
                    shallow_topics = [
                        t for t in config.required_topics 
                        if topic_claim_counts.get(t, 0) < config.claims_per_topic
                    ]
                    
                    if len(uncovered) == 0 and len(state.claims) >= config.min_claims and len(shallow_topics) == 0:
                        state.saturated = True
                        state.saturation_reason = "all_topics_covered"
                        print(f"\n✅ 飽和点到達: 全トピックカバー + 十分な深度")
                        break
                    elif len(shallow_topics) > 0:
                        print(f"  ⚠️ 深度不足トピック: {shallow_topics[:3]}")
                
                # 3. 新情報なし連続
                if len(analysis.get("new_claims", [])) == 0:
                    state.no_new_info_count += 1
                    print(f"  ⚠️ No new claims ({state.no_new_info_count}/{config.stop_on_no_new_info})")
                    if state.no_new_info_count >= config.stop_on_no_new_info:
                        state.saturated = True
                        state.saturation_reason = "no_new_info"
                        print(f"\n✅ 飽和点到達: {config.stop_on_no_new_info}回連続で新情報なし")
                        break
                else:
                    state.no_new_info_count = 0
            
            # 次の質問
            current_question = analysis.get("next_question", "前の回答について、もう少し具体的に教えてください。")
            print(f"  ➡️ Next: {current_question[:60]}...")
        
        # 最大ラリー到達
        if not state.saturated and state.current_rally >= config.max_rallies:
            state.saturated = True
            state.saturation_reason = "max_rallies"
            print(f"\n⚠️ 最大ラリー数到達 ({config.max_rallies})")
        
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # ログ保存
        save_consultation_log(config, state)
        p.stop()
    
    return state


def save_consultation_log(config: ConsultConfig, state: ConsultState) -> Path:
    """相談ログを保存"""
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # JSON
    json_file = log_dir / f"qwen_auto_{timestamp}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump({
            "config": asdict(config),
            "state": asdict(state),
        }, f, ensure_ascii=False, indent=2)
    print(f"\n📁 JSON: {json_file}")
    
    # Markdown
    md_file = log_dir / f"qwen_auto_{timestamp}.md"
    with open(md_file, "w", encoding="utf-8") as f:
        f.write(f"# QWEN Auto Consultation\n\n")
        f.write(f"**日時**: {timestamp}\n")
        f.write(f"**目標**: {config.goal}\n")
        f.write(f"**ラリー数**: {state.current_rally}/{config.max_rallies}\n")
        f.write(f"**飽和**: {state.saturation_reason or 'N/A'}\n\n")
        
        f.write(f"## 収集したClaims ({len(state.claims)}件)\n\n")
        for i, claim in enumerate(state.claims, 1):
            f.write(f"{i}. **[{claim.get('topic', 'N/A')}]** {claim.get('text', '')}\n")
        
        f.write(f"\n## カバー済みトピック\n\n")
        for topic in state.covered_topics:
            f.write(f"- ✅ {topic}\n")
        
        uncovered = set(config.required_topics) - set(state.covered_topics)
        if uncovered:
            f.write(f"\n## 未カバートピック\n\n")
            for topic in uncovered:
                f.write(f"- ❌ {topic}\n")
        
        f.write(f"\n## 会話履歴\n\n")
        for entry in state.history:
            f.write(f"### Rally {entry['rally']}\n\n")
            f.write(f"**Q**: {entry['question']}\n\n")
            f.write(f"**A**: {entry['response']}\n\n")
            f.write("---\n\n")
    
    print(f"📁 Markdown: {md_file}")
    return json_file


def main():
    parser = argparse.ArgumentParser(description="QWEN Auto Consultation Loop")
    parser.add_argument("--goal", "-g", required=True, help="相談目標")
    parser.add_argument("--topics", "-t", default="", help="必要トピック（カンマ区切り）")
    parser.add_argument("--initial-question", "-q", help="初期質問（省略時は自動生成）")
    parser.add_argument("--max-rallies", "-n", type=int, default=100, help="最大ラリー数")
    parser.add_argument("--min-claims", type=int, default=5, help="最小claims数")
    parser.add_argument("--min-rallies", type=int, default=5, help="v1.1.0: 最小ラリー数（早期終了防止）")
    parser.add_argument("--claims-per-topic", type=int, default=2, help="v1.1.0: トピック毎の最小claims数")
    parser.add_argument("--stop-on-no-new", type=int, default=3, help="新情報なしで停止するまでの回数")
    args = parser.parse_args()
    
    # 設定構築
    config = ConsultConfig(
        goal=args.goal,
        initial_question=args.initial_question,
        required_topics=[t.strip() for t in args.topics.split(",") if t.strip()],
        min_claims=args.min_claims,
        max_rallies=args.max_rallies,
        min_rallies=args.min_rallies,
        stop_on_no_new_info=args.stop_on_no_new,
        claims_per_topic=args.claims_per_topic,
    )
    
    # 実行
    state = run_auto_consultation(config)
    
    # 結果サマリ
    print(f"\n{'='*60}")
    print(f"📊 Consultation Complete")
    print(f"{'='*60}")
    print(f"Rallies: {state.current_rally}/{config.max_rallies}")
    print(f"Claims: {len(state.claims)}")
    print(f"Topics Covered: {len(state.covered_topics)}/{len(config.required_topics)}")
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
        raise SystemExit(run_logged_main("desktop", "qwen_auto_consult", main))
