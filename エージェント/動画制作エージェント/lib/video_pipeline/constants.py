from __future__ import annotations

STEP_IDS = [
    "d1_direct",
    "a1_validate",
    "a2_collect",
    "a3_probe",
    "a4_tts",
    "a5_timing",
    "a6_props",
    "a7_render",
    "a8_mix",
    "a9_finalize",
]

STEP_LABELS = {
    "d1_direct": "D1 VideoDirector",
    "a1_validate": "A1 ShotListValidator",
    "a2_collect": "A2 AssetCollector",
    "a3_probe": "A3 MediaProbe",
    "a4_tts": "A4 VoiceVoxTTS",
    "a5_timing": "A5 TimingBuilder",
    "a6_props": "A6 RemotionPropsBuilder",
    "a7_render": "A7 RemotionRenderer",
    "a8_mix": "A8 AudioMixer",
    "a9_finalize": "A9 Finalize",
}

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
DEFAULT_SCHEMA_VERSION = "1.0.0"
DEFAULT_COMPOSITION_ID = "VideoPipelineComposition"
