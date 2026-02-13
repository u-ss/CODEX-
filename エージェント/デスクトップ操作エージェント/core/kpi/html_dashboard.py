# -*- coding: utf-8 -*-
"""
HTML Dashboard - KPIダッシュボードのHTML生成

KPI履歴を可視化するシンプルなHTMLダッシュボードを生成。
SVGスパークラインで傾向を表示。
"""
from __future__ import annotations
from typing import Any, Dict, List
import html
import json


def sparkline_svg(xs: List[float], width: int = 240, height: int = 40) -> str:
    """
    時系列データからSVGスパークラインを生成。
    """
    if not xs:
        return ""
    
    mn, mx = min(xs), max(xs)
    if mx == mn:
        mx = mn + 1e-9
    
    pts = []
    for i, x in enumerate(xs):
        px = int(i * (width - 1) / max(1, len(xs) - 1))
        py = int((1 - (x - mn) / (mx - mn)) * (height - 1))
        pts.append(f"{px},{py}")
    
    poly = " ".join(pts)
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{poly}"/>'
        f'</svg>'
    )


def render_dashboard(
    history: List[Dict[str, Any]],
    metrics: List[str],
    title: str,
    auto_refresh_sec: int | None = None
) -> str:
    """
    KPIダッシュボードのHTMLを生成。
    
    Args:
        history: 履歴データ [{ts:..., summary:...}, ...]
        metrics: 表示するメトリクスのドットパス
        title: ダッシュボードタイトル
        auto_refresh_sec: 自動更新間隔（秒）
    
    Returns:
        HTML文字列
    """
    # 直近N点だけ表示
    N = 200
    h = history[-N:] if len(history) > N else history
    
    rows = []
    for m in metrics:
        xs = []
        for r in h:
            cur = r.get("summary", {})
            for p in m.split("."):
                cur = cur.get(p, {})
            xs.append(float(cur) if isinstance(cur, (int, float)) else 0.0)
        
        last = xs[-1] if xs else 0.0
        spark = sparkline_svg(xs)
        rows.append((m, last, spark))
    
    meta = f'<meta http-equiv="refresh" content="{auto_refresh_sec}"/>' if auto_refresh_sec else ""
    
    body_rows = "\n".join(
        f"<tr><td>{html.escape(name)}</td><td>{last:.6f}</td><td>{spark}</td></tr>"
        for name, last, spark in rows
    )
    
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
{meta}
<title>{html.escape(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 16px; background: #1a1a2e; color: #eee; }}
h2 {{ color: #00d4ff; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border-bottom: 1px solid #333; padding: 8px; vertical-align: middle; }}
th {{ text-align: left; color: #888; }}
.small {{ color: #666; font-size: 12px; }}
svg {{ color: #00d4ff; }}
</style>
</head>
<body>
<h2>{html.escape(title)}</h2>
<div class="small">points: {len(h)}</div>
<table>
<thead><tr><th>metric</th><th>latest</th><th>trend</th></tr></thead>
<tbody>
{body_rows}
</tbody>
</table>
<script>
window.__HISTORY__ = {json.dumps(h, ensure_ascii=False)};
</script>
</body>
</html>"""


# デフォルトで表示するメトリクス
DEFAULT_DASHBOARD_METRICS = [
    "tasks.success_rate",
    "steps.success_rate",
    "actions.success_rate",
    "actions.pixel_rate",
    "actions.misclick_rate",
    "actions.wrong_state_rate",
    "actions.cb_fire_rate",
    "actions.hitl_rate",
    "actions.retry_rate",
]
