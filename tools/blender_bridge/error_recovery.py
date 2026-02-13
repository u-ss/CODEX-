"""
エラーリカバリモジュール - Blenderクラッシュの検知・分類・フォールバック

検知パターン:
- GPU OOM (CUDA/HIP/OptiX メモリ不足)
- GPUドライバエラー (デバイスロスト、vkQueueSubmit失敗等)
- 汎用メモリ不足
- 不明なクラッシュ

リカバリ戦略:
1. GPU OOM → サンプル数/解像度を削減
2. GPUドライバ → CPUフォールバック
3. 不明 → CPUフォールバック + サンプル数削減
"""

import re
from pathlib import Path
from typing import Optional


# ---- ログパターン（正規表現） ----

GPU_OOM_PATTERNS = re.compile(
    r"(out of memory|CUDA error:.*out of memory|"
    r"HIP error:.*out of memory|"
    r"OptiX.*out of memory|"
    r"OPTIX_ERROR_OUT_OF_MEMORY|"
    r"cudaErrorMemoryAllocation)",
    re.IGNORECASE
)

GPU_DRIVER_PATTERNS = re.compile(
    r"(device lost|driver|vkQueueSubmit|"
    r"CUDA error|HIP error|"
    r"OPTIX_ERROR|"
    r"GPU error|"
    r"Segmentation fault|"
    r"Access violation)",
    re.IGNORECASE
)

OOM_PATTERNS = re.compile(
    r"(out of memory|MemoryError|bad_alloc|"
    r"cannot allocate|allocation failed)",
    re.IGNORECASE
)


def classify_failure(log_path: str) -> str:
    """
    ログファイルからエラーを分類

    Args:
        log_path: Blenderのログファイルパス

    Returns:
        "gpu_oom" | "gpu_driver" | "oom" | "unknown"
    """
    p = Path(log_path)
    if not p.exists():
        return "unknown"

    try:
        text = p.read_text(errors="replace")
    except Exception:
        return "unknown"

    # 優先度順に判定（具体的なものから）
    if GPU_OOM_PATTERNS.search(text):
        return "gpu_oom"
    if GPU_DRIVER_PATTERNS.search(text):
        return "gpu_driver"
    if OOM_PATTERNS.search(text):
        return "oom"

    return "unknown"


def get_fallback_policy(current_policy: dict, failure_type: str) -> dict:
    """
    エラー分類に基づいてフォールバックポリシーを生成

    Args:
        current_policy: 現在のレンダリングポリシー {"device": str, "samples": int, ...}
        failure_type: classify_failureの出力

    Returns:
        更新されたポリシー
    """
    policy = dict(current_policy)

    if failure_type == "gpu_oom":
        # GPU OOM: まずサンプル数を半減、それでもダメならCPUへ
        if policy.get("device") == "GPU":
            new_samples = max(16, policy.get("samples", 128) // 2)
            if new_samples == policy.get("samples"):
                # すでに最小なのでCPUフォールバック
                policy["device"] = "CPU"
            else:
                policy["samples"] = new_samples
        else:
            # 既にCPU: サンプルをさらに減らす
            policy["samples"] = max(8, policy.get("samples", 64) // 2)

    elif failure_type == "gpu_driver":
        # GPUドライバエラー: CPUに切替
        policy["device"] = "CPU"

    elif failure_type == "oom":
        # 汎用OOM: サンプル削減 + 解像度削減
        policy["samples"] = max(16, policy.get("samples", 128) // 2)
        if "resolution_x" in policy:
            policy["resolution_x"] = max(640, policy["resolution_x"] // 2)
        if "resolution_y" in policy:
            policy["resolution_y"] = max(360, policy["resolution_y"] // 2)

    else:  # unknown
        # 不明: CPUフォールバック + サンプル削減
        policy["device"] = "CPU"
        policy["samples"] = max(16, policy.get("samples", 128) // 2)

    return policy


def check_process_health(proc, timeout_s: float = 5.0) -> dict:
    """
    プロセスの健全性チェック

    Args:
        proc: subprocess.Popen オブジェクト
        timeout_s: チェックのタイムアウト

    Returns:
        {"alive": bool, "returncode": int|None}
    """
    if proc is None:
        return {"alive": False, "returncode": None}

    rc = proc.poll()
    if rc is None:
        return {"alive": True, "returncode": None}
    else:
        return {"alive": False, "returncode": rc}


def find_latest_checkpoint(work_dir: str, pattern: str = "scene_step_*.blend") -> Optional[str]:
    """
    最新のチェックポイント.blendファイルを検索

    Args:
        work_dir: 作業ディレクトリ
        pattern: 検索パターン

    Returns:
        最新のチェックポイントファイルパス、なければNone
    """
    p = Path(work_dir)
    checkpoints = sorted(p.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
    if checkpoints:
        return str(checkpoints[0])
    return None
