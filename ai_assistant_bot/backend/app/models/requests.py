"""Pydantic request models (copied/adapted from AI_Assistant_Bot).

We keep these here so the bot backend can be API-compatible with the
AI_Assistant_Bot frontend without touching the AI backend.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ScheduleBotRequest(BaseModel):
    event_id: str
    title: Optional[str] = None
    start_time: Optional[str] = None  # ISO string
    meet_link: Optional[str] = None
    websocket_settings: Optional[Dict[str, Any]] = None
    # Optional: message to send in Meet chat once the bot has joined and enabled captions.
    # If provided, it is passed to the bot process as CHAT_ON_JOIN.
    chat_on_join: Optional[str] = None
    # Optional: auto-subscribe a webhook for this bot at creation time
    webhook_url: Optional[str] = None
    webhook_events: Optional[List[str]] = None  # defaults to ["bot.state_changed","transcript.update"]
    webhook_secret: Optional[str] = None


class BotsStatusRequest(BaseModel):
    meet_links: List[str]


class SummarizeRequest(BaseModel):
    bot_id: str
    updated_after: Optional[str] = None


class RAGIngestBotRequest(BaseModel):
    event_id: str
    source: str = "summary"   # or "transcript"


class RAGQueryRequest(BaseModel):
    question: str
    sources: Optional[List[str]] = None
    meeting_link: Optional[str] = None
    bot_id: Optional[str] = None
    top_k: int = 6


class RAGIngestGmailRequest(BaseModel):
    days: int = 30
    query: Optional[str] = None
    max_messages: int = 50
    label_ids: Optional[List[str]] = None


class PlannedMessageCreate(BaseModel):
    event_id: Optional[str] = None
    meet_link: Optional[str] = None
    text: str
    trigger_type: str
    scheduled_at: Optional[str] = None
    offset_minutes: Optional[int] = None
    keywords: Optional[List[str]] = None


class WebhookSubscribeRequest(BaseModel):
    """Subscribe a URL to receive webhook events for a bot.

    - If both `bot_id` and `meet_link` are omitted, subscription is rejected.
    - If both are provided, `bot_id` takes precedence.
    - `events` can include: "bot.state_changed", "transcript.update".
    """

    url: str
    events: Optional[List[str]] = None
    secret: Optional[str] = None
    bot_id: Optional[str] = None
    meet_link: Optional[str] = None
