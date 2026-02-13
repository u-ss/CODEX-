"""
画像好み判定ツール Web サーバー

Flask ベースの API サーバー。
学習・判定・分析の各エンドポイントを提供する。
"""

import json
import sys
import os
import base64
import tempfile
from pathlib import Path
from io import BytesIO

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

# モジュールパスを追加
sys.path.insert(0, str(Path(__file__).parent))

from preference_learner import PreferenceLearner, IMAGE_EXTENSIONS
from preference_judge import PreferenceJudge
from preference_analyzer import PreferenceAnalyzer, refine_reason_text, describe_image_features
from reason_store import ReasonStore

app = Flask(__name__, static_folder="static")

# 設定
BASE_DIR = Path(__file__).parent
TRAINING_DIR = BASE_DIR / "training_data"
MODEL_PATH = BASE_DIR / "models" / "preference_model.pkl"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 理由テキストストア
reason_store = ReasonStore(TRAINING_DIR / "reasons.json")


@app.route("/")
def index():
    """メインページ"""
    return send_from_directory("static", "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    """静的ファイル配信"""
    return send_from_directory("static", filename)


@app.route("/api/status", methods=["GET"])
def status():
    """システム状態を返す"""
    model_exists = MODEL_PATH.exists()
    image_counts = {}
    for label in ["好き", "そうでもない", "嫌い"]:
        folder = TRAINING_DIR / label
        if folder.exists():
            count = sum(
                1 for f in folder.iterdir()
                if f.suffix.lower() in IMAGE_EXTENSIONS
            )
            image_counts[label] = count
        else:
            image_counts[label] = 0

    return jsonify({
        "model_ready": model_exists,
        "image_counts": image_counts,
        "total_images": sum(image_counts.values()),
    })


@app.route("/api/train", methods=["POST"])
def train():
    """モデルを学習する"""
    try:
        learner = PreferenceLearner(TRAINING_DIR)
        stats = learner.train(model_save_path=MODEL_PATH)
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/judge", methods=["POST"])
def judge():
    """画像を判定する"""
    if not MODEL_PATH.exists():
        return jsonify({"error": "モデルが未学習です。先に学習を実行してください。"}), 400

    if "image" not in request.files:
        return jsonify({"error": "画像ファイルが必要です"}), 400

    file = request.files["image"]
    if not file.filename:
        return jsonify({"error": "ファイル名が空です"}), 400

    # 一時ファイルに保存して判定（Windows対応: ファイルを閉じてから処理）
    suffix = Path(file.filename).suffix
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    try:
        os.close(tmp_fd)  # ファイルディスクリプタを閉じる
        file.save(tmp_path)
        j = PreferenceJudge(MODEL_PATH)
        result = j.judge(tmp_path)
        # 特徴量は大きいのでサマリーのみ返す
        result.pop("features", None)
        return jsonify({"success": True, "result": result})
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.route("/api/analyze", methods=["GET"])
def analyze():
    """好み傾向を分析する"""
    model_path = MODEL_PATH if MODEL_PATH.exists() else None
    analyzer = PreferenceAnalyzer(TRAINING_DIR, model_path)
    result = analyzer.analyze()
    return jsonify({"success": True, "analysis": result})


@app.route("/api/upload", methods=["POST"])
def upload():
    """トレーニング用画像をアップロードする"""
    category = request.form.get("category")
    if category not in ["好き", "そうでもない", "嫌い"]:
        return jsonify({"error": "カテゴリが不正です"}), 400

    if "images" not in request.files:
        return jsonify({"error": "画像ファイルが必要です"}), 400

    files = request.files.getlist("images")
    reason = request.form.get("reason", "").strip()
    saved = []
    for file in files:
        if file.filename:
            filename = secure_filename(file.filename)
            # 日本語ファイル名対応
            if not filename or filename == "_":
                filename = file.filename
            dest = TRAINING_DIR / category / filename
            file.save(str(dest))
            saved.append(filename)
            # 理由があれば保存
            if reason:
                reason_store.save_reason(category, filename, reason)

    return jsonify({"success": True, "uploaded": saved, "count": len(saved)})


@app.route("/api/training-images", methods=["GET"])
def training_images():
    """トレーニング画像の一覧を返す"""
    result = {}
    for label in ["好き", "そうでもない", "嫌い"]:
        folder = TRAINING_DIR / label
        if folder.exists():
            images = []
            for f in sorted(folder.iterdir()):
                if f.suffix.lower() in IMAGE_EXTENSIONS:
                    reason = reason_store.get_reason(label, f.name)
                    images.append({
                        "name": f.name,
                        "path": f"api/training-image/{label}/{f.name}",
                        "reason": reason or "",
                    })
            result[label] = images
        else:
            result[label] = []
    return jsonify(result)


@app.route("/api/training-image/<category>/<filename>")
def training_image(category, filename):
    """トレーニング画像を返す"""
    folder = TRAINING_DIR / category
    return send_from_directory(str(folder), filename)


@app.route("/api/reason", methods=["POST"])
def save_reason():
    """理由を保存する"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSONデータが必要です"}), 400

    category = data.get("category")
    filename = data.get("filename")
    reason = data.get("reason", "").strip()

    if category not in ["好き", "そうでもない", "嫌い"]:
        return jsonify({"error": "カテゴリが不正です"}), 400
    if not filename:
        return jsonify({"error": "ファイル名が必要です"}), 400

    if reason:
        reason_store.save_reason(category, filename, reason)
    else:
        reason_store.delete_reason(category, filename)

    return jsonify({"success": True})


@app.route("/api/reasons", methods=["GET"])
def get_reasons():
    """全理由を取得する"""
    return jsonify(reason_store.get_all_reasons())


@app.route("/api/reason", methods=["DELETE"])
def delete_reason():
    """理由を削除する"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSONデータが必要です"}), 400

    category = data.get("category")
    filename = data.get("filename")

    deleted = reason_store.delete_reason(category, filename)
    return jsonify({"success": deleted})


@app.route("/api/refine-reason", methods=["POST"])
def refine_reason():
    """理由テキストをAIが校正・言語化する"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSONデータが必要です"}), 400

    raw_text = data.get("text", "").strip()
    category = data.get("category", "好き")

    if not raw_text:
        return jsonify({"error": "テキストが必要です"}), 400

    result = refine_reason_text(raw_text, category)
    return jsonify({"success": True, **result})


@app.route("/api/describe-image", methods=["POST"])
def describe_image():
    """画像の理由テキストを返す（AI分析済みテキスト優先、なければ自動生成）"""
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSONデータが必要です"}), 400

    category = data.get("category", "好き")
    filename = data.get("filename", "")

    if category not in ["好き", "そうでもない", "嫌い"]:
        return jsonify({"error": "カテゴリが不正です"}), 400
    if not filename:
        return jsonify({"error": "ファイル名が必要です"}), 400

    image_path = TRAINING_DIR / category / filename
    if not image_path.exists():
        return jsonify({"error": "画像が見つかりません"}), 404

    # 既存のAI分析テキストがあればそれを優先
    existing = reason_store.get_reason(category, filename)
    if existing:
        return jsonify({
            "success": True,
            "description": existing,
            "source": "ai_analyzed",
        })

    # フォールバック: feature_extractorベースの自動生成
    try:
        result = describe_image_features(str(image_path), category)
        result["source"] = "auto_generated"
        return jsonify({"success": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("🎨 画像好み判定ツール サーバー起動中...")
    print(f"📁 トレーニングデータ: {TRAINING_DIR}")
    print(f"🧠 モデル: {MODEL_PATH}")
    print(f"🌐 http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
