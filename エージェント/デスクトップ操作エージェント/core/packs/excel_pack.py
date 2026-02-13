# -*- coding: utf-8 -*-
"""
Excel Pack - Microsoft Excel用プリセットパック

Excel（デスクトップ版）の主要操作に対応するlocator候補と回復手順を提供。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .contracts import AppPack, LocatorCandidate, RecoveryPlan, RecoveryStep


class ExcelPack(AppPack):
    """Microsoft Excel プリセットパック"""
    
    @property
    def app_id(self) -> str:
        return "excel"
    
    @property
    def display_name(self) -> str:
        return "Microsoft Excel"
    
    def detect(self, context: Dict[str, Any]) -> bool:
        """Excelウィンドウを検出"""
        title = str(context.get("window_title", "")).lower()
        proc = str(context.get("process_name", "")).lower()
        return "excel" in title or "excel" in proc
    
    def get_locators(self, screen_key: str, intent: str) -> List[LocatorCandidate]:
        """
        Excel操作のlocator候補を返す。
        
        主要操作:
        - open_file: ファイルを開く
        - save: 保存
        - save_as: 名前を付けて保存
        - close: 閉じる
        - new_sheet: 新規シート
        - cell_click: セルクリック
        - cell_input: セル入力
        """
        locators: Dict[str, List[LocatorCandidate]] = {
            "open_file": [
                LocatorCandidate(
                    id="excel_file_menu",
                    intent="click",
                    priority=100,
                    automation_id="FileTabButton",
                    name="ファイル",
                    control_type="Button",
                ),
                LocatorCandidate(
                    id="excel_open_button",
                    intent="click",
                    priority=90,
                    automation_id="OpenButton",
                    name="開く",
                    control_type="Button",
                ),
            ],
            "save": [
                LocatorCandidate(
                    id="excel_quick_save",
                    intent="click",
                    priority=100,
                    automation_id="QuickSaveButton",
                    name="上書き保存",
                    control_type="Button",
                ),
            ],
            "save_as": [
                LocatorCandidate(
                    id="excel_save_as",
                    intent="click",
                    priority=100,
                    automation_id="SaveAsButton",
                    name="名前を付けて保存",
                    control_type="Button",
                ),
            ],
            "close": [
                LocatorCandidate(
                    id="excel_close",
                    intent="click",
                    priority=100,
                    automation_id="CloseButton",
                    control_type="Button",
                ),
            ],
            "new_sheet": [
                LocatorCandidate(
                    id="excel_new_sheet",
                    intent="click",
                    priority=100,
                    automation_id="SheetTab.New",
                    name="新しいシート",
                    control_type="Button",
                ),
            ],
            "cell_click": [
                LocatorCandidate(
                    id="excel_name_box",
                    intent="type",
                    priority=100,
                    automation_id="NATNameBox",
                    control_type="Edit",
                ),
            ],
            "cell_input": [
                LocatorCandidate(
                    id="excel_formula_bar",
                    intent="type",
                    priority=100,
                    automation_id="Fx",
                    name="数式バー",
                    control_type="Edit",
                ),
            ],
        }
        
        return locators.get(intent, [])
    
    def get_recovery(self, fail_type: str) -> Optional[RecoveryPlan]:
        """Excel用回復プラン"""
        plans: Dict[str, RecoveryPlan] = {
            "MODAL_DIALOG": RecoveryPlan(
                fail_type="MODAL_DIALOG",
                steps=[
                    RecoveryStep("press_escape", {}),
                    RecoveryStep("wait_ms", {"ms": 300}),
                    RecoveryStep("click_if_exists", {
                        "automation_id": "CloseButton",
                        "fallback": "press_escape"
                    }),
                ],
            ),
            "WRONG_STATE": RecoveryPlan(
                fail_type="WRONG_STATE",
                steps=[
                    RecoveryStep("press_escape", {}),
                    RecoveryStep("wait_ms", {"ms": 200}),
                    RecoveryStep("focus_window", {"app": "excel"}),
                ],
            ),
            "FOCUS_LOST": RecoveryPlan(
                fail_type="FOCUS_LOST",
                steps=[
                    RecoveryStep("focus_window", {"app": "excel"}),
                    RecoveryStep("wait_ms", {"ms": 300}),
                ],
            ),
        }
        return plans.get(fail_type)
