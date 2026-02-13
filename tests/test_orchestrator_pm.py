"""Orchestrator PM テスト（TDD: Red Phase）

orchestrator_pm.py の主要機能を検証するテスト群。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# スクリプトパス追加
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "エージェント" / "オーケストレーター" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import orchestrator_pm  # noqa: E402


# ---------------------------------------------------------------------------
# テストデータ
# ---------------------------------------------------------------------------
SAMPLE_GOAL = "ChatGPTデスクトップの安定化と自動テスト整備"
SAMPLE_CONSTRAINTS = ["既存エージェント構造を壊さない", "2日以内に完了"]
SAMPLE_AGENTS = [
    {"name": "ANTIGRAVITY:/research", "desc": "外部情報・根拠の調査"},
    {"name": "ANTIGRAVITY:/code", "desc": "仕様確定済みの実装"},
    {"name": "ChatGPTDesktop:consult_to_saturation", "desc": "曖昧な要件の整理"},
    {"name": "ANTIGRAVITY:/check", "desc": "品質・安全ゲート"},
]


def _make_sample_plan() -> dict:
    """サンプル入力から計画を生成するヘルパー"""
    return orchestrator_pm.generate_plan(
        goal=SAMPLE_GOAL,
        constraints=SAMPLE_CONSTRAINTS,
        available_agents=SAMPLE_AGENTS,
    )


# ---------------------------------------------------------------------------
# 1. MACHINE_JSON パース可能
# ---------------------------------------------------------------------------
def test_machine_json_parseable() -> None:
    """MACHINE_JSON が json.loads() でパース可能であること"""
    plan = _make_sample_plan()
    json_str = orchestrator_pm.generate_machine_json(plan)

    # BEGIN_JSON / END_JSON で囲まれていること
    assert "BEGIN_JSON" in json_str
    assert "END_JSON" in json_str

    # 区切り内のJSONがパース可能
    start = json_str.index("BEGIN_JSON") + len("BEGIN_JSON")
    end = json_str.index("END_JSON")
    parsed = json.loads(json_str[start:end])
    assert isinstance(parsed, dict)
    assert "goal" in parsed
    assert "tasks" in parsed


# ---------------------------------------------------------------------------
# 2. 全タスクに deliverable と gate が存在
# ---------------------------------------------------------------------------
def test_tasks_have_deliverable_and_gate() -> None:
    """各タスクに deliverable と gate が必須で設定されていること"""
    plan = _make_sample_plan()
    for task in plan["tasks"]:
        assert "deliverable" in task and task["deliverable"], (
            f"タスク {task['id']} に deliverable がない"
        )
        assert "gate" in task and task["gate"], (
            f"タスク {task['id']} に gate がない"
        )


# ---------------------------------------------------------------------------
# 3. QUESTIONS は最大3つ
# ---------------------------------------------------------------------------
def test_questions_max_three() -> None:
    """questions_to_user が最大3個であること"""
    plan = _make_sample_plan()
    assert len(plan["questions_to_user"]) <= 3


# ---------------------------------------------------------------------------
# 4. EXPAND でのみ詳細が出る
# ---------------------------------------------------------------------------
def test_expand_only_on_request() -> None:
    """EXPAND前はサブステップなし、EXPAND後にサブステップが出力されること"""
    plan = _make_sample_plan()
    first_task_id = plan["tasks"][0]["id"]

    # EXPAND前: サブステップなし
    assert "substeps" not in plan["tasks"][0] or plan["tasks"][0].get("substeps") is None

    # EXPAND後: サブステップあり
    expanded = orchestrator_pm.expand_task(plan, first_task_id)
    expanded_task = next(t for t in expanded["tasks"] if t["id"] == first_task_id)
    assert "substeps" in expanded_task
    assert len(expanded_task["substeps"]) > 0


# ---------------------------------------------------------------------------
# 5. ASSIGN オーバーライド
# ---------------------------------------------------------------------------
def test_override_assign() -> None:
    """ASSIGN task=T1 agent=X 適用後に assigned_agent が変更されること"""
    plan = _make_sample_plan()
    first_id = plan["tasks"][0]["id"]
    new_agent = "ANTIGRAVITY:/research"

    updated = orchestrator_pm.apply_overrides(plan, [
        {"command": "ASSIGN", "task": first_id, "agent": new_agent}
    ])
    task = next(t for t in updated["tasks"] if t["id"] == first_id)
    assert task["assigned_agent"] == new_agent


# ---------------------------------------------------------------------------
# 6. INSERT_TASK オーバーライド
# ---------------------------------------------------------------------------
def test_override_insert_task() -> None:
    """INSERT_TASK で新タスクが正しい位置に挿入されること"""
    plan = _make_sample_plan()
    first_id = plan["tasks"][0]["id"]
    original_count = len(plan["tasks"])

    updated = orchestrator_pm.apply_overrides(plan, [
        {
            "command": "INSERT_TASK",
            "after": first_id,
            "id": "T0b",
            "title": "追加タスク",
            "agent": "ANTIGRAVITY:/code",
            "deliverable": "追加成果物",
            "gate": "テスト通過",
        }
    ])
    assert len(updated["tasks"]) == original_count + 1
    # 挿入位置の確認
    ids = [t["id"] for t in updated["tasks"]]
    assert ids.index("T0b") == ids.index(first_id) + 1


# ---------------------------------------------------------------------------
# 7. SET_GATE オーバーライド
# ---------------------------------------------------------------------------
def test_override_set_gate() -> None:
    """SET_GATE でゲートが更新されること"""
    plan = _make_sample_plan()
    first_id = plan["tasks"][0]["id"]
    new_gate = "全テストパス + コードレビュー完了"

    updated = orchestrator_pm.apply_overrides(plan, [
        {"command": "SET_GATE", "task": first_id, "gate": new_gate}
    ])
    task = next(t for t in updated["tasks"] if t["id"] == first_id)
    assert task["gate"] == new_gate


# ---------------------------------------------------------------------------
# 8. LOCK_ORDER オーバーライド
# ---------------------------------------------------------------------------
def test_override_lock_order() -> None:
    """LOCK_ORDER で依存順が固定されること"""
    plan = _make_sample_plan()
    if len(plan["tasks"]) < 2:
        return  # タスクが2つ未満ならスキップ

    ids = [t["id"] for t in plan["tasks"][:3]]
    updated = orchestrator_pm.apply_overrides(plan, [
        {"command": "LOCK_ORDER", "tasks": ids}
    ])

    # 2番目以降が前のタスクに依存していること
    for i in range(1, len(ids)):
        task = next(t for t in updated["tasks"] if t["id"] == ids[i])
        assert ids[i - 1] in task["dependencies"], (
            f"{ids[i]} が {ids[i-1]} に依存していない"
        )


# ---------------------------------------------------------------------------
# 9. risk=high なタスクの gate に安全ゲート案が含まれる
# ---------------------------------------------------------------------------
def test_risk_high_requires_safety_gate() -> None:
    """risk=high のタスクには gate にテスト/検証系のキーワードが含まれること"""
    plan = _make_sample_plan()
    # 強制的にhighリスクタスクを追加してテスト
    plan_with_high = orchestrator_pm.apply_overrides(plan, [
        {
            "command": "INSERT_TASK",
            "after": plan["tasks"][0]["id"],
            "id": "T_HIGH",
            "title": "高リスクタスク",
            "agent": "ANTIGRAVITY:/code",
            "deliverable": "本番DB変更",
            "gate": "",  # 空のgateで追加 → validate_planで安全ゲート付与
            "risk": "high",
        }
    ])
    validated = orchestrator_pm.validate_plan(plan_with_high)
    high_task = next(t for t in validated["tasks"] if t["id"] == "T_HIGH")
    assert high_task["gate"], "高リスクタスクの gate が空"


# ---------------------------------------------------------------------------
# 10. ルーティングテスト: 外部情報が必要なタスクが /research に割当
# ---------------------------------------------------------------------------
def test_routing_research() -> None:
    """route_task が外部情報タスクに /research を割り当てること"""
    task = {
        "id": "T_TEST",
        "title": "最新のWebフレームワーク比較",
        "objective": "2026年時点の最新情報を調査し根拠をまとめる",
        "needs_external_facts": True,
        "needs_clarification": False,
        "spec_ready": False,
        "safety_critical": False,
    }
    routed = orchestrator_pm.route_task(task, SAMPLE_AGENTS)
    assert "research" in routed["assigned_agent"].lower()


# ---------------------------------------------------------------------------
# 11. ADD_AGENT オーバーライド
# ---------------------------------------------------------------------------
def test_override_add_agent() -> None:
    """ADD_AGENT でエージェント一覧に追加されること"""
    plan = _make_sample_plan()
    original_count = len(plan.get("available_agents", []))

    updated = orchestrator_pm.apply_overrides(plan, [
        {"command": "ADD_AGENT", "name": "CustomBot:v1", "desc": "カスタムボット"}
    ])
    assert len(updated["available_agents"]) == original_count + 1
    names = [a["name"] for a in updated["available_agents"]]
    assert "CustomBot:v1" in names


# ---------------------------------------------------------------------------
# 12. save_outputs がファイルを出力
# ---------------------------------------------------------------------------
def test_save_outputs(tmp_path: Path) -> None:
    """save_outputs が json + md ファイルを出力すること"""
    plan = _make_sample_plan()
    orchestrator_pm.save_outputs(plan, tmp_path)

    # 必須ファイルの存在確認
    assert (tmp_path / "plan.json").exists()
    assert (tmp_path / "roadmap.md").exists()
    assert (tmp_path / "dispatch_queue.json").exists()

    # JSONがパース可能
    data = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert "goal" in data
    assert "tasks" in data
    raw = (tmp_path / "plan.json").read_text(encoding="utf-8")
    assert "BEGIN_JSON" not in raw
    assert "END_JSON" not in raw


# ---------------------------------------------------------------------------
# 13. オーバーライド文字列の解析
# ---------------------------------------------------------------------------
def test_parse_override_command_insert_task() -> None:
    """INSERT_TASK の文字列が正しく辞書化されること"""
    line = 'INSERT_TASK after=T2 id=T2b title="追加タスク" agent=ANTIGRAVITY:/code deliverable="成果物" gate="テスト全パス" risk=med'
    parsed = orchestrator_pm.parse_override_command(line)
    assert parsed["command"] == "INSERT_TASK"
    assert parsed["after"] == "T2"
    assert parsed["id"] == "T2b"
    assert parsed["title"] == "追加タスク"
    assert parsed["agent"] == "ANTIGRAVITY:/code"
    assert parsed["deliverable"] == "成果物"
    assert parsed["gate"] == "テスト全パス"
    assert parsed["risk"] == "med"


def test_parse_override_command_lock_order() -> None:
    """LOCK_ORDER の tasks が配列として解析されること"""
    parsed = orchestrator_pm.parse_override_command("LOCK_ORDER tasks=T1,T2,T3")
    assert parsed["command"] == "LOCK_ORDER"
    assert parsed["tasks"] == ["T1", "T2", "T3"]


def test_parse_override_command_add_agent_capabilities() -> None:
    """ADD_AGENT で capabilities と command_template を受け取れること"""
    line = 'ADD_AGENT name=FlexAgent workflow_path=/flex desc="柔軟実行" capabilities=research,check command_template="/flex --task {task_id}"'
    parsed = orchestrator_pm.parse_override_command(line)
    assert parsed["command"] == "ADD_AGENT"
    assert parsed["workflow_path"] == "/flex"
    assert parsed["capabilities"] == ["research", "check"]
    assert parsed["command_template"] == "/flex --task {task_id}"


# ---------------------------------------------------------------------------
# 14. オーバーライド後に roadmap/questions が再生成される
# ---------------------------------------------------------------------------
def test_apply_overrides_recomputes_roadmap_and_questions() -> None:
    """INSERT_TASK 後に roadmap と questions_to_user が更新されること"""
    plan = _make_sample_plan()
    updated = orchestrator_pm.apply_overrides(plan, [
        {
            "command": "INSERT_TASK",
            "after": plan["tasks"][0]["id"],
            "id": "T_HIGH",
            "title": "高リスク追加タスク",
            "agent": "ANTIGRAVITY:/code",
            "deliverable": "運用手順書",
            "gate": "レビュー完了",
            "risk": "high",
        }
    ])

    assert any("高リスク追加タスク" in phase["summary"] for phase in updated["roadmap"])
    assert any("T_HIGH" in question for question in updated["questions_to_user"])


# ---------------------------------------------------------------------------
# 15. 動的分解: 調査中心ゴールでは実装タスクを省略可能
# ---------------------------------------------------------------------------
def test_dynamic_decompose_research_only_goal() -> None:
    """調査中心のGoalでは実装タスクが生成されないこと"""
    plan = orchestrator_pm.generate_plan(
        goal="2026年の市場調査と比較",
        constraints=["根拠重視"],
        available_agents=SAMPLE_AGENTS,
    )
    titles = [task["title"] for task in plan["tasks"]]
    assert not any(title.startswith("実装:") for title in titles)
    assert any(title.startswith("要件調査:") for title in titles)


# ---------------------------------------------------------------------------
# 16. 依存解決: 入力順に依存しないトポロジカル順になる
# ---------------------------------------------------------------------------
def test_dependency_levels_order_independent() -> None:
    """依存先が後ろにある順序でもレベルが正しく計算されること"""
    tasks = [
        {"id": "T2", "dependencies": ["T1"]},
        {"id": "T1", "dependencies": []},
        {"id": "T3", "dependencies": ["T2"]},
    ]
    levels, ordered, cycles = orchestrator_pm._compute_dependency_levels(tasks)
    assert cycles == []
    assert ordered.index("T1") < ordered.index("T2") < ordered.index("T3")
    assert levels["T1"] < levels["T2"] < levels["T3"]


# ---------------------------------------------------------------------------
# 17. ディスパッチ接続: command_hint が生成される
# ---------------------------------------------------------------------------
def test_dispatch_queue_has_command_hints() -> None:
    """dispatch_queue の各要素に command_hint が含まれること"""
    plan = _make_sample_plan()
    queue = plan.get("dispatch_queue", [])
    assert queue, "dispatch_queue が空"
    assert all(item.get("command_hint") for item in queue)


def test_research_command_hint_prefers_template() -> None:
    task = {
        "id": "T_R",
        "assigned_agent": "/research",
        "title": "市場調査",
        "objective": "一次情報の収集",
        "gate": "根拠付き",
    }
    agent_info = {
        "workflow_path": "/research",
        "command_template": "python .agent/workflows/research/scripts/research.py --goal \"{title}\" --focus \"{objective}\" --task-id \"{task_id}\"",
    }
    hint = orchestrator_pm._build_command_hint(task, agent_info=agent_info)
    assert hint.startswith("python .agent/workflows/research/scripts/research.py")


# ---------------------------------------------------------------------------
# 18. 能力ベース割当: capabilityタグが優先される
# ---------------------------------------------------------------------------
def test_capability_based_routing_prefers_custom_agent() -> None:
    """required_capabilities が複合条件の場合に最適エージェントが選ばれること"""
    agents = SAMPLE_AGENTS + [
        {
            "name": "FlexResearch",
            "workflow_path": "/flex-research",
            "desc": "なんでも調査",
            "capabilities": ["research", "check"],
        }
    ]
    task = {
        "id": "T_CAP",
        "title": "調査タスク",
        "objective": "外部調査",
        "required_capabilities": ["research", "check"],
        "needs_external_facts": False,
        "needs_clarification": False,
        "spec_ready": False,
        "needs_validation": False,
        "safety_critical": False,
    }
    routed = orchestrator_pm.route_task(task, agents)
    assert routed["assigned_agent"] == "/flex-research"


# ---------------------------------------------------------------------------
# 19. ディスパッチ: command_template が反映される
# ---------------------------------------------------------------------------
def test_dispatch_uses_agent_command_template() -> None:
    """assigned_agent の command_template が command_hint に使われること"""
    plan = _make_sample_plan()
    updated = orchestrator_pm.apply_overrides(plan, [
        {
            "command": "ADD_AGENT",
            "name": "FlexPlanner",
            "workflow_path": "/flex-planner",
            "capabilities": ["consult"],
            "command_template": '/flex-planner --task "{task_id}" --goal "{title}"',
        },
        {
            "command": "ASSIGN",
            "task": plan["tasks"][0]["id"],
            "agent": "/flex-planner",
        },
    ])
    queue = updated.get("dispatch_queue", [])
    first = next(item for item in queue if item["task_id"] == plan["tasks"][0]["id"])
    assert first["command_hint"].startswith('/flex-planner --task "')


# ---------------------------------------------------------------------------
# 20. discover_agents: frontmatterのcapabilitiesを検出
# ---------------------------------------------------------------------------
def test_discover_agents_reads_capabilities(tmp_path: Path) -> None:
    """SKILL frontmatter の capabilities/command_template を取り込めること"""
    wf_dir = tmp_path / ".agent" / "workflows" / "flex_agent"
    wf_dir.mkdir(parents=True)
    skill_text = """---
name: Flex Agent
description: 汎用実行エージェント
capabilities: research,consult
command_template: /flex_agent --task "{task_id}"
---
# Flex
"""
    (wf_dir / "SKILL.md").write_text(skill_text, encoding="utf-8")
    agents = orchestrator_pm.discover_agents(tmp_path)
    target = next(agent for agent in agents if agent.get("workflow_path") == "/flex_agent")
    assert "research" in target["capabilities"]
    assert "consult" in target["capabilities"]
    assert target["command_template"] == '/flex_agent --task "{task_id}"'


def test_override_assign_unknown_task_raises() -> None:
    """未知タスクへのASSIGNは例外で明示的に失敗すること"""
    plan = _make_sample_plan()
    with pytest.raises(ValueError):
        orchestrator_pm.apply_overrides(plan, [
            {"command": "ASSIGN", "task": "T999", "agent": "ANTIGRAVITY:/code"}
        ])


def test_override_insert_duplicate_task_id_raises() -> None:
    """INSERT_TASK で既存IDを再利用すると例外になること"""
    plan = _make_sample_plan()
    duplicate_id = plan["tasks"][0]["id"]
    with pytest.raises(ValueError):
        orchestrator_pm.apply_overrides(plan, [
            {
                "command": "INSERT_TASK",
                "after": plan["tasks"][0]["id"],
                "id": duplicate_id,
                "title": "重複IDタスク",
            }
        ])


def test_override_lock_order_unknown_task_raises() -> None:
    """LOCK_ORDER が未知タスクを含む場合は例外になること"""
    plan = _make_sample_plan()
    ids = [plan["tasks"][0]["id"], "T999"]
    with pytest.raises(ValueError):
        orchestrator_pm.apply_overrides(plan, [
            {"command": "LOCK_ORDER", "tasks": ids}
        ])


def test_validate_plan_strict_rejects_cycle() -> None:
    """strict検証では循環依存をエラーとして拒否すること"""
    cyclic_plan = {
        "goal": "cyclic",
        "constraints": [],
        "tasks": [
            {"id": "T1", "title": "A", "dependencies": ["T2"], "deliverable": "d", "gate": "g", "risk": "low"},
            {"id": "T2", "title": "B", "dependencies": ["T1"], "deliverable": "d", "gate": "g", "risk": "low"},
        ],
        "roadmap": [],
        "questions_to_user": [],
        "available_agents": SAMPLE_AGENTS,
    }
    with pytest.raises(ValueError):
        orchestrator_pm.validate_plan(cyclic_plan, strict=True)


def test_discover_agents_fallback_to_workflow_when_skill_broken(tmp_path: Path) -> None:
    """SKILLが読めない場合にWORKFLOW frontmatterへフォールバックできること"""
    wf_dir = tmp_path / ".agent" / "workflows" / "broken_skill_agent"
    wf_dir.mkdir(parents=True)
    # UTF-8で読めないバイト列を配置
    (wf_dir / "SKILL.md").write_bytes(b"\xff\xfe\x00\x00broken")
    (wf_dir / "WORKFLOW.md").write_text(
        "---\nname: Fallback Agent\ndescription: workflow fallback\n---\n# body\n",
        encoding="utf-8",
    )
    agents = orchestrator_pm.discover_agents(tmp_path)
    target = next(agent for agent in agents if agent.get("workflow_path") == "/broken_skill_agent")
    assert target["name"] == "Fallback Agent"
    assert target["desc"] == "workflow fallback"
