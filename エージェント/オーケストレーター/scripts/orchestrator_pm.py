#!/usr/bin/env python3
"""Orchestrator PM v1.1.0 - 計画生成特化オーケストレータ

ユーザーの Goal + Constraints から:
  1) ROADMAP（人間向け）
  2) TASK LIST（成果物単位）
  3) QUESTIONS（最大3個）
  4) MACHINE_JSON（パース可能）
を出力する。実行はしない（後で/checkに接続）。
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import re
import shlex
import sys
from collections import Counter, deque
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------
VERSION = "1.1.0"

# デフォルトの利用可能エージェント
DEFAULT_AGENTS: list[dict[str, Any]] = [
    {
        "name": "ANTIGRAVITY:/research",
        "desc": "外部情報・根拠の調査",
        "workflow_path": "/research",
        "capabilities": ["research"],
    },
    {
        "name": "ANTIGRAVITY:/code",
        "desc": "仕様確定済みの実装",
        "workflow_path": "/code",
        "capabilities": ["code"],
    },
    {
        "name": "ChatGPTDesktop:consult_to_saturation",
        "desc": "曖昧な要件の整理・飽和相談",
        "workflow_path": "/desktop-chatgpt",
        "capabilities": ["consult"],
    },
    {
        "name": "ANTIGRAVITY:/check",
        "desc": "品質・安全ゲート検証",
        "workflow_path": "/check",
        "capabilities": ["check"],
    },
]

SUPPORTED_OVERRIDES = [
    "ADD_AGENT",
    "ASSIGN",
    "INSERT_TASK",
    "SET_GATE",
    "EXPAND",
    "LOCK_ORDER",
]

VALID_RISK_LEVELS = {"low", "med", "high"}

# 安全ゲートのキーワード（risk=highのタスクに自動付与）
SAFETY_GATE_TEMPLATE = "ANTIGRAVITY:/check による品質検証 + テスト全パス + ロールバック手順確認"

# ワークスペースルート（デフォルト: スクリプトから3階層上）
_DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]

CAPABILITY_ALIASES: dict[str, tuple[str, ...]] = {
    "research": ("research", "調査", "fact", "evidence", "analysis"),
    "consult": ("consult", "chatgpt", "相談", "requirements", "decision"),
    "code": ("code", "implementation", "実装", "develop", "build"),
    "check": ("check", "verify", "検証", "test", "qa", "gate"),
}

CAPABILITY_WORKFLOW_HINTS: dict[str, tuple[str, ...]] = {
    "research": ("/research",),
    "consult": ("/desktop-chatgpt", "/consult"),
    "code": ("/code",),
    "check": ("/check",),
}

EXTERNAL_FACT_KEYWORDS = (
    "調査", "research", "最新", "比較", "根拠", "市場", "法令", "仕様確認", "ベンチマーク",
)
CLARIFICATION_KEYWORDS = (
    "相談", "設計", "要件", "方針", "決定", "判断", "ロードマップ", "構造", "整理",
)
IMPLEMENTATION_KEYWORDS = (
    "実装", "開発", "コード", "修正", "作成", "自動化", "機能追加", "統合", "build",
)
HIGH_RISK_KEYWORDS = (
    "本番", "削除", "移行", "deploy", "課金", "送信", "書き換え", "delete",
)
VALIDATION_KEYWORDS = (
    "検証", "check", "テスト", "品質", "validate",
)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    lower_text = text.lower()
    return any(keyword.lower() in lower_text for keyword in keywords)


def _goal_profile(goal: str, constraints: list[str]) -> dict[str, bool]:
    """Goal/Constraints から分解方針を推定する。"""
    combined = " ".join([goal, *constraints])
    has_external_facts = _contains_any(combined, EXTERNAL_FACT_KEYWORDS)
    has_clarification_need = _contains_any(combined, CLARIFICATION_KEYWORDS)
    has_impl_signal = _contains_any(combined, IMPLEMENTATION_KEYWORDS)
    high_risk = _contains_any(combined, HIGH_RISK_KEYWORDS)

    # 実装明示がなく、調査/相談中心語が多い場合は実装タスクを省略
    implementation_required = has_impl_signal or not (has_external_facts or has_clarification_need)
    clarification_required = has_clarification_need or implementation_required
    research_required = has_external_facts

    return {
        "research_required": research_required,
        "clarification_required": clarification_required,
        "implementation_required": implementation_required,
        "high_risk": high_risk,
    }


# ---------------------------------------------------------------------------
# ワークフロー自動検出
# ---------------------------------------------------------------------------
def _parse_frontmatter(text: str) -> dict[str, str]:
    """YAML frontmatter (---で囲まれた部分) を簡易パースする。"""
    match = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def _parse_capabilities(raw_value: str) -> list[str]:
    """frontmatterのcapabilities/tagsを正規化する。"""
    raw = raw_value.strip().strip("[]")
    if not raw:
        return []
    parts = [part.strip(" '\"") for part in raw.split(",")]
    return [part for part in parts if part]


def _infer_capabilities(agent: dict[str, Any]) -> list[str]:
    """name/desc/path から能力タグを推定する。"""
    text = " ".join([
        str(agent.get("name", "")),
        str(agent.get("workflow_path", "")),
    ]).lower()
    detected: list[str] = []
    for capability, aliases in CAPABILITY_ALIASES.items():
        if any(alias.lower() in text for alias in aliases):
            detected.append(capability)
    return detected


def _normalize_agent(agent: dict[str, Any]) -> dict[str, Any]:
    """エージェント辞書を標準形に整える。"""
    normalized = copy.deepcopy(agent)
    workflow_path = normalized.get("workflow_path", "")
    if not workflow_path and normalized.get("name", "").startswith("/"):
        workflow_path = normalized.get("name", "")
    normalized["workflow_path"] = workflow_path
    normalized["name"] = normalized.get("name", workflow_path or "unknown")
    normalized["desc"] = normalized.get("desc", "")

    capabilities = normalized.get("capabilities", [])
    if isinstance(capabilities, str):
        capabilities = _parse_capabilities(capabilities)
    if not capabilities:
        capabilities = _infer_capabilities(normalized)
    normalized["capabilities"] = sorted(set(capabilities))

    command_template = normalized.get("command_template")
    if isinstance(command_template, str):
        normalized["command_template"] = command_template.strip()
    else:
        normalized["command_template"] = ""

    return normalized


def discover_agents(workspace_root: Path | None = None) -> list[dict[str, Any]]:
    """.agent/workflows/ をスキャンし、各 SKILL.md の frontmatter を読み取る。

    Returns:
        各ワークフローの {name, desc, workflow_path} リスト。
        SKILL.md が存在しないワークフローは WORKFLOW.md から description を取得する。
    """
    root = workspace_root or _DEFAULT_WORKSPACE_ROOT
    workflows_dir = root / ".agent" / "workflows"
    if not workflows_dir.is_dir():
        return DEFAULT_AGENTS

    agents: list[dict[str, Any]] = []
    # 自分自身（orchestrator_pm）と shared は除外
    skip_dirs = {"shared", "orchestrator_pm"}

    for child in sorted(workflows_dir.iterdir()):
        if not child.is_dir() or child.name in skip_dirs:
            continue

        # SKILL.md を優先、なければ WORKFLOW.md
        skill_path = child / "SKILL.md"
        workflow_path = child / "WORKFLOW.md"

        fm: dict[str, str] = {}
        for source_path in [skill_path, workflow_path]:
            if not source_path.is_file():
                continue
            text = ""
            try:
                text = source_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    text = source_path.read_text(encoding="utf-8-sig")
                except (OSError, UnicodeDecodeError):
                    continue
            except OSError:
                continue
            parsed = _parse_frontmatter(text)
            if parsed:
                fm = parsed
                break

        name = fm.get("name", f"/{child.name}")
        desc = fm.get("description", "")
        capabilities = _parse_capabilities(fm.get("capabilities", fm.get("tags", "")))
        command_template = fm.get("command_template", fm.get("invoke", ""))

        agents.append(_normalize_agent({
            "name": name,
            "desc": desc,
            "workflow_path": f"/{child.name}",
            "capabilities": capabilities,
            "command_template": command_template,
        }))

    # 最低限のフォールバック
    if not agents:
        return [_normalize_agent(agent) for agent in DEFAULT_AGENTS]

    return [_normalize_agent(agent) for agent in agents]


def parse_override_command(line: str) -> dict[str, Any]:
    """1行のオーバーライド文字列を辞書に変換する。"""
    tokens = shlex.split(line.strip(), posix=True)
    if not tokens:
        raise ValueError("空行はオーバーライドとして解釈できません")

    command = tokens[0].upper()
    if command not in SUPPORTED_OVERRIDES:
        raise ValueError(f"未対応コマンドです: {command}")

    params: dict[str, str] = {}
    for token in tokens[1:]:
        if "=" not in token:
            raise ValueError(f"key=value 形式ではありません: {token}")
        key, value = token.split("=", 1)
        params[key] = value

    override: dict[str, Any] = {"command": command}

    if command == "ADD_AGENT":
        if "name" not in params:
            raise ValueError("ADD_AGENT には name が必要です")
        override["name"] = params["name"]
        if "desc" in params:
            override["desc"] = params["desc"]
        if "workflow_path" in params:
            override["workflow_path"] = params["workflow_path"]
        if "capabilities" in params:
            override["capabilities"] = _parse_capabilities(params["capabilities"])
        if "command_template" in params:
            override["command_template"] = params["command_template"]

    elif command == "ASSIGN":
        for field in ("task", "agent"):
            if field not in params:
                raise ValueError(f"ASSIGN には {field} が必要です")
        override["task"] = params["task"]
        override["agent"] = params["agent"]

    elif command == "INSERT_TASK":
        for field in ("after", "id", "title"):
            if field not in params:
                raise ValueError(f"INSERT_TASK には {field} が必要です")
        override["after"] = params["after"]
        override["id"] = params["id"]
        override["title"] = params["title"]
        if "objective" in params:
            override["objective"] = params["objective"]
        if "agent" in params:
            override["agent"] = params["agent"]
        if "deliverable" in params:
            override["deliverable"] = params["deliverable"]
        if "gate" in params:
            override["gate"] = params["gate"]
        if "risk" in params:
            risk = params["risk"].lower()
            if risk not in VALID_RISK_LEVELS:
                raise ValueError(f"risk は low/med/high のいずれかです: {params['risk']}")
            override["risk"] = risk
        if "required_capabilities" in params:
            override["required_capabilities"] = _parse_capabilities(params["required_capabilities"])

    elif command == "SET_GATE":
        for field in ("task", "gate"):
            if field not in params:
                raise ValueError(f"SET_GATE には {field} が必要です")
        override["task"] = params["task"]
        override["gate"] = params["gate"]

    elif command == "EXPAND":
        if "task" not in params:
            raise ValueError("EXPAND には task が必要です")
        override["task"] = params["task"]

    elif command == "LOCK_ORDER":
        tasks = params.get("tasks", "")
        task_ids = [task_id.strip() for task_id in tasks.split(",") if task_id.strip()]
        if not task_ids:
            raise ValueError("LOCK_ORDER には tasks=T1,T2 のような指定が必要です")
        override["tasks"] = task_ids

    return override


def collect_overrides_from_stdin() -> list[dict[str, Any]]:
    """stdin対話でオーバーライドを受け付ける。"""
    if sys.stdin is None or not sys.stdin.isatty():
        return []

    print("[Orchestrator PM] オーバーライドを入力できます。空行または done で終了します。")
    overrides: list[dict[str, Any]] = []
    while True:
        try:
            line = input("override> ").strip()
        except EOFError:
            break
        if not line or line.lower() in {"done", "end", "exit", "quit"}:
            break
        try:
            override = parse_override_command(line)
        except ValueError as exc:
            print(f"[Orchestrator PM] 無効な入力: {exc}")
            continue
        overrides.append(override)
        print(f"[Orchestrator PM] 受付: {override['command']}")
    return overrides


# ---------------------------------------------------------------------------
# ルーティングルール
# ---------------------------------------------------------------------------
def _pick_agent(
    available_agents: list[dict[str, Any]],
    keywords: tuple[str, ...],
    fallback: str,
) -> str:
    """name/desc/workflow_path を横断してエージェントを選択する。"""
    # 1) workflow_path と name を優先（誤マッチを減らす）
    for agent in available_agents:
        haystack = " ".join([
            agent.get("name", ""),
            agent.get("workflow_path", ""),
        ]).lower()
        if any(keyword.lower() in haystack for keyword in keywords):
            return agent.get("workflow_path") or agent.get("name") or fallback

    # 2) fallbackとして description も参照
    for agent in available_agents:
        desc = agent.get("desc", "").lower()
        if any(keyword.lower() in desc for keyword in keywords):
            return agent.get("workflow_path") or agent.get("name") or fallback
    return fallback


def _pick_agent_by_capabilities(
    available_agents: list[dict[str, Any]],
    required_capabilities: list[str],
    fallback: str,
) -> str:
    """能力タグに基づいて最適エージェントを選ぶ。"""
    if not required_capabilities:
        return fallback

    best_score = -1
    best_specificity = 10**6
    best_agent: dict[str, Any] | None = None
    for raw_agent in available_agents:
        agent = _normalize_agent(raw_agent)
        capabilities = set(agent.get("capabilities", []))
        workflow_path = agent.get("workflow_path", "").lower()
        score = 0
        for required in required_capabilities:
            if required in capabilities:
                score += 3
            else:
                aliases = CAPABILITY_ALIASES.get(required, ())
                text = " ".join([
                    agent.get("name", ""),
                    agent.get("desc", ""),
                    agent.get("workflow_path", ""),
                ]).lower()
                if any(alias.lower() in text for alias in aliases):
                    score += 1
            hints = CAPABILITY_WORKFLOW_HINTS.get(required, ())
            if any(hint in workflow_path for hint in hints):
                score += 2
        specificity = len(agent.get("capabilities", []))
        if score > best_score or (score == best_score and specificity < best_specificity):
            best_score = score
            best_specificity = specificity
            best_agent = agent

    if best_agent and best_score > 0:
        return best_agent.get("workflow_path") or best_agent.get("name") or fallback
    return fallback


def route_task(task: dict[str, Any], available_agents: list[dict[str, Any]]) -> dict[str, Any]:
    """タスク属性に基づいてエージェントを割り当てる。

    判断基準:
    - needs_external_facts=True → /research
    - needs_clarification=True → ChatGPTDesktop:consult
    - spec_ready=True → /code
    - safety_critical=True → ANTIGRAVITY:/check をgateに追加
    """
    task = copy.deepcopy(task)
    normalized_agents = [_normalize_agent(agent) for agent in available_agents]
    explicit_caps = task.get("required_capabilities", [])
    if explicit_caps:
        task["assigned_agent"] = _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=list(explicit_caps),
            fallback=task.get("assigned_agent", "") or "ANTIGRAVITY:/code",
        )
    elif task.get("needs_validation"):
        task["assigned_agent"] = _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=["check"],
            fallback=_pick_agent(
                normalized_agents,
                keywords=("/check", "quality", "check"),
                fallback="ANTIGRAVITY:/check",
            ),
        )

    elif task.get("needs_external_facts"):
        task["assigned_agent"] = _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=["research"],
            fallback=_pick_agent(
                normalized_agents,
                keywords=("research", "/research"),
                fallback="ANTIGRAVITY:/research",
            ),
        )

    elif task.get("needs_clarification"):
        task["assigned_agent"] = _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=["consult"],
            fallback=_pick_agent(
                normalized_agents,
                keywords=("desktop-chatgpt", "chatgpt", "consult"),
                fallback="ChatGPTDesktop:consult_to_saturation",
            ),
        )

    elif task.get("spec_ready"):
        task["assigned_agent"] = _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=["code"],
            fallback=_pick_agent(
                normalized_agents,
                keywords=("/code", "implementation", "code"),
                fallback="ANTIGRAVITY:/code",
            ),
        )

    else:
        # デフォルト: /code（空文字は未割当とみなす）
        task["assigned_agent"] = task.get("assigned_agent") or _pick_agent_by_capabilities(
            normalized_agents,
            required_capabilities=["code"],
            fallback=_pick_agent(
                normalized_agents,
                keywords=("/code", "implementation", "code"),
                fallback="ANTIGRAVITY:/code",
            ),
        )

    # safety_critical ならゲートに ANTIGRAVITY:/check を含める
    if task.get("safety_critical"):
        gate = task.get("gate", "")
        if "ANTIGRAVITY:/check" not in gate:
            task["gate"] = f"{gate} + ANTIGRAVITY:/check" if gate else "ANTIGRAVITY:/check"

    return task


# ---------------------------------------------------------------------------
# タスク分解
# ---------------------------------------------------------------------------
def decompose_tasks(
    goal: str,
    constraints: list[str],
) -> list[dict[str, Any]]:
    """Goal と Constraints からタスク一覧を生成する。

    v1.1: Goal/Constraints から必要フェーズを推定する。
    """
    profile = _goal_profile(goal, constraints)
    tasks: list[dict[str, Any]] = []

    def _next_task_id() -> str:
        return f"T{len(tasks) + 1}"

    def _prev_dependencies() -> list[str]:
        if not tasks:
            return []
        return [tasks[-1]["id"]]

    if profile["research_required"]:
        tasks.append({
            "id": _next_task_id(),
            "title": f"要件調査: {goal}",
            "objective": "外部情報・制約・根拠を収集し、判断材料を作る",
            "assigned_agent": "",
            "deliverable": "調査メモ（根拠リンク付き）",
            "gate": "根拠付きの調査結果が揃っている",
            "dependencies": _prev_dependencies(),
            "risk": "low",
            "needs_external_facts": True,
            "needs_clarification": False,
            "spec_ready": False,
            "needs_validation": False,
            "safety_critical": False,
        })

    if profile["clarification_required"]:
        tasks.append({
            "id": _next_task_id(),
            "title": f"設計相談: {goal}",
            "objective": "論点と未知点を飽和点まで詰め、実装可能な仕様に落とす",
            "assigned_agent": "",
            "deliverable": "実装ブリーフ（目的/制約/非目標/受入基準）",
            "gate": "unknownsが閾値以下で意思決定が確定",
            "dependencies": _prev_dependencies(),
            "risk": "low",
            "needs_external_facts": False,
            "needs_clarification": True,
            "spec_ready": False,
            "needs_validation": False,
            "safety_critical": False,
        })

    if profile["implementation_required"]:
        tasks.append({
            "id": _next_task_id(),
            "title": f"実装: {goal}",
            "objective": "確定した仕様に基づいて差分実装を行う",
            "assigned_agent": "",
            "deliverable": "実装コード + 差分テスト",
            "gate": "テスト全パス + lint エラー0",
            "dependencies": _prev_dependencies(),
            "risk": "high" if profile["high_risk"] else "med",
            "needs_external_facts": False,
            "needs_clarification": False,
            "spec_ready": True,
            "needs_validation": False,
            "safety_critical": profile["high_risk"],
        })

    tasks.append({
        "id": _next_task_id(),
        "title": f"品質検証: {goal}",
        "objective": "成果物の品質・安全性・受入基準を検証する",
        "assigned_agent": "",
        "deliverable": "検証レポート（PASS/FAIL理由付き）",
        "gate": "ANTIGRAVITY:/check 全ゲートPASS",
        "dependencies": _prev_dependencies(),
        "risk": "low",
        "needs_external_facts": False,
        "needs_clarification": False,
        "spec_ready": False,
        "needs_validation": True,
        "safety_critical": True,
    })

    return tasks


# ---------------------------------------------------------------------------
# 計画生成（メイン関数）
# ---------------------------------------------------------------------------
def generate_plan(
    goal: str,
    constraints: list[str] | None = None,
    available_agents: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Goal から完全な計画を生成する。

    available_agents が None の場合、workspace_root の .agent/workflows/ を
    スキャンして動的に検出する。
    """
    constraints = constraints or []
    if available_agents is None:
        available_agents = discover_agents(workspace_root)
    else:
        available_agents = [_normalize_agent(agent) for agent in available_agents]
    overrides = overrides or []

    # タスク分解
    tasks = decompose_tasks(goal, constraints)

    # ルーティング（各タスクにエージェント割当）
    routed_tasks = [route_task(t, available_agents) for t in tasks]
    has_implementation_task = any(task.get("spec_ready") for task in routed_tasks)

    # ルーティング属性を除去（出力にはメタ属性を含めない）
    for t in routed_tasks:
        for key in ["needs_external_facts", "needs_clarification", "spec_ready", "needs_validation", "safety_critical"]:
            t.pop(key, None)

    # 成功基準の推定
    success_criteria = [
        f"{goal} が完了すること",
        "全テストがパスすること" if has_implementation_task else "調査・設計成果物がレビュー済みであること",
        "関連ドキュメントが同期されていること",
    ]

    plan: dict[str, Any] = {
        "goal": goal,
        "success_criteria": success_criteria,
        "constraints": constraints,
        "available_agents": available_agents,
        "roadmap": [],
        "tasks": routed_tasks,
        "questions_to_user": [],
        "supported_overrides": SUPPORTED_OVERRIDES,
    }
    plan = refresh_plan_views(plan)

    # オーバーライド適用
    if overrides:
        plan = apply_overrides(plan, overrides)

    return plan


# ---------------------------------------------------------------------------
# ロードマップ生成
# ---------------------------------------------------------------------------
def _find_duplicate_task_ids(tasks: list[dict[str, Any]]) -> list[str]:
    """重複している task_id 一覧を返す。"""
    counts = Counter(task.get("id", "") for task in tasks if task.get("id"))
    return sorted([task_id for task_id, count in counts.items() if count > 1])


def _compute_dependency_levels(tasks: list[dict[str, Any]]) -> tuple[dict[str, int], list[str], list[str]]:
    """依存関係をトポロジカルに解決し、レベルと順序を返す。

    Returns:
        levels: task_id -> level
        ordered_ids: 依存順タスクID
        cycle_ids: 循環依存で未解決だったID
    """
    if not tasks:
        return {}, [], []

    ids_in_order = [task["id"] for task in tasks]
    duplicate_ids = _find_duplicate_task_ids(tasks)
    if duplicate_ids:
        raise ValueError(f"重複したタスクIDを検知しました: {', '.join(duplicate_ids)}")
    valid_ids = set(ids_in_order)

    deps_map: dict[str, list[str]] = {}
    children_map: dict[str, list[str]] = {task_id: [] for task_id in ids_in_order}
    indegree: dict[str, int] = {task_id: 0 for task_id in ids_in_order}

    for task in tasks:
        task_id = task["id"]
        deps = []
        for dep in task.get("dependencies", []):
            if dep in valid_ids and dep != task_id and dep not in deps:
                deps.append(dep)
        deps_map[task_id] = deps
        indegree[task_id] = len(deps)
        for dep in deps:
            children_map[dep].append(task_id)

    queue = deque([task_id for task_id in ids_in_order if indegree[task_id] == 0])
    levels: dict[str, int] = {task_id: 0 for task_id in ids_in_order}
    ordered_ids: list[str] = []

    while queue:
        current = queue.popleft()
        ordered_ids.append(current)
        for child in children_map[current]:
            levels[child] = max(levels.get(child, 0), levels[current] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    cycle_ids = [task_id for task_id in ids_in_order if task_id not in ordered_ids]
    if cycle_ids:
        base_level = max(levels.values(), default=0) + 1
        for index, task_id in enumerate(cycle_ids):
            levels[task_id] = base_level + index
            ordered_ids.append(task_id)

    return levels, ordered_ids, cycle_ids


def _build_roadmap(tasks: list[dict[str, Any]]) -> list[dict[str, str | list[str]]]:
    """タスク一覧からフェーズ別ロードマップを生成する。"""
    # タスクをフェーズにグルーピング
    phases: list[dict[str, str | list[str]]] = []

    # 依存関係に基づいてフェーズを推定（循環依存は末尾レベルに退避）
    dep_levels, _, cycle_ids = _compute_dependency_levels(tasks)

    # フェーズ名テンプレート
    phase_names = ["調査・分析", "設計・相談", "実装", "検証・完了"]

    # フェーズごとにタスクをグループ化
    max_level = max(dep_levels.values()) if dep_levels else 0
    for level in range(max_level + 1):
        level_tasks = [t for t in tasks if dep_levels.get(t["id"]) == level]
        if not level_tasks:
            continue
        phase_name = phase_names[level] if level < len(phase_names) else f"フェーズ{level + 1}"
        agents = list({t["assigned_agent"] for t in level_tasks if t.get("assigned_agent")})
        summary_parts = [t["title"] for t in level_tasks]
        phases.append({
            "phase": phase_name,
            "summary": " / ".join(summary_parts),
            "agents": agents,
        })

    if cycle_ids:
        phases.append({
            "phase": "依存エラー",
            "summary": f"循環依存を検知: {', '.join(cycle_ids)}",
            "agents": ["Orchestrator PM"],
        })

    return phases


# ---------------------------------------------------------------------------
# 質問生成
# ---------------------------------------------------------------------------
def _generate_questions(
    goal: str,
    constraints: list[str],
    tasks: list[dict[str, Any]],
) -> list[str]:
    """計画確定に必要な不足情報に基づく質問を生成する（最大3つ）。"""
    questions: list[str] = []

    # 制約が少ない場合
    if len(constraints) < 2:
        questions.append("スケジュール・予算などの追加制約はありますか？")

    # 高リスクタスクがある場合
    high_risk = [t for t in tasks if t.get("risk") == "high"]
    if high_risk:
        questions.append(f"高リスクタスク（{', '.join(t['id'] for t in high_risk)}）のロールバック手順は既にありますか？")

    # 未割当タスクがある場合
    unassigned = [t["id"] for t in tasks if not t.get("assigned_agent")]
    if unassigned:
        questions.append(f"未割当タスク（{', '.join(unassigned)}）の担当エージェントを指定しますか？")

    # デフォルト質問
    if len(questions) < 1:
        questions.append("この計画の優先度変更や特別な要件はありますか？")

    return questions[:3]


def _agent_lookup(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """name/workflow_path をキーにエージェント情報を引けるようにする。"""
    lookup: dict[str, dict[str, Any]] = {}
    for raw_agent in plan.get("available_agents", []):
        agent = _normalize_agent(raw_agent)
        if agent.get("name"):
            lookup[agent["name"]] = agent
        if agent.get("workflow_path"):
            lookup[agent["workflow_path"]] = agent
    return lookup


def _render_command_template(template: str, task: dict[str, Any]) -> str:
    """command template のプレースホルダを展開する。"""
    params = {
        "task_id": task.get("id", ""),
        "title": task.get("title", ""),
        "objective": task.get("objective", ""),
        "gate": task.get("gate", ""),
    }
    rendered = template
    for key, value in params.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered


def _quote_command_value(value: Any) -> str:
    """コマンド引数として安全に埋め込むためJSON形式でクォートする。"""
    text = str(value).replace("\r", " ").replace("\n", " ")
    return json.dumps(text, ensure_ascii=False)


def _build_command_hint(task: dict[str, Any], agent_info: dict[str, Any] | None = None) -> str:
    """担当エージェントに応じて実行ヒントを返す。"""
    task_id = task.get("id", "")
    agent = task.get("assigned_agent", "")
    title = task.get("title", "")
    objective = task.get("objective", "")
    gate = task.get("gate", "")

    if agent_info:
        template = agent_info.get("command_template", "")
        if template:
            return _render_command_template(template, task)

    workflow_path = ""
    if agent_info:
        workflow_path = agent_info.get("workflow_path", "")
    if not workflow_path and isinstance(agent, str) and agent.startswith("/"):
        workflow_path = agent

    lower_agent = agent.lower()
    if "research" in lower_agent:
        return f"/research Goal={_quote_command_value(title)} Focus={_quote_command_value(objective)}"
    if "consult" in lower_agent or "chatgptdesktop" in lower_agent:
        return f"ChatGPTDesktop consult Goal={_quote_command_value(title)} Output={_quote_command_value('decision_record')}"
    if "/code" in lower_agent or "implementation" in lower_agent:
        return f"/code Task={_quote_command_value(task_id)} Objective={_quote_command_value(objective)} Gate={_quote_command_value(gate)}"
    if "check" in lower_agent:
        return f"/check --task-id {_quote_command_value(task_id)}"
    if workflow_path:
        return f"{workflow_path} --goal {_quote_command_value(title)} --task-id {_quote_command_value(task_id)}"
    return f"# manual_dispatch task={_quote_command_value(task_id)} agent={_quote_command_value(agent)}"


def build_dispatch_queue(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """計画を実行順キューに変換する（接続レイヤ）。"""
    tasks = plan.get("tasks", [])
    levels, ordered_ids, cycle_ids = _compute_dependency_levels(tasks)
    if cycle_ids:
        raise ValueError(f"循環依存を含むため dispatch_queue を生成できません: {', '.join(cycle_ids)}")
    task_map = {task["id"]: task for task in tasks}
    agent_map = _agent_lookup(plan)

    queue: list[dict[str, Any]] = []
    for task_id in ordered_ids:
        task = task_map[task_id]
        assigned_agent = task.get("assigned_agent", "")
        agent_info = agent_map.get(assigned_agent)
        queue.append({
            "task_id": task_id,
            "order_level": levels.get(task_id, 0),
            "assigned_agent": assigned_agent,
            "depends_on": task.get("dependencies", []),
            "deliverable": task.get("deliverable", ""),
            "gate": task.get("gate", ""),
            "command_hint": _build_command_hint(task, agent_info=agent_info),
            "status": "planned",
        })
    return queue


def refresh_plan_views(plan: dict[str, Any]) -> dict[str, Any]:
    """tasksに依存する派生出力（roadmap/questions）を再生成する。"""
    plan = copy.deepcopy(plan)
    tasks = plan.get("tasks", [])
    constraints = plan.get("constraints", [])
    goal = plan.get("goal", "")
    plan["roadmap"] = _build_roadmap(tasks)
    plan["questions_to_user"] = _generate_questions(goal, constraints, tasks)[:3]
    plan["dispatch_queue"] = build_dispatch_queue(plan)
    return plan


# ---------------------------------------------------------------------------
# オーバーライド適用
# ---------------------------------------------------------------------------
def apply_overrides(
    plan: dict[str, Any],
    overrides: list[dict[str, Any]],
) -> dict[str, Any]:
    """オーバーライドコマンドを計画に適用する。"""
    plan = copy.deepcopy(plan)

    for ov in overrides:
        cmd = ov.get("command", "")

        if cmd == "ADD_AGENT":
            existing_keys = {
                agent.get("name", "") for agent in plan.get("available_agents", [])
            } | {
                agent.get("workflow_path", "") for agent in plan.get("available_agents", [])
            }
            requested_name = ov["name"]
            requested_path = ov.get("workflow_path", "")
            if requested_name in existing_keys or (requested_path and requested_path in existing_keys):
                raise ValueError(f"既存エージェントと重複しています: {requested_name or requested_path}")
            added_agent = _normalize_agent({
                "name": ov["name"],
                "desc": ov.get("desc", ""),
                "workflow_path": ov.get("workflow_path", ""),
                "capabilities": ov.get("capabilities", []),
                "command_template": ov.get("command_template", ""),
            })
            plan.setdefault("available_agents", []).append(added_agent)

        elif cmd == "ASSIGN":
            task_id = ov["task"]
            agent = ov["agent"]
            matched = False
            for t in plan["tasks"]:
                if t["id"] == task_id:
                    t["assigned_agent"] = agent
                    matched = True
                    break
            if not matched:
                raise ValueError(f"ASSIGN 対象タスクが存在しません: {task_id}")

        elif cmd == "INSERT_TASK":
            after_id = ov["after"]
            existing_ids = [task["id"] for task in plan.get("tasks", [])]
            if after_id not in existing_ids:
                raise ValueError(f"INSERT_TASK の after が存在しません: {after_id}")
            if ov["id"] in existing_ids:
                raise ValueError(f"INSERT_TASK の id が重複しています: {ov['id']}")
            risk_level = ov.get("risk", "low").lower()
            if risk_level not in VALID_RISK_LEVELS:
                raise ValueError(f"risk は low/med/high のいずれかです: {risk_level}")
            new_task: dict[str, Any] = {
                "id": ov["id"],
                "title": ov["title"],
                "objective": ov.get("objective", ov["title"]),
                "assigned_agent": ov.get("agent", ""),
                "deliverable": ov.get("deliverable", ""),
                "gate": ov.get("gate", ""),
                "dependencies": [after_id],
                "risk": risk_level,
                "required_capabilities": ov.get("required_capabilities", []),
            }
            # 担当未指定の場合は最小ヒューリスティックで割当
            if not new_task["assigned_agent"]:
                title_and_obj = f"{new_task['title']} {new_task['objective']}"
                new_task["needs_external_facts"] = _contains_any(title_and_obj, EXTERNAL_FACT_KEYWORDS)
                new_task["needs_clarification"] = _contains_any(title_and_obj, CLARIFICATION_KEYWORDS)
                new_task["spec_ready"] = _contains_any(title_and_obj, IMPLEMENTATION_KEYWORDS)
                new_task["needs_validation"] = _contains_any(title_and_obj, VALIDATION_KEYWORDS)
                new_task["safety_critical"] = new_task.get("risk") == "high"
                new_task = route_task(new_task, plan.get("available_agents", DEFAULT_AGENTS))
                for key in ["needs_external_facts", "needs_clarification", "spec_ready", "needs_validation", "safety_critical"]:
                    new_task.pop(key, None)
            # 挿入位置を特定
            idx = next(
                (i for i, t in enumerate(plan["tasks"]) if t["id"] == after_id),
                len(plan["tasks"]) - 1,
            )
            plan["tasks"].insert(idx + 1, new_task)

        elif cmd == "SET_GATE":
            task_id = ov["task"]
            new_gate = ov["gate"]
            matched = False
            for t in plan["tasks"]:
                if t["id"] == task_id:
                    t["gate"] = new_gate
                    matched = True
                    break
            if not matched:
                raise ValueError(f"SET_GATE 対象タスクが存在しません: {task_id}")

        elif cmd == "EXPAND":
            task_id = ov["task"]
            plan = expand_task(plan, task_id)

        elif cmd == "LOCK_ORDER":
            task_ids = ov["tasks"]
            existing_ids = {task["id"] for task in plan.get("tasks", [])}
            missing_ids = [task_id for task_id in task_ids if task_id not in existing_ids]
            if missing_ids:
                raise ValueError(f"LOCK_ORDER に未知タスクがあります: {', '.join(missing_ids)}")
            for i in range(1, len(task_ids)):
                for t in plan["tasks"]:
                    if t["id"] == task_ids[i]:
                        if task_ids[i - 1] not in t.get("dependencies", []):
                            t.setdefault("dependencies", []).append(task_ids[i - 1])
                        break

    return refresh_plan_views(plan)


# ---------------------------------------------------------------------------
# EXPAND（サブステップ展開）
# ---------------------------------------------------------------------------
def expand_task(plan: dict[str, Any], task_id: str) -> dict[str, Any]:
    """指定タスクのサブステップを展開する。"""
    plan = copy.deepcopy(plan)
    found = False
    for task in plan["tasks"]:
        if task["id"] == task_id:
            # 汎用サブステップテンプレート
            task["substeps"] = [
                {"step": 1, "action": "前提条件の確認", "tool": "read_only"},
                {"step": 2, "action": "対象ファイルの特定", "tool": "grep_search / list_dir"},
                {"step": 3, "action": "実行", "tool": task.get("assigned_agent", "")},
                {"step": 4, "action": "結果の検証", "tool": "test / verify"},
            ]
            found = True
            break
    if not found:
        raise ValueError(f"EXPAND 対象タスクが存在しません: {task_id}")
    return plan


# ---------------------------------------------------------------------------
# バリデーション
# ---------------------------------------------------------------------------
def validate_plan(plan: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """計画の整合性を検証し、自動補正する。"""
    plan = copy.deepcopy(plan)
    tasks = plan.get("tasks", [])
    task_ids = {task["id"] for task in tasks}
    errors: list[str] = []

    duplicate_ids = _find_duplicate_task_ids(tasks)
    if duplicate_ids:
        errors.append(f"重複タスクID: {', '.join(duplicate_ids)}")

    for task in tasks:
        # 依存関係を正規化（未知ID・自己参照・重複を除外）
        dependencies: list[str] = []
        unknown_deps: list[str] = []
        for dep in task.get("dependencies", []):
            if dep in task_ids and dep != task["id"] and dep not in dependencies:
                dependencies.append(dep)
            elif dep not in task_ids:
                unknown_deps.append(dep)
        task["dependencies"] = dependencies
        if unknown_deps:
            errors.append(f"{task['id']} が未知依存を参照: {', '.join(unknown_deps)}")

        risk = str(task.get("risk", "low")).lower()
        task["risk"] = risk
        if risk not in VALID_RISK_LEVELS:
            errors.append(f"{task['id']} の risk が不正です: {risk}")

        # deliverable が空なら警告付きで補填
        if not task.get("deliverable"):
            task["deliverable"] = f"（要定義: {task['title']} の成果物）"

        # gate が空なら補填
        if not task.get("gate"):
            if task.get("risk") == "high":
                task["gate"] = SAFETY_GATE_TEMPLATE
            else:
                task["gate"] = "タスク完了確認"

        # risk=high で gate に安全関連キーワードがない場合は追加
        if task.get("risk") == "high":
            safety_keywords = ["ANTIGRAVITY:/check", "テスト", "検証", "レビュー", "ロールバック", "check"]
            if not any(kw in task["gate"] for kw in safety_keywords):
                task["gate"] = f"{task['gate']} + {SAFETY_GATE_TEMPLATE}"

    try:
        _, _, cycle_ids = _compute_dependency_levels(tasks)
    except ValueError as exc:
        errors.append(str(exc))
        cycle_ids = []
    if cycle_ids:
        errors.append(f"循環依存: {', '.join(cycle_ids)}")

    if errors:
        plan["validation_errors"] = errors
        if strict:
            raise ValueError("; ".join(errors))
        # 非strictでは無効計画の実行接続を停止
        plan["dispatch_queue"] = []
        plan["roadmap"] = []
        plan["questions_to_user"] = _generate_questions(
            plan.get("goal", ""),
            plan.get("constraints", []),
            tasks,
        )[:3]
        return plan

    plan.pop("validation_errors", None)

    # 正規化後に派生出力を更新
    plan = refresh_plan_views(plan)

    return plan


# ---------------------------------------------------------------------------
# MACHINE_JSON 生成
# ---------------------------------------------------------------------------
def _build_machine_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """MACHINE_JSON用の辞書を構築する。"""
    return {
        "goal": plan["goal"],
        "success_criteria": plan.get("success_criteria", []),
        "constraints": plan.get("constraints", []),
        "roadmap": plan.get("roadmap", []),
        "tasks": [
            {
                "id": t["id"],
                "title": t["title"],
                "objective": t.get("objective", t["title"]),
                "assigned_agent": t.get("assigned_agent", ""),
                "deliverable": t.get("deliverable", ""),
                "gate": t.get("gate", ""),
                "dependencies": t.get("dependencies", []),
                "risk": t.get("risk", "low"),
            }
            for t in plan["tasks"]
        ],
        "questions_to_user": plan.get("questions_to_user", [])[:3],
        "dispatch_queue": plan.get("dispatch_queue", []),
        "supported_overrides": SUPPORTED_OVERRIDES,
    }


def generate_machine_json(plan: dict[str, Any]) -> str:
    """MACHINE_JSON を BEGIN_JSON/END_JSON で囲んで出力する。"""
    json_str = json.dumps(_build_machine_payload(plan), ensure_ascii=False, indent=2)
    return f"BEGIN_JSON\n{json_str}\nEND_JSON"


# ---------------------------------------------------------------------------
# Markdown ロードマップ生成
# ---------------------------------------------------------------------------
def generate_roadmap_md(plan: dict[str, Any]) -> str:
    """人間向けのMarkdownロードマップを生成する。"""
    lines: list[str] = []
    lines.append(f"# ロードマップ: {plan['goal']}")
    lines.append("")

    # 制約
    if plan.get("constraints"):
        lines.append("## 制約")
        for c in plan["constraints"]:
            lines.append(f"- {c}")
        lines.append("")

    # フェーズ
    lines.append("## フェーズ")
    for phase in plan.get("roadmap", []):
        agents = ", ".join(phase.get("agents", []))
        lines.append(f"### {phase['phase']}")
        lines.append(f"- **概要**: {phase['summary']}")
        lines.append(f"- **担当**: {agents}")
        lines.append("")

    # タスク一覧
    lines.append("## タスク一覧")
    lines.append("")
    lines.append("| ID | タイトル | 担当 | 成果物 | ゲート | リスク |")
    lines.append("|:---|:--------|:-----|:-------|:-------|:-------|")
    for t in plan["tasks"]:
        lines.append(
            f"| {t['id']} | {t['title']} | {t.get('assigned_agent', '')} "
            f"| {t.get('deliverable', '')} | {t.get('gate', '')} | {t.get('risk', 'low')} |"
        )
    lines.append("")

    # ディスパッチ接続（実行キュー）
    dispatch_queue = plan.get("dispatch_queue", [])
    if dispatch_queue:
        lines.append("## 実行ディスパッチ（接続レイヤ）")
        lines.append("")
        lines.append("| Task | Agent | Depends On | Command Hint |")
        lines.append("|:-----|:------|:-----------|:-------------|")
        for item in dispatch_queue:
            deps = ",".join(item.get("depends_on", [])) or "-"
            lines.append(
                f"| {item['task_id']} | {item.get('assigned_agent', '')} | {deps} | {item.get('command_hint', '')} |"
            )
        lines.append("")

    # 質問
    questions = plan.get("questions_to_user", [])
    if questions:
        lines.append("## 確認事項")
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q}")
        lines.append("")

    # オーバーライド
    lines.append("## オーバーライドコマンド")
    lines.append("")
    lines.append("以下のコマンドで計画を微調整できます：")
    lines.append("")
    lines.append("```")
    lines.append('ADD_AGENT name=... desc=...')
    lines.append('ASSIGN task=T3 agent=...')
    lines.append('INSERT_TASK after=T2 id=T2b title="..." agent=... deliverable="..." gate="..."')
    lines.append('SET_GATE task=T4 gate="..."')
    lines.append('EXPAND task=T2')
    lines.append('LOCK_ORDER tasks=T1,T2,T3')
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 出力保存
# ---------------------------------------------------------------------------
def save_outputs(plan: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    """計画をファイルに保存する。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # MACHINE_JSON
    plan_json_path = output_dir / "plan.json"
    payload = _build_machine_payload(plan)
    plan_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Markdown ロードマップ
    roadmap_path = output_dir / "roadmap.md"
    roadmap_md = generate_roadmap_md(plan)
    roadmap_path.write_text(roadmap_md, encoding="utf-8")

    # ディスパッチキュー
    dispatch_path = output_dir / "dispatch_queue.json"
    dispatch_path.write_text(
        json.dumps(plan.get("dispatch_queue", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {"plan_json": plan_json_path, "roadmap_md": roadmap_path, "dispatch_queue": dispatch_path}


# ---------------------------------------------------------------------------
# CLI エントリポイント
# ---------------------------------------------------------------------------
def main() -> None:
    """CLIエントリポイント"""
    parser = argparse.ArgumentParser(
        description=f"Orchestrator PM v{VERSION} - 計画生成オーケストレータ",
    )
    parser.add_argument("--goal", required=True, help="達成目標")
    parser.add_argument("--constraints", nargs="*", default=[], help="制約条件")
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="ワークスペースルート（デフォルト: 自動検出）",
    )
    parser.add_argument(
        "--interactive-overrides",
        dest="interactive_overrides",
        action="store_true",
        default=True,
        help="stdin対話でオーバーライドを受け付ける（デフォルト: 有効）",
    )
    parser.add_argument(
        "--no-interactive-overrides",
        dest="interactive_overrides",
        action="store_false",
        help="stdin対話オーバーライドを無効化する",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="出力ディレクトリ（デフォルト: _outputs/orchestrator_pm/YYYYMMDD/）",
    )
    args = parser.parse_args()

    # ワークスペースルート
    ws_root = Path(args.workspace_root) if args.workspace_root else None

    # 検出されたエージェント一覧を表示
    agents = discover_agents(ws_root)
    print(f"[Orchestrator PM] 検出されたワークフロー: {len(agents)}件")
    for a in agents:
        print(f"  - {a['name']}: {a['desc'][:60]}")
    print()

    # 出力ディレクトリ
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        today = dt.datetime.now().strftime("%Y%m%d")
        output_dir = Path("_outputs") / "orchestrator_pm" / today

    # 計画生成
    plan = generate_plan(
        goal=args.goal,
        constraints=args.constraints,
        workspace_root=ws_root,
    )

    # 対話オーバーライド受付
    if args.interactive_overrides:
        overrides = collect_overrides_from_stdin()
        if overrides:
            try:
                plan = apply_overrides(plan, overrides)
            except ValueError as exc:
                print(f"[Orchestrator PM] オーバーライド適用エラー: {exc}", file=sys.stderr)
                raise SystemExit(2) from exc

    # バリデーション
    try:
        plan = validate_plan(plan, strict=True)
    except ValueError as exc:
        print(f"[Orchestrator PM] 計画バリデーションエラー: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    # 出力保存
    paths = save_outputs(plan, output_dir)
    print(f"[Orchestrator PM] 計画を保存しました:")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    # MACHINE_JSON を標準出力にも表示
    print()
    print(generate_machine_json(plan))

    # ロードマップも表示
    print()
    print(generate_roadmap_md(plan))


if __name__ == "__main__":
    _shared_dir = Path(__file__).resolve().parents[3] / ".agent" / "workflows" / "shared"
    if str(_shared_dir) not in sys.path:
        sys.path.insert(0, str(_shared_dir))
    try:
        from workflow_logging_hook import logged_main, phase_scope
    except Exception:
        main()
    else:
        # /orchestrator_pm は入口なので、主要処理を明示フェーズで分割して記録する
        def _logged_orchestrator_main() -> int:
            parser = argparse.ArgumentParser(
                description=f"Orchestrator PM v{VERSION} - 計画生成オーケストレータ",
            )
            parser.add_argument("--goal", required=True, help="達成目標")
            parser.add_argument("--constraints", nargs="*", default=[], help="制約条件")
            parser.add_argument(
                "--workspace-root",
                default=None,
                help="ワークスペースルート（デフォルト: 自動検出）",
            )
            parser.add_argument(
                "--interactive-overrides",
                dest="interactive_overrides",
                action="store_true",
                default=True,
                help="stdin対話でオーバーライドを受け付ける（デフォルト: 有効）",
            )
            parser.add_argument(
                "--no-interactive-overrides",
                dest="interactive_overrides",
                action="store_false",
                help="stdin対話オーバーライドを無効化する",
            )
            parser.add_argument(
                "--output-dir",
                default=None,
                help="出力ディレクトリ（デフォルト: _outputs/orchestrator_pm/YYYYMMDD/）",
            )
            args = parser.parse_args()

            ws_root = Path(args.workspace_root) if args.workspace_root else None
            today = dt.datetime.now().strftime("%Y%m%d")
            output_dir = Path(args.output_dir) if args.output_dir else Path("_outputs") / "orchestrator_pm" / today

            with logged_main("orchestrator_pm", "orchestrator_pm") as logger:
                with phase_scope(logger, "DISCOVER_AGENTS", inputs={"workspace_root": str(ws_root) if ws_root else ""}) as p:
                    agents = discover_agents(ws_root)
                    p.set_output("agent_count", len(agents))

                print(f"[Orchestrator PM] 検出されたワークフロー: {len(agents)}件")
                for a in agents:
                    print(f"  - {a['name']}: {a['desc'][:60]}")
                print()

                with phase_scope(logger, "GENERATE_PLAN", inputs={"goal": args.goal, "constraints": args.constraints}) as p:
                    plan = generate_plan(
                        goal=args.goal,
                        constraints=args.constraints,
                        workspace_root=ws_root,
                    )
                    p.set_output("task_count", len(plan.get("tasks", [])))

                if args.interactive_overrides:
                    with phase_scope(logger, "OVERRIDES") as p:
                        overrides = collect_overrides_from_stdin()
                        p.set_output("override_count", len(overrides))
                        if overrides:
                            try:
                                plan = apply_overrides(plan, overrides)
                            except ValueError as exc:
                                p.add_error(str(exc), error_type=type(exc).__name__)
                                print(f"[Orchestrator PM] オーバーライド適用エラー: {exc}", file=sys.stderr)
                                return 2

                with phase_scope(logger, "VALIDATE_PLAN") as p:
                    try:
                        plan = validate_plan(plan, strict=True)
                    except ValueError as exc:
                        p.add_error(str(exc), error_type=type(exc).__name__)
                        print(f"[Orchestrator PM] 計画バリデーションエラー: {exc}", file=sys.stderr)
                        return 2

                with phase_scope(logger, "SAVE_OUTPUTS", inputs={"output_dir": str(output_dir)}) as p:
                    paths = save_outputs(plan, output_dir)
                    p.set_output("artifacts", {k: str(v) for k, v in paths.items()})

                print(f"[Orchestrator PM] 計画を保存しました:")
                for name, path in paths.items():
                    print(f"  {name}: {path}")
                print()
                print(generate_machine_json(plan))
                print()
                print(generate_roadmap_md(plan))
            return 0

        raise SystemExit(_logged_orchestrator_main())
