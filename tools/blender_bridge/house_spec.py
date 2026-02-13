"""
house_spec.py - 戸建て生成用の仕様正規化と修正ロジック

責務:
- prompt + フォーム入力を正規化して `house_spec` を生成
- mm/cm/m/坪 の単位をメートル基準へ変換
- 必須キーの妥当性チェック
- validator が返す repair_actions の適用
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Dict, List, Optional, Tuple

TSUBO_TO_M2 = 3.305785

_NUMBER_UNIT_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*([a-zA-Z\u3040-\u30ff\u4e00-\u9fff0-9/%]*)")


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_length_m(value: Any) -> Optional[float]:
    """長さをメートルへ変換する。未指定単位は m 扱い。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    m = _NUMBER_UNIT_RE.search(text)
    if not m:
        return None

    num = float(m.group(1))
    unit = m.group(2)

    if unit in ("", "m", "meter", "meters", "メートル"):
        return num
    if unit in ("cm", "centimeter", "centimeters", "センチ"):
        return num / 100.0
    if unit in ("mm", "millimeter", "millimeters", "ミリ"):
        return num / 1000.0
    if unit in ("km",):
        return num * 1000.0

    # 未知単位は m とみなす
    return num


def parse_area_m2(value: Any) -> Optional[float]:
    """面積を平方メートルへ変換する。坪・m2・㎡を解釈。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    m = _NUMBER_UNIT_RE.search(text)
    if not m:
        return None

    num = float(m.group(1))
    unit = m.group(2)
    if "坪" in text:
        return num * TSUBO_TO_M2
    if unit in ("m2", "㎡", "平方メートル", ""):
        return num
    if unit in ("cm2", "平方センチメートル"):
        return num / 10000.0
    return num


def _parse_roof_pitch(value: Any) -> Optional[float]:
    """勾配を rise/run 比で返す。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().lower()
    if not text:
        return None

    # 4寸 -> 0.4
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*寸", text)
    if m:
        return float(m.group(1)) / 10.0

    # 30度 -> tan(30deg)
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*度", text)
    if m:
        deg = float(m.group(1))
        return math.tan(math.radians(deg))

    # 0.4 or 勾配:0.4
    m = re.search(r"(-?\d+(?:\.\d+)?)", text)
    if m:
        return float(m.group(1))
    return None


def _parse_float_list(value: Any) -> Optional[List[float]]:
    if value is None:
        return None
    if isinstance(value, list):
        out: List[float] = []
        for v in value:
            fv = parse_length_m(v)
            if fv is None:
                return None
            out.append(float(fv))
        return out
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parts = [p.strip() for p in re.split(r"[,\s/]+", text) if p.strip()]
        out = []
        for p in parts:
            fv = parse_length_m(p)
            if fv is None:
                return None
            out.append(float(fv))
        return out if out else None
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def _parse_prompt(prompt: str) -> Dict[str, Any]:
    if not prompt:
        return {}

    text = prompt.lower()
    out: Dict[str, Any] = {}

    # 階数
    if any(k in prompt for k in ("平屋", "1階建", "一階建", "single story")):
        out["floors"] = 1
    elif any(k in prompt for k in ("3階", "三階", "3f")):
        out["floors"] = 3
    elif any(k in prompt for k in ("2階", "二階", "2f")):
        out["floors"] = 2

    # 屋根タイプ
    if any(k in prompt for k in ("片流れ", "shed")):
        out["roof_type"] = "shed"
    elif any(k in prompt for k in ("寄棟", "hip")):
        out["roof_type"] = "hip"
    elif any(k in prompt for k in ("切妻", "gable")):
        out["roof_type"] = "gable"

    # 品質モード
    if any(k in prompt for k in ("フォトリアル", "photoreal", "高品質", "リアル")):
        out["quality_mode"] = "photoreal"
    elif any(k in prompt for k in ("高速", "quick", "draft", "ラフ")):
        out["quality_mode"] = "draft"

    # WxD
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(mm|cm|m|メートル)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|メートル)?",
        text,
    )
    if m:
        left = parse_length_m(f"{m.group(1)}{m.group(2) or 'm'}")
        right = parse_length_m(f"{m.group(3)}{m.group(4) or m.group(2) or 'm'}")
        if left is not None:
            out["footprint_w_m"] = left
        if right is not None:
            out["footprint_d_m"] = right

    # 幅/奥行
    m = re.search(r"(?:幅|間口|width)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m|メートル)?)", prompt.lower())
    if m:
        val = parse_length_m(m.group(1))
        if val is not None:
            out["footprint_w_m"] = val
    m = re.search(r"(?:奥行|奥行き|depth)\s*[:=]?\s*([0-9.]+\s*(?:mm|cm|m|メートル)?)", prompt.lower())
    if m:
        val = parse_length_m(m.group(1))
        if val is not None:
            out["footprint_d_m"] = val

    # 勾配
    m = re.search(r"(?:勾配|roof\s*pitch)\s*[:=]?\s*([0-9.]+\s*(?:寸|度)?)", prompt.lower())
    if m:
        pitch = _parse_roof_pitch(m.group(1))
        if pitch is not None:
            out["roof_pitch"] = pitch
    else:
        m = re.search(r"([0-9.]+)\s*寸", prompt)
        if m:
            out["roof_pitch"] = float(m.group(1)) / 10.0

    # 坪
    m = re.search(r"([0-9.]+)\s*坪", prompt)
    if m:
        out["floor_area_m2"] = float(m.group(1)) * TSUBO_TO_M2

    return out


def _pick(source: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _clamp(value: float, lo: float, hi: float, key: str, assumptions: List[str]) -> float:
    if value < lo:
        assumptions.append(f"{key} を最小値 {lo} に補正")
        return lo
    if value > hi:
        assumptions.append(f"{key} を最大値 {hi} に補正")
        return hi
    return value


def normalize_house_spec(prompt: str, form_data: Optional[Dict[str, Any]], preset: Dict[str, Any]) -> Dict[str, Any]:
    """
    prompt + フォーム + プリセットから正規化仕様を作成する。
    """
    form_data = form_data or {}
    parsed_prompt = _parse_prompt(prompt)
    assumptions: List[str] = []

    combined = dict(parsed_prompt)
    combined.update(form_data)

    spec = copy.deepcopy(preset)
    spec["style_preset"] = str(preset.get("style_preset", "JP_WOOD_2F_STANDARD"))

    floors = _coerce_int(_pick(combined, ["floors", "stories", "階数"])) or int(spec.get("floors", 2))
    floors = int(_clamp(float(floors), 1, 3, "floors", assumptions))
    spec["floors"] = floors

    quality = str(_pick(combined, ["quality_mode", "quality", "qualityMode"]) or spec.get("quality_mode", "balanced")).lower()
    if quality not in ("draft", "balanced", "photoreal"):
        assumptions.append("quality_mode が未知値のため balanced を使用")
        quality = "balanced"
    spec["quality_mode"] = quality

    roof_type = str(_pick(combined, ["roof_type", "roof", "屋根"]) or spec.get("roof_type", "gable")).lower()
    if roof_type not in ("gable", "hip", "shed"):
        assumptions.append("roof_type が未知値のため gable を使用")
        roof_type = "gable"
    spec["roof_type"] = roof_type

    # 坪入力があれば footprint を再計算（幅/奥行が明示指定されていない場合）
    area_m2 = parse_area_m2(_pick(combined, ["floor_area_m2", "floor_area", "延床面積"]))
    explicit_w = parse_length_m(_pick(combined, ["footprint_w_m", "width_m", "width", "間口"]))
    explicit_d = parse_length_m(_pick(combined, ["footprint_d_m", "depth_m", "depth", "奥行"]))

    if area_m2 is not None and (explicit_w is None or explicit_d is None):
        ratio = float(spec.get("footprint_w_m", 8.0)) / max(float(spec.get("footprint_d_m", 6.0)), 0.1)
        footprint_area = area_m2 / max(floors, 1)
        derived_w = math.sqrt(max(footprint_area, 1.0) * ratio)
        derived_d = footprint_area / max(derived_w, 0.1)
        assumptions.append("坪指定から footprint を自動算出")
        if explicit_w is None:
            explicit_w = derived_w
        if explicit_d is None:
            explicit_d = derived_d

    if explicit_w is None:
        assumptions.append("footprint_w_m 未指定のためプリセット値を使用")
        explicit_w = float(spec.get("footprint_w_m", 8.0))
    if explicit_d is None:
        assumptions.append("footprint_d_m 未指定のためプリセット値を使用")
        explicit_d = float(spec.get("footprint_d_m", 6.0))

    spec["footprint_w_m"] = _clamp(float(explicit_w), 4.0, 20.0, "footprint_w_m", assumptions)
    spec["footprint_d_m"] = _clamp(float(explicit_d), 4.0, 20.0, "footprint_d_m", assumptions)

    roof_pitch = _parse_roof_pitch(_pick(combined, ["roof_pitch", "pitch", "勾配"]))
    if roof_pitch is None:
        roof_pitch = float(spec.get("roof_pitch", 0.4))
        assumptions.append("roof_pitch 未指定のためプリセット値を使用")
    spec["roof_pitch"] = _clamp(float(roof_pitch), 0.2, 0.7, "roof_pitch", assumptions)

    # 寸法系
    wall_t = parse_length_m(_pick(combined, ["wall_thickness_m", "wall_thickness", "壁厚"])) or float(spec.get("wall_thickness_m", 0.18))
    eaves = parse_length_m(_pick(combined, ["eaves_m", "eaves", "軒"])) or float(spec.get("eaves_m", 0.45))
    foundation_h = parse_length_m(_pick(combined, ["foundation_h_m", "foundation_h", "基礎高"])) or float(spec.get("foundation_h_m", 0.4))
    slab_t = parse_length_m(_pick(combined, ["floor_slab_t_m", "floor_slab_t", "床厚"])) or float(spec.get("floor_slab_t_m", 0.25))
    door_h = parse_length_m(_pick(combined, ["door_height_m", "door_h", "ドア高"])) or float(spec.get("door_height_m", 2.1))
    door_w = parse_length_m(_pick(combined, ["door_width_m", "door_w", "ドア幅"])) or float(spec.get("door_width_m", 0.9))
    win_h = parse_length_m(_pick(combined, ["window_height_m", "window_h", "窓高"])) or float(spec.get("window_height_m", 1.0))
    win_w = parse_length_m(_pick(combined, ["window_width_m", "window_w", "窓幅"])) or float(spec.get("window_width_m", 1.2))

    spec["wall_thickness_m"] = _clamp(float(wall_t), 0.09, 0.30, "wall_thickness_m", assumptions)
    spec["eaves_m"] = _clamp(float(eaves), 0.2, 0.9, "eaves_m", assumptions)
    spec["foundation_h_m"] = _clamp(float(foundation_h), 0.2, 0.8, "foundation_h_m", assumptions)
    spec["floor_slab_t_m"] = _clamp(float(slab_t), 0.1, 0.5, "floor_slab_t_m", assumptions)
    spec["door_height_m"] = _clamp(float(door_h), 1.8, 2.4, "door_height_m", assumptions)
    spec["door_width_m"] = _clamp(float(door_w), 0.7, 1.2, "door_width_m", assumptions)
    spec["window_height_m"] = _clamp(float(win_h), 0.5, 2.4, "window_height_m", assumptions)
    spec["window_width_m"] = _clamp(float(win_w), 0.5, 2.4, "window_width_m", assumptions)

    floor_heights = _parse_float_list(_pick(combined, ["floor_heights_m", "floor_heights", "階高"]))
    if not floor_heights:
        floor_heights = [float(x) for x in spec.get("floor_heights_m", [2.6, 2.4])]
        assumptions.append("floor_heights_m 未指定のためプリセット値を使用")

    # floors に合わせて補正
    if len(floor_heights) < floors:
        last = floor_heights[-1] if floor_heights else 2.4
        floor_heights = floor_heights + [last] * (floors - len(floor_heights))
        assumptions.append("floor_heights_m を階数に合わせて補完")
    elif len(floor_heights) > floors:
        floor_heights = floor_heights[:floors]
        assumptions.append("floor_heights_m を階数に合わせて切り詰め")

    floor_heights = [_clamp(float(h), 2.0, 3.5, "floor_heights_m", assumptions) for h in floor_heights]
    spec["floor_heights_m"] = floor_heights

    ground_margin = parse_length_m(_pick(combined, ["ground_margin_m", "ground_margin"])) or float(spec.get("ground_margin_m", 8.0))
    spec["ground_margin_m"] = _clamp(float(ground_margin), 2.0, 30.0, "ground_margin_m", assumptions)

    samples = _coerce_int(_pick(combined, ["samples", "render_samples"])) or int(spec.get("samples", 128))
    spec["samples"] = int(_clamp(float(samples), 16, 1024, "samples", assumptions))

    spec["assumptions"] = assumptions
    spec["source_prompt"] = prompt
    return spec


def validate_spec(spec: Dict[str, Any]) -> List[str]:
    """必須キーと基本レンジを検証する。"""
    required = [
        "style_preset",
        "floors",
        "footprint_w_m",
        "footprint_d_m",
        "roof_type",
        "roof_pitch",
        "quality_mode",
    ]
    errors: List[str] = []
    for key in required:
        if key not in spec:
            errors.append(f"required key missing: {key}")

    floors = _coerce_int(spec.get("floors"))
    if floors is None or floors < 1 or floors > 3:
        errors.append("floors must be between 1 and 3")

    for key, lo, hi in (
        ("footprint_w_m", 4.0, 20.0),
        ("footprint_d_m", 4.0, 20.0),
        ("roof_pitch", 0.2, 0.7),
        ("wall_thickness_m", 0.09, 0.30),
        ("eaves_m", 0.2, 0.9),
    ):
        if key in spec:
            v = _to_float(spec.get(key))
            if v is None or v < lo or v > hi:
                errors.append(f"{key} out of range [{lo}, {hi}]")

    floor_heights = spec.get("floor_heights_m")
    if not isinstance(floor_heights, list) or not floor_heights:
        errors.append("floor_heights_m must be non-empty list")
    else:
        if floors is not None and len(floor_heights) != floors:
            errors.append("floor_heights_m length must equal floors")
        for i, h in enumerate(floor_heights):
            hv = _to_float(h)
            if hv is None or hv < 2.0 or hv > 3.5:
                errors.append(f"floor_heights_m[{i}] out of range [2.0, 3.5]")

    if str(spec.get("roof_type", "")).lower() not in ("gable", "hip", "shed"):
        errors.append("roof_type must be one of gable/hip/shed")
    if str(spec.get("quality_mode", "")).lower() not in ("draft", "balanced", "photoreal"):
        errors.append("quality_mode must be one of draft/balanced/photoreal")

    return errors


def _get_by_path(data: Dict[str, Any], path: str) -> Any:
    cur: Any = data
    for seg in path.split("."):
        if not isinstance(cur, dict) or seg not in cur:
            return None
        cur = cur[seg]
    return cur


def _set_by_path(data: Dict[str, Any], path: str, value: Any) -> bool:
    cur: Any = data
    segs = path.split(".")
    for seg in segs[:-1]:
        if seg not in cur or not isinstance(cur[seg], dict):
            cur[seg] = {}
        cur = cur[seg]
    cur[segs[-1]] = value
    return True


def apply_repair_actions(spec: Dict[str, Any], actions: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """
    validator の repair_actions を適用する。

    サポート:
    - {"action":"set_value","key":"roof_pitch","value":0.4}
    - {"action":"clamp_range","key":"roof_pitch","min":0.2,"max":0.7}
    - {"action":"scale_value","key":"door_height_m","factor":0.95}
    """
    next_spec = copy.deepcopy(spec)
    applied: List[str] = []

    for item in actions or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("action", "")).strip()
        key = str(item.get("key", "")).strip()
        if not kind or not key:
            continue

        if kind == "set_value":
            if "value" in item:
                _set_by_path(next_spec, key, item["value"])
                applied.append(f"{key} を {item['value']} に設定")
            continue

        if kind == "clamp_range":
            cur = _to_float(_get_by_path(next_spec, key))
            lo = _to_float(item.get("min"))
            hi = _to_float(item.get("max"))
            if cur is None:
                continue
            new_val = cur
            if lo is not None and cur < lo:
                new_val = lo
            if hi is not None and cur > hi:
                new_val = hi
            if new_val != cur:
                _set_by_path(next_spec, key, new_val)
                applied.append(f"{key} をレンジ補正 ({cur} -> {new_val})")
            continue

        if kind == "scale_value":
            cur = _to_float(_get_by_path(next_spec, key))
            factor = _to_float(item.get("factor"))
            if cur is None or factor is None:
                continue
            new_val = cur * factor
            _set_by_path(next_spec, key, new_val)
            applied.append(f"{key} をスケール補正 ({cur} -> {new_val})")
            continue

    return next_spec, applied
