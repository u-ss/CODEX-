"""
レンダリング出力検証スクリプト

レンダリング結果のPNGファイルを検証する:
- ファイル存在確認
- 最小サイズ確認（破損検知）
- PNG形式の妥当性チェック
"""

import struct
import sys
from pathlib import Path


def validate_png(file_path: str, min_size: int = 10000) -> dict:
    """
    PNGファイルの妥当性を検証

    Args:
        file_path: 検証対象のファイルパス
        min_size: 最小ファイルサイズ（バイト）

    Returns:
        {"valid": bool, "path": str, "size": int, "errors": list[str]}
    """
    p = Path(file_path)
    errors = []

    # ファイル存在確認
    if not p.exists():
        return {"valid": False, "path": file_path, "size": 0, "errors": ["ファイルが存在しません"]}

    # サイズ確認
    size = p.stat().st_size
    if size < min_size:
        errors.append(f"ファイルサイズが小さすぎます ({size} bytes < {min_size} bytes)")

    # PNGシグネチャ確認
    PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
    try:
        with open(p, 'rb') as f:
            sig = f.read(8)
            if sig != PNG_SIGNATURE:
                errors.append("PNGシグネチャが不正です")
            else:
                # IHDRチャンク読み取り（幅と高さ）
                chunk_len = struct.unpack('>I', f.read(4))[0]
                chunk_type = f.read(4)
                if chunk_type == b'IHDR' and chunk_len >= 13:
                    width = struct.unpack('>I', f.read(4))[0]
                    height = struct.unpack('>I', f.read(4))[0]
                    if width == 0 or height == 0:
                        errors.append(f"画像サイズが不正です ({width}x{height})")
    except Exception as e:
        errors.append(f"ファイル読み取りエラー: {e}")

    return {
        "valid": len(errors) == 0,
        "path": file_path,
        "size": size,
        "errors": errors
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使用法: python validate_output.py <png_file>")
        sys.exit(1)

    result = validate_png(sys.argv[1])
    if result["valid"]:
        print(f"✅ 検証OK: {result['path']} ({result['size']} bytes)")
    else:
        print(f"❌ 検証NG: {result['path']}")
        for err in result["errors"]:
            print(f"   - {err}")
        sys.exit(1)
