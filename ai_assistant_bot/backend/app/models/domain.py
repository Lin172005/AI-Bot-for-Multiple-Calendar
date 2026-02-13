"""Domain/response models (kept minimal for frontend compatibility)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class PastMeeting(BaseModel):
    event_id: Optional[str] = None
    attendee_bot_id: Optional[str] = None
    title: Optional[str] = None
    start_time: Optional[str] = None
    meet_link: Optional[str] = None
    summary: Optional[str] = None
