# -*- coding: utf-8 -*-
"""
Perplexity Desktop 検証ランナー v1.0.0

Research結果に基づく実装:
- UIAは浅い構造のため、キーボードショートカット主体
- Visionで画面変化を検知
- 状態機械: IDLE → QUERY_SUBMITTED → LOADING → ANSWER_VISIBLE → DONE
"""

import asyncio
import time
import mss
import imagehash
from PIL import Image
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Literal, Optional, List
from enum import Enum
import pyautogui
import json
import os

# Planner学習機能
try:
    from core.planner import learn_from_actions, Intent, IntentVerb, TargetApp, ActionSpec
    HAS_PLANNER = True
except ImportError:
    HAS_PLANNER = False

# 既存モジュールのインポート（存在する場合）
try:
    from perception.img_hash import compute_phash, hash_distance
except ImportError:
    # フォールバック実装
    def compute_phash(img):
        return str(imagehash.phash(img))
    def hash_distance(h1, h2):
        return sum(c1 != c2 for c1, c2 in zip(h1, h2))


class TaskState(Enum):
    """検証タスクの状態"""
    IDLE = "idle"
    QUERY_SUBMITTED = "query_submitted"
    LOADING = "loading"
    ANSWER_VISIBLE = "answer_visible"
    DONE = "done"
    FAILED = "failed"


@dataclass
class VerificationConfig:
    """検証設定"""
    query: str = "Hello, this is a test query. Please respond briefly."
    timeout_sec: float = 60.0
    stability_threshold_sec: float = 3.0  # この時間変化がなければDONE
    hash_diff_threshold: int = 5  # この差分以下は「変化なし」
    screenshot_dir: str = "_screenshots"
    log_dir: str = ".agent/workflows/desktop/logs"


@dataclass
class Observation:
    """観測データ"""
    timestamp: float
    screen_hash: str
    roi_hash: Optional[str] = None
    state: TaskState = TaskState.IDLE


@dataclass
class StepRecord:
    """1ステップの記録"""
    step_id: str
    intent: str
    target: str
    layer: str  # "KEYBOARD" / "UIA" / "VISION"
    pre_observation: Optional[Observation] = None
    action: dict = field(default_factory=dict)
    post_observation: Optional[Observation] = None
    verdict: str = "PENDING"  # OK / RETRYABLE_FAIL / FATAL_FAIL
    reason_code: str = ""
    artifacts: dict = field(default_factory=dict)


class PerplexityVerificationRunner:
    """Perplexity Desktop操作の検証ランナー"""
    
    def __init__(self, config: VerificationConfig = None):
        self.config = config or VerificationConfig()
        self.state = TaskState.IDLE
        self.observations: List[Observation] = []
        self.records: List[StepRecord] = []
        self.run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # ディレクトリ作成
        Path(self.config.screenshot_dir).mkdir(exist_ok=True)
        Path(self.config.log_dir).mkdir(parents=True, exist_ok=True)
    
    def _capture_screen(self) -> tuple[Image.Image, str]:
        """画面キャプチャとハッシュ計算"""
        with mss.mss() as sct:
            # Monitor 1（Perplexityがあるモニター）
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            
        screen_hash = compute_phash(img)
        return img, screen_hash
    
    def _save_screenshot(self, img: Image.Image, prefix: str) -> str:
        """スクリーンショットを保存"""
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{self.config.screenshot_dir}/{prefix}_{self.run_id}_{timestamp}.png"
        img.save(filename)
        return filename
    
    def _observe(self) -> Observation:
        """現在の画面を観測"""
        img, screen_hash = self._capture_screen()
        
        obs = Observation(
            timestamp=time.time(),
            screen_hash=screen_hash,
            state=self.state
        )
        self.observations.append(obs)
        return obs
    
    def _detect_change(self) -> bool:
        """直近の観測で画面変化があったか"""
        if len(self.observations) < 2:
            return True  # 初回は常に変化あり
        
        prev = self.observations[-2]
        curr = self.observations[-1]
        
        distance = hash_distance(prev.screen_hash, curr.screen_hash)
        return distance > self.config.hash_diff_threshold
    
    def _is_stable(self) -> bool:
        """画面が安定したか（一定時間変化なし）"""
        if len(self.observations) < 2:
            return False
        
        # 直近N秒の観測を取得
        now = time.time()
        recent_obs = [o for o in self.observations if now - o.timestamp < self.config.stability_threshold_sec]
        
        if len(recent_obs) < 2:
            return False
        
        # 全ての観測でハッシュが同じか
        first_hash = recent_obs[0].screen_hash
        return all(hash_distance(first_hash, o.screen_hash) <= self.config.hash_diff_threshold for o in recent_obs)
    
    async def _focus_perplexity(self) -> bool:
        """Perplexityウィンドウにフォーカス"""
        try:
            import ctypes
            from ctypes import wintypes
            
            user32 = ctypes.windll.user32
            
            def enum_windows_callback(hwnd, lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buff, length + 1)
                    if 'perplexity' in buff.value.lower():
                        user32.SetForegroundWindow(hwnd)
                        return False  # 停止
                return True
            
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            print(f"Focus error: {e}")
            return False
    
    async def _focus_input(self) -> bool:
        """入力欄にフォーカス（Ctrl+J）"""
        pyautogui.hotkey('ctrl', 'j')
        await asyncio.sleep(0.3)
        return True
    
    async def _type_query(self, query: str) -> bool:
        """クエリを入力"""
        # 既存テキストをクリア
        pyautogui.hotkey('ctrl', 'a')
        await asyncio.sleep(0.1)
        
        # テキスト入力（日本語対応）
        import pyperclip
        pyperclip.copy(query)
        pyautogui.hotkey('ctrl', 'v')
        await asyncio.sleep(0.3)
        return True
    
    async def _submit_query(self) -> bool:
        """クエリを送信（Enter）"""
        pyautogui.press('enter')
        await asyncio.sleep(0.5)
        return True
    
    async def run_verification(self, query: str = None) -> dict:
        """
        検証タスクを実行
        
        Returns:
            dict: 検証結果（success, state, observations_count, etc.）
        """
        query = query or self.config.query
        start_time = time.time()
        result = {
            "success": False,
            "state": self.state.value,
            "query": query,
            "run_id": self.run_id,
            "observations_count": 0,
            "duration_sec": 0,
            "final_screenshot": None,
            "error": None
        }
        
        try:
            # Step 1: Perplexityにフォーカス
            print("[1/5] Perplexityにフォーカス...")
            pre_obs = self._observe()
            
            if not await self._focus_perplexity():
                raise Exception("Perplexityウィンドウが見つかりません")
            
            post_obs = self._observe()
            self.records.append(StepRecord(
                step_id=f"{self.run_id}_1",
                intent="focus_perplexity",
                target="Perplexity window",
                layer="UIA",
                pre_observation=pre_obs,
                action={"type": "SetForegroundWindow"},
                post_observation=post_obs,
                verdict="OK"
            ))
            
            # Step 2: 入力欄にフォーカス
            print("[2/5] 入力欄にフォーカス...")
            pre_obs = self._observe()
            
            await self._focus_input()
            
            post_obs = self._observe()
            self.records.append(StepRecord(
                step_id=f"{self.run_id}_2",
                intent="focus_input",
                target="input_field",
                layer="KEYBOARD",
                pre_observation=pre_obs,
                action={"type": "hotkey", "keys": ["ctrl", "j"]},
                post_observation=post_obs,
                verdict="OK"
            ))
            
            # Step 3: クエリ入力
            print(f"[3/5] クエリ入力: {query[:30]}...")
            pre_obs = self._observe()
            
            await self._type_query(query)
            
            post_obs = self._observe()
            self.records.append(StepRecord(
                step_id=f"{self.run_id}_3",
                intent="type_query",
                target="input_field",
                layer="KEYBOARD",
                pre_observation=pre_obs,
                action={"type": "paste", "text": query},
                post_observation=post_obs,
                verdict="OK"
            ))
            
            # Step 4: 送信
            print("[4/5] クエリ送信...")
            pre_obs = self._observe()
            self.state = TaskState.QUERY_SUBMITTED
            
            await self._submit_query()
            
            self.state = TaskState.LOADING
            post_obs = self._observe()
            self.records.append(StepRecord(
                step_id=f"{self.run_id}_4",
                intent="submit_query",
                target="send_action",
                layer="KEYBOARD",
                pre_observation=pre_obs,
                action={"type": "key", "key": "enter"},
                post_observation=post_obs,
                verdict="OK"
            ))
            
            # Step 5: 回答完了を待機
            print("[5/5] 回答完了を待機...")
            timeout_at = start_time + self.config.timeout_sec
            last_change_time = time.time()
            
            while time.time() < timeout_at:
                await asyncio.sleep(0.5)
                self._observe()
                
                if self._detect_change():
                    last_change_time = time.time()
                    self.state = TaskState.LOADING
                else:
                    # 変化がない → 安定化チェック
                    elapsed_since_change = time.time() - last_change_time
                    if elapsed_since_change >= self.config.stability_threshold_sec:
                        self.state = TaskState.DONE
                        break
            
            if self.state != TaskState.DONE:
                self.state = TaskState.FAILED
                result["error"] = "Timeout: response did not stabilize"
                # 失敗を学習
                self._learn_result(query, success=False)
            else:
                result["success"] = True
                # 成功を学習
                self._learn_result(query, success=True)
            
            # 最終スクリーンショット
            img, _ = self._capture_screen()
            final_ss = self._save_screenshot(img, "final")
            result["final_screenshot"] = final_ss
            
        except Exception as e:
            self.state = TaskState.FAILED
            result["error"] = str(e)
            import traceback
            traceback.print_exc()
            # 失敗を学習
            self._learn_result(query, success=False)
        
        # 結果更新
        result["state"] = self.state.value
        result["observations_count"] = len(self.observations)
        result["duration_sec"] = time.time() - start_time
        
        # ログ保存
        self._save_run_log(result)
        
        return result
    
    def _learn_result(self, query: str, success: bool):
        """Plannerに結果を学習させる"""
        if not HAS_PLANNER:
            return
        
        try:
            intent = Intent(
                verb=IntentVerb.SEARCH,
                target_app=TargetApp.PERPLEXITY,
                query=query
            )
            # 実行したアクション列を構築
            actions = [
                ActionSpec(layer="uia", action_type="focus", target="Perplexity", description="Perplexityにフォーカス"),
                ActionSpec(layer="pixel", action_type="fill", target="", params={"value": query}, description="クエリ入力"),
                ActionSpec(layer="pixel", action_type="press", params={"key": "enter"}, description="送信"),
            ]
            learn_from_actions(intent, actions, success)
            print(f"[Planner] 学習完了: success={success}")
        except Exception as e:
            print(f"[Planner] 学習エラー: {e}")
    
    def _save_run_log(self, result: dict):
        """実行ログを保存"""
        log_file = f"{self.config.log_dir}/verification_run_{self.run_id}.json"
        
        log_data = {
            "run_id": self.run_id,
            "result": result,
            "records": [
                {
                    "step_id": r.step_id,
                    "intent": r.intent,
                    "layer": r.layer,
                    "verdict": r.verdict
                }
                for r in self.records
            ],
            "observations_summary": {
                "count": len(self.observations),
                "unique_hashes": len(set(o.screen_hash for o in self.observations))
            }
        }
        
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
        
        print(f"Log saved: {log_file}")


async def main():
    """テスト実行"""
    config = VerificationConfig(
        query="Antigravityという名前のAI IDEについて簡単に説明してください。",
        timeout_sec=45.0,
        stability_threshold_sec=3.0
    )
    
    runner = PerplexityVerificationRunner(config)
    result = await runner.run_verification()
    
    print("\n=== Verification Result ===")
    print(f"Success: {result['success']}")
    print(f"State: {result['state']}")
    print(f"Duration: {result['duration_sec']:.1f}s")
    print(f"Observations: {result['observations_count']}")
    if result['error']:
        print(f"Error: {result['error']}")
    if result['final_screenshot']:
        print(f"Final SS: {result['final_screenshot']}")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    _shared_dir = Path(__file__).resolve().parents[2] / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import run_logged_main_async
    except Exception:
        asyncio.run(main())
    else:
        raise SystemExit(
            asyncio.run(
                run_logged_main_async(
                    "desktop",
                    "perplexity_verification_runner",
                    main,
                )
            )
        )
