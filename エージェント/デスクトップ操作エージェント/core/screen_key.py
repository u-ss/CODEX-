"""
Desktop Control Core: screen_key（画面識別）
--------------------------------------------
ChatGPT壁打ちRally 9で設計した画面識別システム。
Circuit BreakerやBandit学習のスコープを決定する。
"""
from __future__ import annotations

import ctypes
import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# Windows API
try:
    import ctypes.wintypes as wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    HAS_WIN32 = True
except Exception:
    HAS_WIN32 = False


# =============================================================================
# ScreenFingerprint（画面指紋）
# =============================================================================

@dataclass(frozen=True)
class ScreenFingerprint:
    """画面を識別するための指紋情報"""
    process: str          # プロセス名（notepad.exe, chrome.exeなど）
    win_class: str        # ウィンドウクラス名
    title_norm: str       # 正規化されたウィンドウタイトル
    url_norm: str         # 正規化されたURL（ブラウザの場合）
    uia_sig: str          # UIAツリーの軽量シグネチャ
    img_sig: str          # 画像ベースのシグネチャ（オプション）
    
    def coarse_key(self) -> str:
        """粗いキー（デスクトップ全般で強い）"""
        return f"{self.process}|{self.win_class}"
    
    def mid_key(self) -> str:
        """中程度のキー（推奨）"""
        parts = [self.coarse_key()]
        if self.url_norm:
            parts.append(self.url_norm)
        elif self.title_norm:
            parts.append(self.title_norm)
        return "|".join(parts)
    
    def fine_key(self) -> str:
        """細かいキー（厳密だが動的要素で分裂しやすい）"""
        return f"{self.mid_key()}|{self.uia_sig}"
    
    def backoff_keys(self) -> list[str]:
        """粗い順にバックオフするキーのリスト"""
        return [self.fine_key(), self.mid_key(), self.coarse_key()]


# =============================================================================
# ScreenKeyBuilder
# =============================================================================

class ScreenKeyBuilder:
    """画面識別キーを構築する"""
    
    def __init__(
        self,
        enable_uia: bool = True,
        enable_img: bool = False,
    ):
        self.enable_uia = enable_uia
        self.enable_img = enable_img
    
    def build(self, hwnd: int) -> ScreenFingerprint:
        """ウィンドウハンドルから画面指紋を生成"""
        if not HAS_WIN32:
            return ScreenFingerprint("", "", "", "", "", "")
        
        # プロセス名
        process = self._get_process_name(hwnd)
        
        # ウィンドウクラス
        win_class = self._get_window_class(hwnd).lower()
        
        # タイトル（正規化）
        title = self._get_window_title(hwnd)
        title_norm = self._normalize_title(title)
        
        # URL（ブラウザの場合）
        url_norm = ""
        if self._is_browser(process, win_class):
            url_norm = self._try_get_url() or ""
        
        # UIA構造シグネチャ
        uia_sig = ""
        if self.enable_uia:
            uia_sig = self._try_get_uia_signature(hwnd)
        
        # 画像シグネチャ
        img_sig = ""
        if self.enable_img:
            img_sig = self._try_get_image_hash(hwnd)
        
        return ScreenFingerprint(
            process=process,
            win_class=win_class,
            title_norm=title_norm,
            url_norm=url_norm,
            uia_sig=uia_sig,
            img_sig=img_sig,
        )
    
    def build_from_active(self) -> Tuple[int, ScreenFingerprint]:
        """アクティブウィンドウから画面指紋を生成"""
        if not HAS_WIN32:
            return 0, ScreenFingerprint("", "", "", "", "", "")
        
        hwnd = user32.GetForegroundWindow()
        return hwnd, self.build(hwnd)
    
    # -------------------------------------------------------------------------
    # Private Methods
    # -------------------------------------------------------------------------
    
    def _get_process_name(self, hwnd: int) -> str:
        """ウィンドウのプロセス名を取得"""
        try:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            
            h_process = kernel32.OpenProcess(0x0400 | 0x0010, False, pid.value)
            if h_process:
                buf = ctypes.create_unicode_buffer(260)
                kernel32.K32GetModuleBaseNameW(h_process, None, buf, 260)
                kernel32.CloseHandle(h_process)
                return buf.value.lower()
        except Exception:
            pass
        return ""
    
    def _get_window_class(self, hwnd: int) -> str:
        """ウィンドウクラス名を取得"""
        try:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            return buf.value
        except Exception:
            return ""
    
    def _get_window_title(self, hwnd: int) -> str:
        """ウィンドウタイトルを取得"""
        try:
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            return buf.value
        except Exception:
            return ""
    
    def _normalize_title(self, title: str) -> str:
        """タイトルを正規化（動的部分を除去）"""
        # 数字の連続を汎用プレースホルダに
        normalized = re.sub(r'\d+', '#', title)
        # タイムスタンプ風のパターンを除去
        normalized = re.sub(r'\d{2}:\d{2}(:\d{2})?', '', normalized)
        normalized = re.sub(r'\d{4}[-/]\d{2}[-/]\d{2}', '', normalized)
        # 余分な空白を整理
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized[:100]  # 長すぎる場合は切り詰め
    
    def _is_browser(self, process: str, win_class: str) -> bool:
        """ブラウザかどうか判定"""
        browser_processes = ['chrome.exe', 'msedge.exe', 'brave.exe', 'firefox.exe']
        browser_classes = ['Chrome_WidgetWin_1', 'MozillaWindowClass']
        return process in browser_processes or win_class in browser_classes
    
    def _try_get_url(self) -> Optional[str]:
        """ブラウザのURLを取得（CDP経由、利用可能な場合）"""
        # TODO: CDP接続時にURLを取得
        return None
    
    def _try_get_uia_signature(self, hwnd: int) -> str:
        """UIAツリーの軽量シグネチャを生成"""
        try:
            from pywinauto import Desktop
            desktop = Desktop(backend='uia')
            
            # トップレベルの子要素のcontrol_typeを取得
            for w in desktop.windows(visible_only=True):
                if w.handle == hwnd:
                    children_types = []
                    for child in w.children()[:10]:  # 最初の10要素
                        try:
                            children_types.append(child.element_info.control_type)
                        except Exception:
                            pass
                    return "|".join(children_types)[:100]
        except Exception:
            pass
        return ""
    
    def _try_get_image_hash(self, hwnd: int) -> str:
        """画像ベースのハッシュを生成（オプション）"""
        try:
            import mss
            from PIL import Image
            
            # ウィンドウの位置を取得
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            
            with mss.mss() as sct:
                region = {
                    'left': rect.left,
                    'top': rect.top,
                    'width': rect.right - rect.left,
                    'height': rect.bottom - rect.top
                }
                raw = sct.grab(region)
                img = Image.frombytes('RGB', raw.size, raw.rgb)
                # 縮小してハッシュ
                img_small = img.resize((16, 16))
                return hashlib.md5(img_small.tobytes()).hexdigest()[:8]
        except Exception:
            pass
        return ""


# =============================================================================
# Utility: 「同じ画面」判定
# =============================================================================

def same_screen(a: ScreenFingerprint, b: ScreenFingerprint) -> bool:
    """2つの画面指紋が「同じ画面」かどうか判定"""
    # coarseが違う → 別画面
    if a.coarse_key() != b.coarse_key():
        return False
    
    # mid一致 → 同画面扱い（推奨）
    if a.mid_key() == b.mid_key():
        return True
    
    # 画像hashが両方あり一致なら同画面とみなす（保険）
    if a.img_sig and b.img_sig and a.img_sig == b.img_sig:
        return True
    
    return False
