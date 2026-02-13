# -*- coding: utf-8 -*-
"""
Support Bundle - サポート用ZIPパッケージ生成

障害発生時にtrace/KPI/alerts/環境情報を1つのZIPにまとめ、
サポート依頼時に添付できるようにする。
"""
from __future__ import annotations
import json
import os
import zipfile
import platform
import sys
from typing import Dict, Any, List, Optional
from datetime import datetime


def get_env_info() -> Dict[str, Any]:
    """
    環境情報を収集。
    OS/Python/解像度等をサポート向けに提供。
    """
    info = {
        "collected_at": datetime.now().isoformat(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
    }
    
    # Windows固有: DPI/解像度を取得（可能なら）
    if platform.system() == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            info["display"] = {
                "width": user32.GetSystemMetrics(0),
                "height": user32.GetSystemMetrics(1),
            }
        except Exception:
            pass
    
    return info


def make_support_bundle(
    out_zip: str,
    trace_path: str,
    kpi_path: Optional[str] = None,
    alerts_path: Optional[str] = None,
    env_info: Optional[Dict[str, Any]] = None,
    extra_files: Optional[List[str]] = None,
    screenshot_path: Optional[str] = None,
) -> str:
    """
    サポートバンドルZIPを生成。
    
    Args:
        out_zip: 出力ZIPパス
        trace_path: trace.jsonlパス
        kpi_path: kpi_summary.jsonパス（任意）
        alerts_path: alerts.jsonパス（任意）
        env_info: 環境情報（Noneなら自動収集）
        extra_files: 追加ファイルリスト
        screenshot_path: 最新スクリーンショット
    
    Returns:
        生成したZIPのパス
    """
    os.makedirs(os.path.dirname(out_zip) if os.path.dirname(out_zip) else ".", exist_ok=True)
    
    if env_info is None:
        env_info = get_env_info()
    
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # trace
        if os.path.exists(trace_path):
            z.write(trace_path, arcname="trace.jsonl")
        
        # KPI summary
        if kpi_path and os.path.exists(kpi_path):
            z.write(kpi_path, arcname="kpi_summary.json")
        
        # alerts
        if alerts_path and os.path.exists(alerts_path):
            z.write(alerts_path, arcname="alerts.json")
        
        # 環境情報
        z.writestr("env.json", json.dumps(env_info, ensure_ascii=False, indent=2))
        
        # スクリーンショット
        if screenshot_path and os.path.exists(screenshot_path):
            z.write(screenshot_path, arcname="last_screenshot.png")
        
        # 追加ファイル
        for fp in (extra_files or []):
            if fp and os.path.exists(fp):
                z.write(fp, arcname=os.path.basename(fp))
    
    return out_zip


def make_repro_pack(
    out_zip: str,
    trace_path: str,
    window_before: int = 50,
    window_after: int = 10,
) -> str:
    """
    再現パック（失敗周辺のみ抽出）を生成。
    
    Args:
        out_zip: 出力ZIPパス
        trace_path: trace.jsonlパス
        window_before: 失敗前の取得イベント数
        window_after: 失敗後の取得イベント数
    
    Returns:
        生成したZIPのパス
    """
    import json
    
    # traceを読み込み
    events = []
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if s:
                try:
                    events.append(json.loads(s))
                except Exception:
                    continue
    
    # 最初の失敗を検出
    fail_idx = None
    for i, e in enumerate(events):
        if e.get("event") == "action_end" and not bool(e.get("ok", True)):
            fail_idx = i
            break
    
    if fail_idx is None:
        raise RuntimeError("No failure found in trace")
    
    # ウィンドウ抽出
    lo = max(0, fail_idx - window_before)
    hi = min(len(events), fail_idx + window_after + 1)
    clip = events[lo:hi]
    
    # replay script生成
    script = []
    for e in clip:
        if e.get("event") == "action_end":
            script.append({
                "ok": bool(e.get("ok")),
                "duration_ms": float(e.get("duration_ms", 0.0)),
                "fail_type": e.get("fail_type"),
                "layer": e.get("layer"),
                "intent": e.get("intent"),
                "locator_id": e.get("locator_id"),
            })
    
    # 失敗サマリー
    fail_event = events[fail_idx]
    summary = (
        f"fail_type={fail_event.get('fail_type')} "
        f"layer={fail_event.get('layer')} "
        f"intent={fail_event.get('intent')} "
        f"locator_id={fail_event.get('locator_id')} "
        f"screen_key={fail_event.get('screen_key')}"
    )
    
    # ZIP生成
    os.makedirs(os.path.dirname(out_zip) if os.path.dirname(out_zip) else ".", exist_ok=True)
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("trace_clip.jsonl", "\n".join(json.dumps(x, ensure_ascii=False) for x in clip))
        z.writestr("replay_script.json", json.dumps(script, ensure_ascii=False, indent=2))
        z.writestr("summary.txt", summary)
    
    return out_zip
