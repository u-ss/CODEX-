# router.py - レイヤー選択ロジック（Router）
# ChatGPT 5.2相談（ラリー4）に基づく実装

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol
import time


class Layer(str, Enum):
    """操作レイヤー"""
    CDP = "CDP"       # Playwright/CDP
    UIA = "UIA"       # pywinauto/UIAutomation
    PIXEL = "PIXEL"   # PyAutoGUI/画像認識


@dataclass
class Health:
    """レイヤーの健全性"""
    ok: bool
    reason: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class RouteKey:
    """ルーティングキー"""
    screen_key: str
    locator_key: str
    action_kind: str
    layer: Layer


@dataclass
class LayerStats:
    """レイヤー別統計（EWMA）"""
    ok_ewma: float = 0.80
    n: int = 0
    last_ts: float = 0.0


class MetricsStore:
    """レイヤー別成功率の記録"""
    
    def __init__(self):
        self._m: Dict[RouteKey, LayerStats] = {}
    
    def get(self, k: RouteKey) -> LayerStats:
        return self._m.get(k, LayerStats())
    
    def update(self, k: RouteKey, ok: bool, alpha: float = 0.25) -> None:
        st = self._m.get(k)
        if st is None:
            st = LayerStats()
            self._m[k] = st
        st.n += 1
        st.last_ts = time.time()
        x = 1.0 if ok else 0.0
        st.ok_ewma = (1 - alpha) * st.ok_ewma + alpha * x


class HealthChecker:
    """レイヤーの健全性をチェック"""
    
    def check(self, ctx: Any, layer: Layer) -> Health:
        t0 = time.time()
        try:
            if layer == Layer.CDP:
                if not getattr(ctx, "page", None):
                    return Health(False, "page_missing")
                _ = getattr(ctx.page, "url", None)
                return Health(True, "ok", (time.time() - t0) * 1000)
            
            if layer == Layer.UIA:
                if not getattr(ctx, "uia", None):
                    return Health(False, "uia_missing")
                _ = ctx.uia.rectangle()
                return Health(True, "ok", (time.time() - t0) * 1000)
            
            if layer == Layer.PIXEL:
                grab = getattr(ctx, "screen_grab", None)
                if not grab:
                    return Health(False, "screen_grab_missing")
                _ = grab()
                return Health(True, "ok", (time.time() - t0) * 1000)
            
            return Health(False, "unknown_layer")
        except Exception as e:
            return Health(False, f"exception:{repr(e)}", (time.time() - t0) * 1000)


class Resolver(Protocol):
    """ロケータ解決インターフェース"""
    def resolve_for_layer(
        self,
        ctx: Any,
        locator_key: str,
        screen_key: str,
        layer: Layer,
    ) -> Dict[str, Any]: ...


@dataclass
class RouteDecision:
    """ルーティング決定"""
    layer: Layer
    locator: Dict[str, Any]
    score: float
    health: Health
    stats: LayerStats


class Router:
    """
    レイヤー選択ロジック
    
    特徴:
    - CDP > UIA > Pixel の基本優先度
    - 健全性、成功率、コストでスコアリング
    - 失敗時のフォールバック戦略
    """
    
    def __init__(
        self,
        health_checker: HealthChecker,
        metrics: MetricsStore,
        resolver: Resolver,
        base_priority: Optional[Dict[Layer, float]] = None,
    ):
        self.health_checker = health_checker
        self.metrics = metrics
        self.resolver = resolver
        self.base_priority = base_priority or {
            Layer.CDP: 1.00,
            Layer.UIA: 0.85,
            Layer.PIXEL: 0.55,
        }
    
    def choose_order(
        self,
        ctx: Any,
        locator_key: str,
        action_kind: str,
    ) -> List[RouteDecision]:
        """候補レイヤーをスコア順にソート"""
        sk = getattr(ctx, "screen_key", "unknown")
        candidates: List[RouteDecision] = []
        
        for layer in (Layer.CDP, Layer.UIA, Layer.PIXEL):
            h = self.health_checker.check(ctx, layer)
            if not h.ok:
                continue
            
            # 高リスク時はPixelを抑制
            if layer == Layer.PIXEL:
                risk = getattr(ctx, "risk_level", 0)
                if risk >= 90 and not getattr(ctx, "pixel_allowed", False):
                    continue
            
            # ロケータ解決
            try:
                loc = self.resolver.resolve_for_layer(ctx, locator_key, sk, layer)
            except Exception:
                continue
            
            rk = RouteKey(sk, locator_key, action_kind, layer)
            st = self.metrics.get(rk)
            
            # コスト（CDPが最安）
            cost = {Layer.CDP: 1.0, Layer.UIA: 1.2, Layer.PIXEL: 1.6}[layer]
            
            # 健全性重み（レイテンシ）
            health_w = 1.0 if h.latency_ms <= 50 else 0.9 if h.latency_ms <= 150 else 0.75
            
            # 成功確率（EWMA）
            p = max(0.05, min(0.99, st.ok_ewma))
            cold_penalty = 0.9 if st.n < 5 else 1.0
            
            score = self.base_priority[layer] * health_w * p * cold_penalty / cost
            candidates.append(RouteDecision(layer, loc, score, h, st))
        
        candidates.sort(key=lambda d: d.score, reverse=True)
        return candidates
    
    def get_best_layer(
        self,
        ctx: Any,
        locator_key: str,
        action_kind: str,
    ) -> Optional[RouteDecision]:
        """最適なレイヤーを返す"""
        order = self.choose_order(ctx, locator_key, action_kind)
        return order[0] if order else None
