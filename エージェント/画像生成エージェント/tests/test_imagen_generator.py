"""
test_imagen_generator.py - 画像生成エージェントのユニットテスト

テスト対象:
- 設定ファイル読み込み
- プロンプト構築ロジック
- GCP認証チェック
- 画像生成フロー（APIモック使用）
"""
import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


# ============================================================
# 設定ファイル読み込みテスト
# ============================================================
class TestConfigLoading:
    """設定ファイルの読み込みと検証テスト"""

    def test_デフォルト設定ファイルが存在する(self):
        """config/imagen_config.json が存在すること"""
        config_path = PROJECT_ROOT / "config" / "imagen_config.json"
        assert config_path.exists(), f"設定ファイルが見つかりません: {config_path}"

    def test_設定ファイルが有効なJSONである(self):
        """設定ファイルがパース可能なJSONであること"""
        config_path = PROJECT_ROOT / "config" / "imagen_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert isinstance(config, dict)

    def test_必須フィールドが存在する(self):
        """project_id, location, model が設定に含まれること"""
        config_path = PROJECT_ROOT / "config" / "imagen_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert "project_id" in config
        assert "location" in config
        assert "model" in config

    def test_デフォルトパラメータが存在する(self):
        """defaults セクションに必要なパラメータがあること"""
        config_path = PROJECT_ROOT / "config" / "imagen_config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        defaults = config.get("defaults", {})
        assert "aspect_ratio" in defaults
        assert "count" in defaults
        assert "safety_filter_level" in defaults

    def test_load_config関数(self):
        """load_config() が正常に設定を返すこと"""
        from imagen_generator import load_config
        config = load_config()
        assert config["model"] == "imagen-3.0-generate-002"
        assert config["location"] == "us-central1"

    def test_カスタムパス指定(self, tmp_path):
        """カスタムパスの設定ファイルを読み込めること"""
        from imagen_generator import load_config
        custom_config = {
            "project_id": "test-project",
            "location": "asia-northeast1",
            "model": "imagen-3.0-generate-002",
            "defaults": {
                "aspect_ratio": "1:1",
                "count": 1,
                "safety_filter_level": "block_most"
            }
        }
        config_file = tmp_path / "test_config.json"
        config_file.write_text(json.dumps(custom_config), encoding="utf-8")
        loaded = load_config(str(config_file))
        assert loaded["project_id"] == "test-project"
        assert loaded["location"] == "asia-northeast1"


# ============================================================
# プロンプト構築テスト
# ============================================================
class TestPromptBuilder:
    """shot_list からImagen用プロンプトを構築するテスト"""

    def test_基本テキストからプロンプト生成(self):
        """シンプルなテキストからプロンプトが生成されること"""
        from prompt_builder import build_prompt_from_shot
        shot = {
            "shot_id": "s01",
            "text": "夜の東京の街並み、ネオンが光る",
            "video": {"storyboard": "cyberpunk cityscape"}
        }
        prompt = build_prompt_from_shot(shot, style="cinematic")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_storyboard情報が反映される(self):
        """video.storyboard の内容がプロンプトに含まれること"""
        from prompt_builder import build_prompt_from_shot
        shot = {
            "shot_id": "s02",
            "text": "桜が舞う公園",
            "video": {"storyboard": "cherry blossom park, spring, petals falling"}
        }
        prompt = build_prompt_from_shot(shot, style="photorealistic")
        assert "cherry blossom" in prompt.lower() or "sakura" in prompt.lower()

    def test_スタイル指定が反映される(self):
        """style パラメータがプロンプトに含まれること"""
        from prompt_builder import build_prompt_from_shot
        shot = {
            "shot_id": "s03",
            "text": "テスト",
            "video": {}
        }
        prompt = build_prompt_from_shot(shot, style="anime")
        assert "anime" in prompt.lower()

    def test_空テキストの処理(self):
        """text が空でもエラーにならないこと"""
        from prompt_builder import build_prompt_from_shot
        shot = {
            "shot_id": "s04",
            "text": "",
            "video": {"storyboard": "mountain landscape"}
        }
        prompt = build_prompt_from_shot(shot, style="cinematic")
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_shot_listからバッチプロンプト生成(self):
        """shot_list 全体からプロンプト一覧を生成できること"""
        from prompt_builder import build_prompts_from_shotlist
        shot_list = {
            "shots": [
                {"shot_id": "s01", "text": "夜の街", "video": {"storyboard": "night city"}},
                {"shot_id": "s02", "text": "朝の海", "video": {"storyboard": "morning ocean"}},
            ]
        }
        prompts = build_prompts_from_shotlist(shot_list, style="cinematic")
        assert len(prompts) == 2
        assert all("shot_id" in p and "prompt" in p for p in prompts)

    def test_アスペクト比の自動判定(self):
        """動画用ショットなら16:9が推奨されること"""
        from prompt_builder import suggest_aspect_ratio
        # 動画素材用
        assert suggest_aspect_ratio("video") == "16:9"
        # ポートレート用
        assert suggest_aspect_ratio("portrait") == "9:16"
        # デフォルト
        assert suggest_aspect_ratio("general") == "1:1"


# ============================================================
# GCP認証テスト
# ============================================================
class TestGCPAuth:
    """GCP認証チェックのテスト（APIは呼ばない）"""

    def test_認証情報未設定でエラー(self):
        """GOOGLE_APPLICATION_CREDENTIALS が未設定の場合にエラーを返すこと"""
        from imagen_generator import verify_gcp_auth
        # GCP関連の環境変数だけを除去（全クリアするとPath.home()が壊れる）
        env_overrides = {"GOOGLE_APPLICATION_CREDENTIALS": ""}
        with patch.dict(os.environ, env_overrides):
            # ADCファイルも存在しないパスをモック
            with patch("imagen_generator.Path.home", return_value=Path("/nonexistent")):
                result = verify_gcp_auth(config={"project_id": "test", "credentials_path": ""})
                assert result["status"] == "error"
                assert "credentials" in result["message"].lower() or "認証" in result["message"]

    def test_project_id未設定でエラー(self):
        """project_id がデフォルト値のままならエラーを返すこと"""
        from imagen_generator import verify_gcp_auth
        result = verify_gcp_auth(config={
            "project_id": "YOUR_GCP_PROJECT_ID",
            "credentials_path": ""
        })
        assert result["status"] == "error"

    def test_正常な設定でOK(self, tmp_path):
        """有効な設定ならstatusがokであること（API呼び出しなし）"""
        from imagen_generator import verify_gcp_auth
        # ダミーの認証ファイルを作成
        creds_file = tmp_path / "fake_creds.json"
        creds_file.write_text('{"type": "service_account"}', encoding="utf-8")
        result = verify_gcp_auth(config={
            "project_id": "my-real-project",
            "credentials_path": str(creds_file)
        })
        assert result["status"] == "ok"


# ============================================================
# 画像生成フローテスト（APIモック）
# ============================================================
class TestImageGeneration:
    """画像生成のフローテスト（Vertex AI APIをモック）"""

    @patch("imagen_generator.ImageGenerationModel")
    def test_単発生成フロー(self, mock_model_class, tmp_path):
        """プロンプトから画像を生成し、ファイルが保存されること"""
        from imagen_generator import generate_from_prompt

        # モック画像を準備
        mock_image = MagicMock()
        mock_image._image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_response = MagicMock()
        mock_response.images = [mock_image]
        mock_model = MagicMock()
        mock_model.generate_images.return_value = mock_response
        mock_model_class.from_pretrained.return_value = mock_model

        config = {
            "project_id": "test-project",
            "location": "us-central1",
            "model": "imagen-3.0-generate-002",
            "defaults": {
                "aspect_ratio": "16:9",
                "count": 1,
                "safety_filter_level": "block_few",
                "person_generation": "allow_adult"
            }
        }

        result = generate_from_prompt(
            prompt="test prompt",
            output_dir=str(tmp_path),
            config=config,
            _model_instance=mock_model
        )
        assert result["status"] == "success"
        assert len(result["files"]) > 0

    @patch("imagen_generator.ImageGenerationModel")
    def test_バッチ生成フロー(self, mock_model_class, tmp_path):
        """shot_list からバッチで画像を生成できること"""
        from imagen_generator import generate_from_shotlist

        # モック
        mock_image = MagicMock()
        mock_image._image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_response = MagicMock()
        mock_response.images = [mock_image, mock_image]
        mock_model = MagicMock()
        mock_model.generate_images.return_value = mock_response
        mock_model_class.from_pretrained.return_value = mock_model

        shot_list = {
            "shots": [
                {"shot_id": "s01", "text": "夜の街", "video": {"storyboard": "night city"}},
                {"shot_id": "s02", "text": "朝の海", "video": {"storyboard": "morning sea"}},
            ]
        }

        config = {
            "project_id": "test-project",
            "location": "us-central1",
            "model": "imagen-3.0-generate-002",
            "defaults": {
                "aspect_ratio": "16:9",
                "count": 2,
                "safety_filter_level": "block_few",
                "person_generation": "allow_adult"
            }
        }

        result = generate_from_shotlist(
            shot_list=shot_list,
            output_dir=str(tmp_path),
            config=config,
            style="cinematic",
            _model_instance=mock_model
        )
        assert result["status"] == "success"
        assert result["total_shots"] == 2
        assert result["total_images"] >= 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
