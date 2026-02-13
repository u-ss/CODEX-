# -*- coding: utf-8 -*-
"""
Outlook Pack - Microsoft Outlook用プリセットパック

Outlook（デスクトップ版）の主要操作に対応するlocator候補と回復手順を提供。
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from .contracts import AppPack, LocatorCandidate, RecoveryPlan, RecoveryStep


class OutlookPack(AppPack):
    """Microsoft Outlook プリセットパック"""
    
    @property
    def app_id(self) -> str:
        return "outlook"
    
    @property
    def display_name(self) -> str:
        return "Microsoft Outlook"
    
    def detect(self, context: Dict[str, Any]) -> bool:
        """Outlookウィンドウを検出"""
        title = str(context.get("window_title", "")).lower()
        proc = str(context.get("process_name", "")).lower()
        return "outlook" in title or "outlook" in proc
    
    def get_locators(self, screen_key: str, intent: str) -> List[LocatorCandidate]:
        """
        Outlook操作のlocator候補を返す。
        
        主要操作:
        - new_mail: 新規メール
        - send: 送信
        - reply: 返信
        - reply_all: 全員に返信
        - forward: 転送
        - to_field: 宛先入力
        - subject_field: 件名入力
        - body_field: 本文入力
        - search: 検索
        """
        locators: Dict[str, List[LocatorCandidate]] = {
            "new_mail": [
                LocatorCandidate(
                    id="outlook_new_mail",
                    intent="click",
                    priority=100,
                    automation_id="NewMailButton",
                    name="新しいメール",
                    control_type="Button",
                ),
                LocatorCandidate(
                    id="outlook_new_mail_alt",
                    intent="click",
                    priority=80,
                    name="新規作成",
                    control_type="Button",
                ),
            ],
            "send": [
                LocatorCandidate(
                    id="outlook_send",
                    intent="click",
                    priority=100,
                    automation_id="SendButton",
                    name="送信",
                    control_type="Button",
                ),
            ],
            "reply": [
                LocatorCandidate(
                    id="outlook_reply",
                    intent="click",
                    priority=100,
                    automation_id="ReplyButton",
                    name="返信",
                    control_type="Button",
                ),
            ],
            "reply_all": [
                LocatorCandidate(
                    id="outlook_reply_all",
                    intent="click",
                    priority=100,
                    automation_id="ReplyAllButton",
                    name="全員に返信",
                    control_type="Button",
                ),
            ],
            "forward": [
                LocatorCandidate(
                    id="outlook_forward",
                    intent="click",
                    priority=100,
                    automation_id="ForwardButton",
                    name="転送",
                    control_type="Button",
                ),
            ],
            "to_field": [
                LocatorCandidate(
                    id="outlook_to",
                    intent="type",
                    priority=100,
                    automation_id="ToRecipientsField",
                    name="宛先",
                    control_type="Edit",
                ),
            ],
            "subject_field": [
                LocatorCandidate(
                    id="outlook_subject",
                    intent="type",
                    priority=100,
                    automation_id="SubjectField",
                    name="件名",
                    control_type="Edit",
                ),
            ],
            "body_field": [
                LocatorCandidate(
                    id="outlook_body",
                    intent="type",
                    priority=100,
                    automation_id="BodyEdit",
                    name="メッセージ",
                    control_type="Edit",
                ),
            ],
            "search": [
                LocatorCandidate(
                    id="outlook_search",
                    intent="type",
                    priority=100,
                    automation_id="SearchBox",
                    name="検索",
                    control_type="Edit",
                ),
            ],
        }
        
        return locators.get(intent, [])
    
    def get_recovery(self, fail_type: str) -> Optional[RecoveryPlan]:
        """Outlook用回復プラン"""
        plans: Dict[str, RecoveryPlan] = {
            "MODAL_DIALOG": RecoveryPlan(
                fail_type="MODAL_DIALOG",
                steps=[
                    RecoveryStep("press_escape", {}),
                    RecoveryStep("wait_ms", {"ms": 300}),
                    RecoveryStep("click_if_exists", {
                        "name": "いいえ",
                        "fallback": "press_escape"
                    }),
                ],
            ),
            "WRONG_STATE": RecoveryPlan(
                fail_type="WRONG_STATE",
                steps=[
                    RecoveryStep("press_escape", {}),
                    RecoveryStep("wait_ms", {"ms": 200}),
                    RecoveryStep("focus_window", {"app": "outlook"}),
                ],
            ),
            "FOCUS_LOST": RecoveryPlan(
                fail_type="FOCUS_LOST",
                steps=[
                    RecoveryStep("focus_window", {"app": "outlook"}),
                    RecoveryStep("wait_ms", {"ms": 300}),
                ],
            ),
            "SEND_BLOCKED": RecoveryPlan(
                fail_type="SEND_BLOCKED",
                steps=[
                    RecoveryStep("click_if_exists", {
                        "name": "許可",
                        "fallback": "wait_for_user"
                    }),
                ],
            ),
        }
        return plans.get(fail_type)
