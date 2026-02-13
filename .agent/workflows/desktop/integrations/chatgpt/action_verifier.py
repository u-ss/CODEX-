# -*- coding: utf-8 -*-
"""
Desktop Control v5.1.1 - Action Verification Module

アクション後のSS確認とQWEN推論を統合。
各アクションの成功/失敗を視覚的に検証。
"""

import json
import base64
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import requests
import sys

# パスを追加（直接実行時用）
_this_dir = Path(__file__).parent
_desktop_dir = _this_dir.parent.parent
if str(_desktop_dir) not in sys.path:
    sys.path.insert(0, str(_desktop_dir))

from tools.screenshot import capture_all_monitors, get_monitor_count, DEFAULT_SS_DIR


@dataclass
class VerificationResult:
    """検証結果"""
    action: str                  # 実行したアクション
    success: bool                # 成功判定
    confidence: float            # 確信度 (0.0-1.0)
    reason: str                  # 判定理由
    screenshot_paths: List[Path] # 撮影したSSパス
    qwen_response: Optional[str] = None  # QWENの生の応答
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class QWENClient:
    """
    Ollama経由でQWENモデルを使用するクライアント
    """
    
    def __init__(
        self,
        model: str = "qwen3:14b",  # Qwen3-14B Q4_K_M
        base_url: str = "http://localhost:11434",
    ):
        self.model = model
        self.base_url = base_url
    
    def analyze_screenshot(
        self,
        image_paths: List[Path],
        prompt: str,
        context: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        スクリーンショットをQWENで分析
        
        Args:
            image_paths: 分析する画像パス
            prompt: 分析プロンプト
            context: 追加コンテキスト
        
        Returns:
            {"success": bool, "response": str, "error": str|None}
        """
        # 画像をbase64エンコード
        images_b64 = []
        for path in image_paths:
            if path.exists():
                with open(path, "rb") as f:
                    images_b64.append(base64.b64encode(f.read()).decode())
        
        if not images_b64:
            return {"success": False, "response": "", "error": "画像が見つかりません"}
        
        # プロンプト構築
        full_prompt = prompt
        if context:
            full_prompt = f"{context}\n\n{prompt}"
        
        try:
            # Ollama API呼び出し（マルチモーダル）
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": full_prompt,
                    "images": images_b64,
                    "stream": False,
                    "options": {
                        "temperature": 0.3,  # 判定なので低め
                    }
                },
                timeout=120,
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "response": "",
                    "error": f"API error: {response.status_code}"
                }
            
            result = response.json()
            return {
                "success": True,
                "response": result.get("response", ""),
                "error": None
            }
            
        except requests.exceptions.Timeout:
            return {"success": False, "response": "", "error": "タイムアウト"}
        except Exception as e:
            return {"success": False, "response": "", "error": str(e)}
    
    def generate_next_question(
        self,
        goal: str,
        conversation_history: List[Dict],
        last_response: str,
    ) -> Dict[str, Any]:
        """
        ゴールと会話履歴から次の質問を推論
        
        Args:
            goal: 相談目標
            conversation_history: これまでの会話
            last_response: 最新の回答
        
        Returns:
            {"success": bool, "question": str, "reasoning": str, "goal_satisfied": bool}
        """
        # 会話履歴のサマリ
        history_summary = ""
        for i, entry in enumerate(conversation_history[-3:], 1):  # 直近3つ
            history_summary += f"--- Rally {i} ---\n"
            history_summary += f"Q: {entry.get('prompt', '')[:200]}...\n"
            history_summary += f"A: {entry.get('response', '')[:300]}...\n\n"
        
        prompt = f"""あなたは目標達成のための相談アシスタントです。以下の情報を分析し、次にすべき質問を推論してください。

## 相談目標
{goal}

## 会話履歴
{history_summary}

## 最新の回答
{last_response[:1000]}

## タスク
1. 目標に対して何が分かったか分析
2. まだ解決していない論点を特定
3. 目標達成に最も効果的な次の質問を生成

回答は以下のJSON形式で：
```json
{{
    "learned": ["分かったこと1", "分かったこと2"],
    "open_questions": ["未解決論点1", "未解決論点2"],
    "goal_satisfied": false,
    "next_question": "次に聞くべき質問",
    "reasoning": "この質問を選んだ理由"
}}
```"""
        
        try:
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.5,
                    }
                },
                timeout=120,
            )
            
            if response.status_code != 200:
                return {
                    "success": False,
                    "question": "",
                    "reasoning": "",
                    "goal_satisfied": False,
                    "error": f"API error: {response.status_code}"
                }
            
            result = response.json()
            raw_response = result.get("response", "")
            
            # JSONを抽出
            try:
                # ```json ... ``` の中身を抽出
                import re
                json_match = re.search(r'```json\s*(.*?)\s*```', raw_response, re.DOTALL)
                if json_match:
                    parsed = json.loads(json_match.group(1))
                else:
                    # JSONっぽい部分を探す
                    json_match = re.search(r'\{[^{}]*"next_question"[^{}]*\}', raw_response, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(0))
                    else:
                        # フォールバック
                        return {
                            "success": True,
                            "question": "前の回答について、もう少し詳しく教えてください。",
                            "reasoning": "JSONパース失敗、フォールバック",
                            "goal_satisfied": False,
                            "raw_response": raw_response
                        }
                
                return {
                    "success": True,
                    "question": parsed.get("next_question", ""),
                    "reasoning": parsed.get("reasoning", ""),
                    "goal_satisfied": parsed.get("goal_satisfied", False),
                    "learned": parsed.get("learned", []),
                    "open_questions": parsed.get("open_questions", []),
                }
                
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "question": "前の回答について、もう少し詳しく教えてください。",
                    "reasoning": "JSONパース失敗",
                    "goal_satisfied": False,
                    "raw_response": raw_response
                }
            
        except Exception as e:
            return {
                "success": False,
                "question": "",
                "reasoning": "",
                "goal_satisfied": False,
                "error": str(e)
            }


class ActionVerifier:
    """
    アクション後の検証を行うクラス
    """
    
    def __init__(
        self,
        qwen_client: Optional[QWENClient] = None,
        ss_output_dir: Optional[Path] = None,
    ):
        self.qwen = qwen_client or QWENClient()
        self.ss_dir = ss_output_dir or DEFAULT_SS_DIR / "verification"
        self.ss_dir.mkdir(parents=True, exist_ok=True)
    
    def verify_action(
        self,
        action: str,
        expected_outcome: str,
        context: Optional[str] = None,
    ) -> VerificationResult:
        """
        アクション後にSSを撮影し、期待結果と照合
        
        Args:
            action: 実行したアクション名（例: "send_message", "open_new_chat"）
            expected_outcome: 期待される結果の説明
            context: 追加コンテキスト
        
        Returns:
            VerificationResult
        """
        # 1. 両モニターのSSを撮影
        print(f"[Verify] Capturing screenshots for action: {action}")
        ss_paths = capture_all_monitors(
            output_dir=self.ss_dir,
            prefix=f"verify_{action}"
        )
        print(f"[Verify] Captured {len(ss_paths)} screenshots")
        
        # 2. QWENで分析
        prompt = f"""以下のスクリーンショットを分析してください。

## 実行したアクション
{action}

## 期待される結果
{expected_outcome}

## 質問
1. アクションは成功しましたか？（yes/no）
2. 確信度は？（0-100%）
3. 判断の根拠は？

回答は以下の形式で：
SUCCESS: yes または no
CONFIDENCE: 数値（0-100）
REASON: 理由の説明"""
        
        analysis = self.qwen.analyze_screenshot(
            image_paths=ss_paths,
            prompt=prompt,
            context=context,
        )
        
        # 3. 結果をパース
        if not analysis["success"]:
            return VerificationResult(
                action=action,
                success=False,
                confidence=0.0,
                reason=f"QWEN分析失敗: {analysis['error']}",
                screenshot_paths=ss_paths,
            )
        
        response = analysis["response"]
        
        # シンプルなパース
        success = "yes" in response.lower().split("success")[-1][:20] if "success" in response.lower() else False
        
        # 確信度抽出
        confidence = 0.5  # デフォルト
        import re
        conf_match = re.search(r'confidence[:\s]*(\d+)', response.lower())
        if conf_match:
            confidence = int(conf_match.group(1)) / 100.0
        
        # 理由抽出
        reason = response
        reason_match = re.search(r'reason[:\s]*(.+?)(?:\n|$)', response, re.IGNORECASE)
        if reason_match:
            reason = reason_match.group(1).strip()
        
        return VerificationResult(
            action=action,
            success=success,
            confidence=confidence,
            reason=reason,
            screenshot_paths=ss_paths,
            qwen_response=response,
        )
    
    def verify_chatgpt_response_received(self) -> VerificationResult:
        """ChatGPTの回答を正常に受信できたか確認"""
        return self.verify_action(
            action="receive_chatgpt_response",
            expected_outcome="ChatGPTの回答が画面に表示されている。回答は完全で、エラーメッセージは表示されていない。",
        )
    
    def verify_new_chat_opened(self) -> VerificationResult:
        """新規チャットが開かれたか確認"""
        return self.verify_action(
            action="open_new_chat",
            expected_outcome="ChatGPTで新しいチャットが開かれている。入力欄が空で、以前の会話履歴が表示されていない。",
        )
    
    def verify_message_sent(self, message_preview: str) -> VerificationResult:
        """メッセージが正常に送信されたか確認"""
        return self.verify_action(
            action="send_message",
            expected_outcome=f"ユーザーのメッセージが送信された。送信されたメッセージのプレビュー: '{message_preview[:50]}...'",
        )


def _cli_main() -> int:
    if len(sys.argv) > 1:
        if sys.argv[1] == "test-qwen":
            print("Testing QWEN connection...")
            client = QWENClient()
            result = client.generate_next_question(
                goal="テスト目標",
                conversation_history=[],
                last_response="テスト応答",
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        if sys.argv[1] == "test-ss":
            print("Testing screenshot capture...")
            verifier = ActionVerifier()
            result = verifier.verify_action(
                action="test",
                expected_outcome="テスト用のスクリーンショット",
            )
            print(f"Success: {result.success}")
            print(f"Confidence: {result.confidence}")
            print(f"Reason: {result.reason}")
            print(f"Screenshots: {result.screenshot_paths}")
            return 0

    print("Usage:")
    print("  python action_verifier.py test-qwen  # QWEN接続テスト")
    print("  python action_verifier.py test-ss    # SS撮影テスト")
    return 1


if __name__ == "__main__":
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
        _run_logged_main("desktop", "action_verifier", _cli_main, phase_name="ACTION_VERIFY_CLI")
    )

