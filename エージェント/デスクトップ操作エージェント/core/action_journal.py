"""
Action Journal（実行台帳）+ 再現パック生成

目的: 失敗学習を「使える形」にする

内容:
- ActionID / screen_key / layer / selector(or coords) / pre_ss / post_ss / result / failure_tags
- 1つのフォルダに固めて、後でそのまま再現できるようにする

ChatGPT 5.2フィードバック（2026-02-05）より
"""

import json
import time
import shutil
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Any
from enum import Enum
import uuid


class ActionLayer(Enum):
    """実行レイヤー"""
    LAYER_2_PLUS = "layer2+"  # Playwright+CDP
    LAYER_3 = "layer3"        # Pywinauto (UIA)
    LAYER_1 = "layer1"        # PyAutoGUI
    LAYER_0 = "layer0"        # VLM


class ActionResult(Enum):
    """実行結果"""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"       # Circuit Breaker等でスキップ
    CANCELLED = "cancelled"   # ユーザーキャンセル


class ActionPhase(Enum):
    """SSのフェーズ"""
    PRE = "pre"    # 実行前
    POST = "post"  # 実行後
    FAIL = "fail"  # 失敗時


@dataclass
class ActionEntry:
    """アクション記録エントリ"""
    
    action_id: str                    # UUID
    timestamp: str                    # ISO形式
    screen_key: str                   # 画面識別キー
    layer: ActionLayer                # 実行レイヤー
    action_type: str                  # Click/TypeText/Wait等
    target: dict                      # セレクタ、座標等
    result: ActionResult              # 結果
    duration_ms: int                  # 実行時間
    
    # オプション
    pre_ss_path: Optional[str] = None
    post_ss_path: Optional[str] = None
    fail_ss_path: Optional[str] = None
    
    # 失敗情報
    failure_tags: list[str] = field(default_factory=list)
    root_cause: Optional[str] = None   # 推定原因
    symptom: Optional[str] = None      # 観測症状
    confidence: float = 1.0            # 確度 0.0-1.0
    
    # 回復情報
    recovery_action: Optional[str] = None
    fallback_layer: Optional[ActionLayer] = None
    
    # メタデータ
    run_id: Optional[str] = None      # 実行セッションID
    parent_action_id: Optional[str] = None  # 親アクション（リトライ元）
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """辞書に変換"""
        d = asdict(self)
        d["layer"] = self.layer.value
        d["result"] = self.result.value
        if self.fallback_layer:
            d["fallback_layer"] = self.fallback_layer.value
        return d


class ActionJournal:
    """アクション実行台帳"""
    
    def __init__(self, journal_dir: Path):
        self.journal_dir = journal_dir
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        
        self.entries: list[ActionEntry] = []
        self.run_id = str(uuid.uuid4())[:8]
        self._start_time = datetime.now()
    
    def record(
        self,
        screen_key: str,
        layer: ActionLayer,
        action_type: str,
        target: dict,
        result: ActionResult,
        duration_ms: int,
        **kwargs
    ) -> ActionEntry:
        """アクションを記録"""
        
        entry = ActionEntry(
            action_id=str(uuid.uuid4())[:12],
            timestamp=datetime.now().isoformat(),
            screen_key=screen_key,
            layer=layer,
            action_type=action_type,
            target=target,
            result=result,
            duration_ms=duration_ms,
            run_id=self.run_id,
            **kwargs
        )
        
        self.entries.append(entry)
        self._append_to_file(entry)
        
        return entry
    
    def record_ss(
        self,
        action_id: str,
        phase: ActionPhase,
        ss_path: Path
    ) -> None:
        """SSをアクションに紐付け"""
        
        for entry in reversed(self.entries):
            if entry.action_id == action_id:
                if phase == ActionPhase.PRE:
                    entry.pre_ss_path = str(ss_path)
                elif phase == ActionPhase.POST:
                    entry.post_ss_path = str(ss_path)
                elif phase == ActionPhase.FAIL:
                    entry.fail_ss_path = str(ss_path)
                break
    
    def record_failure(
        self,
        action_id: str,
        failure_tags: list[str],
        root_cause: Optional[str] = None,
        symptom: Optional[str] = None,
        confidence: float = 0.5
    ) -> None:
        """失敗情報を追加"""
        
        for entry in reversed(self.entries):
            if entry.action_id == action_id:
                entry.failure_tags = failure_tags
                entry.root_cause = root_cause
                entry.symptom = symptom
                entry.confidence = confidence
                break
    
    def _append_to_file(self, entry: ActionEntry) -> None:
        """ファイルに追記"""
        
        journal_file = self.journal_dir / f"journal_{self.run_id}.jsonl"
        with open(journal_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
    
    def get_failures(self) -> list[ActionEntry]:
        """失敗エントリのみ取得"""
        return [e for e in self.entries if e.result == ActionResult.FAILURE]
    
    def get_by_screen_key(self, screen_key: str) -> list[ActionEntry]:
        """screen_keyでフィルタ"""
        return [e for e in self.entries if e.screen_key == screen_key]
    
    def get_summary(self) -> dict:
        """サマリ取得"""
        
        total = len(self.entries)
        success = len([e for e in self.entries if e.result == ActionResult.SUCCESS])
        failure = len([e for e in self.entries if e.result == ActionResult.FAILURE])
        
        layers = {}
        for e in self.entries:
            layers[e.layer.value] = layers.get(e.layer.value, 0) + 1
        
        return {
            "run_id": self.run_id,
            "total_actions": total,
            "success": success,
            "failure": failure,
            "success_rate": success / total if total > 0 else 0,
            "layers": layers,
            "duration_total_ms": sum(e.duration_ms for e in self.entries),
        }


class ReproductionPack:
    """再現パック生成"""
    
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
    
    def create(
        self,
        action_id: str,
        journal: ActionJournal,
        include_context: bool = True
    ) -> Path:
        """指定アクションの再現パックを作成"""
        
        # 対象エントリを取得
        target_entry = None
        for entry in journal.entries:
            if entry.action_id == action_id:
                target_entry = entry
                break
        
        if not target_entry:
            raise ValueError(f"ActionID not found: {action_id}")
        
        # パックディレクトリ作成
        pack_dir = self.output_dir / f"repro_{action_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pack_dir.mkdir(parents=True, exist_ok=True)
        
        # アクション情報を保存
        with open(pack_dir / "action.json", "w", encoding="utf-8") as f:
            json.dump(target_entry.to_dict(), f, ensure_ascii=False, indent=2)
        
        # SSをコピー
        ss_dir = pack_dir / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        
        for attr in ["pre_ss_path", "post_ss_path", "fail_ss_path"]:
            ss_path = getattr(target_entry, attr)
            if ss_path and Path(ss_path).exists():
                dst = ss_dir / f"{attr.split('_')[0]}_{Path(ss_path).name}"
                shutil.copy(ss_path, dst)
        
        # コンテキスト情報
        if include_context:
            # 前後のアクションも含める
            idx = journal.entries.index(target_entry)
            context_entries = journal.entries[max(0, idx-3):idx+2]
            
            with open(pack_dir / "context.json", "w", encoding="utf-8") as f:
                json.dump(
                    [e.to_dict() for e in context_entries],
                    f, ensure_ascii=False, indent=2
                )
        
        # READMEを生成
        readme = self._generate_readme(target_entry)
        with open(pack_dir / "README.md", "w", encoding="utf-8") as f:
            f.write(readme)
        
        return pack_dir
    
    def create_failure_pack(self, journal: ActionJournal) -> Path:
        """失敗エントリ全体の再現パックを作成"""
        
        failures = journal.get_failures()
        if not failures:
            raise ValueError("No failures to pack")
        
        pack_dir = self.output_dir / f"failures_{journal.run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        pack_dir.mkdir(parents=True, exist_ok=True)
        
        # 全失敗を保存
        with open(pack_dir / "failures.json", "w", encoding="utf-8") as f:
            json.dump(
                [e.to_dict() for e in failures],
                f, ensure_ascii=False, indent=2
            )
        
        # サマリ
        summary = journal.get_summary()
        with open(pack_dir / "summary.json", "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        
        # SS収集
        ss_dir = pack_dir / "screenshots"
        ss_dir.mkdir(exist_ok=True)
        
        for entry in failures:
            for attr in ["pre_ss_path", "post_ss_path", "fail_ss_path"]:
                ss_path = getattr(entry, attr)
                if ss_path and Path(ss_path).exists():
                    dst = ss_dir / f"{entry.action_id}_{attr.split('_')[0]}_{Path(ss_path).name}"
                    shutil.copy(ss_path, dst)
        
        return pack_dir
    
    def _generate_readme(self, entry: ActionEntry) -> str:
        """README.mdを生成"""
        
        return f"""# 再現パック: {entry.action_id}

## 概要

- **日時**: {entry.timestamp}
- **画面**: {entry.screen_key}
- **レイヤー**: {entry.layer.value}
- **アクション**: {entry.action_type}
- **結果**: {entry.result.value}
- **所要時間**: {entry.duration_ms}ms

## ターゲット

```json
{json.dumps(entry.target, ensure_ascii=False, indent=2)}
```

## 失敗情報

- **タグ**: {', '.join(entry.failure_tags) or 'なし'}
- **推定原因**: {entry.root_cause or 'なし'}
- **症状**: {entry.symptom or 'なし'}
- **確度**: {entry.confidence}

## 再現手順

1. 同じscreen_key状態を再現する
2. `action.json`の内容に従ってアクションを実行
3. `screenshots/`フォルダで状態を確認

## ファイル一覧

- `action.json` - アクション詳細
- `context.json` - 前後のアクション
- `screenshots/` - SS画像
"""


# テスト
if __name__ == "__main__":
    import tempfile
    
    # テスト用ディレクトリ
    test_dir = Path(tempfile.mkdtemp())
    journal_dir = test_dir / "journal"
    repro_dir = test_dir / "repro"
    
    # Journalテスト
    print("=" * 60)
    print("Action Journal テスト")
    print("=" * 60)
    
    journal = ActionJournal(journal_dir)
    
    # アクション記録
    entry1 = journal.record(
        screen_key="chrome.exe|chatgpt.com/c/abc123",
        layer=ActionLayer.LAYER_2_PLUS,
        action_type="TypeText",
        target={"selector": "#prompt-textarea", "text": "テスト質問"},
        result=ActionResult.SUCCESS,
        duration_ms=150
    )
    print(f"1. SUCCESS: {entry1.action_id}")
    
    entry2 = journal.record(
        screen_key="chrome.exe|chatgpt.com/c/abc123",
        layer=ActionLayer.LAYER_2_PLUS,
        action_type="Click",
        target={"selector": "[data-testid='send-button']"},
        result=ActionResult.FAILURE,
        duration_ms=5000
    )
    print(f"2. FAILURE: {entry2.action_id}")
    
    # 失敗情報追加
    journal.record_failure(
        action_id=entry2.action_id,
        failure_tags=["TIMEOUT", "ELEMENT_NOT_FOUND"],
        root_cause="送信ボタンのセレクタが変更された可能性",
        symptom="5秒待機後もボタンが見つからない",
        confidence=0.7
    )
    
    entry3 = journal.record(
        screen_key="chrome.exe|chatgpt.com/c/abc123",
        layer=ActionLayer.LAYER_3,  # フォールバック
        action_type="Click",
        target={"uia_name": "送信", "coords": (500, 600)},
        result=ActionResult.SUCCESS,
        duration_ms=200,
        parent_action_id=entry2.action_id,
        recovery_action="FALLBACK_LAYER3"
    )
    print(f"3. SUCCESS (fallback): {entry3.action_id}")
    
    # サマリ
    print(f"\nサマリ: {json.dumps(journal.get_summary(), ensure_ascii=False, indent=2)}")
    
    # 再現パックテスト
    print("\n" + "=" * 60)
    print("Reproduction Pack テスト")
    print("=" * 60)
    
    repro = ReproductionPack(repro_dir)
    
    # 失敗パック作成
    pack_path = repro.create_failure_pack(journal)
    print(f"失敗パック作成: {pack_path}")
    
    # ファイル一覧
    for p in pack_path.rglob("*"):
        if p.is_file():
            print(f"  - {p.relative_to(pack_path)}")
    
    print("\n" + "=" * 60)
    print("テスト完了")
