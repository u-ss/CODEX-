"""
Layer Healthcheck / Capability Probe

目的: フォールバックを「事故」ではなく「計画」にする

チェック内容:
- Layer2+（CDP）接続可否
- Layer3（UIA）で対象ウィンドウ列挙可否
- Layer1（座標/画像）でキャリブレーション可否
- 失敗時にUnknown App Protocolの状態遷移へ明示的に渡す

ChatGPT 5.2フィードバック（2026-02-05）より
"""

import socket
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Callable
from pathlib import Path


class LayerType(Enum):
    """レイヤータイプ"""
    LAYER_2_PLUS = "layer2+"  # Playwright+CDP
    LAYER_3 = "layer3"        # Pywinauto (UIA)
    LAYER_1 = "layer1"        # PyAutoGUI
    LAYER_0 = "layer0"        # VLM


class HealthStatus(Enum):
    """ヘルスステータス"""
    HEALTHY = "healthy"       # 正常
    DEGRADED = "degraded"     # 機能低下（一部機能不可）
    UNHEALTHY = "unhealthy"   # 異常（使用不可）
    UNKNOWN = "unknown"       # 未チェック


@dataclass
class HealthCheckResult:
    """ヘルスチェック結果"""
    layer: LayerType
    status: HealthStatus
    message: str
    latency_ms: Optional[int] = None   # 応答時間
    capabilities: list[str] = None     # 利用可能な機能
    limitations: list[str] = None      # 制限事項
    
    def __post_init__(self):
        self.capabilities = self.capabilities or []
        self.limitations = self.limitations or []


class CDPHealthCheck:
    """Layer 2+ (CDP) ヘルスチェック"""
    
    def __init__(self, port: int = 9222, host: str = "localhost"):
        self.port = port
        self.host = host
    
    def check(self) -> HealthCheckResult:
        """CDP接続をチェック"""
        
        start = time.time()
        
        # ポート接続チェック
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3)
            result = sock.connect_ex((self.host, self.port))
            sock.close()
            
            if result != 0:
                return HealthCheckResult(
                    layer=LayerType.LAYER_2_PLUS,
                    status=HealthStatus.UNHEALTHY,
                    message=f"CDPポート{self.port}に接続できません",
                    limitations=["DOM操作不可", "ブラウザ自動化不可"]
                )
        except Exception as e:
            return HealthCheckResult(
                layer=LayerType.LAYER_2_PLUS,
                status=HealthStatus.UNHEALTHY,
                message=f"接続エラー: {e}",
                limitations=["DOM操作不可"]
            )
        
        latency = int((time.time() - start) * 1000)
        
        # Playwrightで接続テスト
        try:
            # 簡易チェック（httpでJSONエンドポイント確認）
            import urllib.request
            with urllib.request.urlopen(f"http://{self.host}:{self.port}/json", timeout=5) as resp:
                data = resp.read()
                if data:
                    return HealthCheckResult(
                        layer=LayerType.LAYER_2_PLUS,
                        status=HealthStatus.HEALTHY,
                        message="CDP接続OK",
                        latency_ms=latency,
                        capabilities=["DOM操作", "JavaScript実行", "スクリーンショット"],
                    )
        except Exception as e:
            return HealthCheckResult(
                layer=LayerType.LAYER_2_PLUS,
                status=HealthStatus.DEGRADED,
                message=f"CDPエンドポイント確認失敗: {e}",
                latency_ms=latency,
                limitations=["一部機能制限の可能性"]
            )
        
        return HealthCheckResult(
            layer=LayerType.LAYER_2_PLUS,
            status=HealthStatus.UNKNOWN,
            message="チェック未完了"
        )


class UIAHealthCheck:
    """Layer 3 (UIA) ヘルスチェック"""
    
    def check(self, target_process: Optional[str] = None) -> HealthCheckResult:
        """UIAでウィンドウ列挙をチェック"""
        
        start = time.time()
        
        try:
            # pywinautoインポートチェック
            from pywinauto import Desktop
            
            desktop = Desktop(backend="uia")
            windows = desktop.windows()
            
            latency = int((time.time() - start) * 1000)
            
            if not windows:
                return HealthCheckResult(
                    layer=LayerType.LAYER_3,
                    status=HealthStatus.DEGRADED,
                    message="ウィンドウが見つかりません",
                    latency_ms=latency,
                    limitations=["対象ウィンドウなし"]
                )
            
            # 特定プロセスのチェック
            if target_process:
                found = any(target_process.lower() in str(w).lower() for w in windows)
                if not found:
                    return HealthCheckResult(
                        layer=LayerType.LAYER_3,
                        status=HealthStatus.DEGRADED,
                        message=f"対象プロセス({target_process})が見つかりません",
                        latency_ms=latency,
                        capabilities=["ウィンドウ列挙"],
                        limitations=[f"{target_process}操作不可"]
                    )
            
            return HealthCheckResult(
                layer=LayerType.LAYER_3,
                status=HealthStatus.HEALTHY,
                message=f"UIA正常（{len(windows)}ウィンドウ）",
                latency_ms=latency,
                capabilities=["ウィンドウ列挙", "要素操作", "フォーカス制御"],
            )
            
        except ImportError:
            return HealthCheckResult(
                layer=LayerType.LAYER_3,
                status=HealthStatus.UNHEALTHY,
                message="pywinautoがインストールされていません",
                limitations=["UIA操作不可"]
            )
        except Exception as e:
            return HealthCheckResult(
                layer=LayerType.LAYER_3,
                status=HealthStatus.UNHEALTHY,
                message=f"UIAエラー: {e}",
                limitations=["UIA操作不可"]
            )


class PyAutoGUIHealthCheck:
    """Layer 1 (PyAutoGUI) ヘルスチェック"""
    
    def check(self) -> HealthCheckResult:
        """PyAutoGUIの動作をチェック"""
        
        start = time.time()
        
        try:
            import pyautogui
            
            # スクリーンサイズ取得
            width, height = pyautogui.size()
            
            # マウス位置取得
            x, y = pyautogui.position()
            
            latency = int((time.time() - start) * 1000)
            
            # DPIスケーリング検出
            try:
                import ctypes
                dpi = ctypes.windll.user32.GetDpiForSystem()
                scale = dpi / 96
            except:
                scale = 1.0
            
            capabilities = ["座標クリック", "キー入力", "スクリーンショット"]
            limitations = []
            
            if scale != 1.0:
                limitations.append(f"DPIスケーリング({scale}x)あり、座標補正が必要")
            
            if x < 0 or y < 0:
                limitations.append("マルチモニタで負座標あり")
            
            return HealthCheckResult(
                layer=LayerType.LAYER_1,
                status=HealthStatus.HEALTHY,
                message=f"PyAutoGUI正常（{width}x{height}、マウス@{x},{y}）",
                latency_ms=latency,
                capabilities=capabilities,
                limitations=limitations if limitations else None
            )
            
        except ImportError:
            return HealthCheckResult(
                layer=LayerType.LAYER_1,
                status=HealthStatus.UNHEALTHY,
                message="pyautoguiがインストールされていません",
                limitations=["座標操作不可"]
            )
        except Exception as e:
            return HealthCheckResult(
                layer=LayerType.LAYER_1,
                status=HealthStatus.UNHEALTHY,
                message=f"PyAutoGUIエラー: {e}",
                limitations=["座標操作不可"]
            )


class LayerHealthcheck:
    """全レイヤーヘルスチェック統合"""
    
    def __init__(self, cdp_port: int = 9222):
        self.cdp_check = CDPHealthCheck(port=cdp_port)
        self.uia_check = UIAHealthCheck()
        self.pyautogui_check = PyAutoGUIHealthCheck()
    
    def check_all(self, target_process: Optional[str] = None) -> dict[LayerType, HealthCheckResult]:
        """全レイヤーをチェック"""
        
        return {
            LayerType.LAYER_2_PLUS: self.cdp_check.check(),
            LayerType.LAYER_3: self.uia_check.check(target_process),
            LayerType.LAYER_1: self.pyautogui_check.check(),
        }
    
    def get_best_layer(self, results: dict[LayerType, HealthCheckResult]) -> Optional[LayerType]:
        """利用可能な最良レイヤーを取得"""
        
        priority = [LayerType.LAYER_2_PLUS, LayerType.LAYER_3, LayerType.LAYER_1]
        
        for layer in priority:
            if layer in results and results[layer].status == HealthStatus.HEALTHY:
                return layer
        
        # DEGRADEDでも使えるものを探す
        for layer in priority:
            if layer in results and results[layer].status == HealthStatus.DEGRADED:
                return layer
        
        return None
    
    def get_fallback_chain(self, results: dict[LayerType, HealthCheckResult]) -> list[LayerType]:
        """フォールバックチェーンを取得"""
        
        chain = []
        priority = [LayerType.LAYER_2_PLUS, LayerType.LAYER_3, LayerType.LAYER_1]
        
        for layer in priority:
            if layer in results:
                status = results[layer].status
                if status in [HealthStatus.HEALTHY, HealthStatus.DEGRADED]:
                    chain.append(layer)
        
        return chain
    
    def format_report(self, results: dict[LayerType, HealthCheckResult]) -> str:
        """レポートをフォーマット"""
        
        lines = ["Layer Healthcheck Report:"]
        
        for layer, result in results.items():
            icon = {
                HealthStatus.HEALTHY: "✅",
                HealthStatus.DEGRADED: "⚠️",
                HealthStatus.UNHEALTHY: "❌",
                HealthStatus.UNKNOWN: "❓",
            }[result.status]
            
            latency = f" ({result.latency_ms}ms)" if result.latency_ms else ""
            lines.append(f"  {icon} [{layer.value}]{latency}")
            lines.append(f"      {result.message}")
            
            if result.capabilities:
                lines.append(f"      ✓ {', '.join(result.capabilities)}")
            if result.limitations:
                lines.append(f"      ✗ {', '.join(result.limitations)}")
        
        best = self.get_best_layer(results)
        if best:
            lines.append(f"\n推奨レイヤー: {best.value}")
        
        chain = self.get_fallback_chain(results)
        if chain:
            lines.append(f"フォールバック: {' → '.join(l.value for l in chain)}")
        
        return "\n".join(lines)


# テスト
if __name__ == "__main__":
    print("=" * 60)
    print("Layer Healthcheck テスト")
    print("=" * 60)
    
    healthcheck = LayerHealthcheck(cdp_port=9222)
    
    # 全レイヤーチェック
    print("\n--- 全レイヤーチェック ---")
    results = healthcheck.check_all(target_process="brave.exe")
    print(healthcheck.format_report(results))
    
    print("\n" + "=" * 60)
    print("テスト完了")
