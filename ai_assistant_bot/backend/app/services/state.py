from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import hmac
import hashlib
import json
from datetime import datetime
import httpx

from ..supabase_integration import upsert_meeting as _sb_upsert
try:
    from groq import Groq  # type: ignore
except Exception:
    Groq = None  # type: ignore


@dataclass
class BotSession:
    bot_id: str
    event_id: str
    meet_link: str
    user_id: str = ""
    title: str = ""
    start_time: str = ""
    state: str = "scheduled"  # scheduled|running|ended|error
    created_at: float = field(default_factory=lambda: time.time())
    updated_at: float = field(default_factory=lambda: time.time())
    # transcript utterances as dicts (frontend-compatible)
    utterances: List[Dict[str, Any]] = field(default_factory=list)
    # live subscribers
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    # finalized summary for quick access
    summary_text: str = ""


class AppState:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.by_bot_id: Dict[str, BotSession] = {}
        self.by_meet_link: Dict[str, str] = {}
        self.planned_messages: Dict[str, Dict[str, Any]] = {}  # pm_id -> payload
        self.command_queues: Dict[str, asyncio.Queue] = {}  # bot_id -> queue of dict commands
        # Webhook subscriptions per bot_id: List[{url, events, secret}]
        self.webhooks: Dict[str, List[Dict[str, Any]]] = {}

    async def create_bot(self, *, event_id: str, meet_link: str, title: str = "", start_time: str = "", user_id: Optional[str] = None) -> BotSession:
        async with self._lock:
            bot_id = f"bot_{uuid.uuid4().hex[:12]}"
            # Associate a Supabase user for meeting records (required by schema)
            sb_user_id = (user_id or os.getenv("SUPABASE_USER_ID", "").strip())
            session = BotSession(
                bot_id=bot_id,
                event_id=event_id,
                meet_link=meet_link,
                user_id=sb_user_id,
                title=title or "",
                start_time=start_time or "",
            )
            self.by_bot_id[bot_id] = session
            if meet_link:
                self.by_meet_link[meet_link] = bot_id
            self.command_queues[bot_id] = asyncio.Queue(maxsize=200)
            self.webhooks[bot_id] = []
            return session

    async def get_bot(self, bot_id: str) -> Optional[BotSession]:
        async with self._lock:
            return self.by_bot_id.get(bot_id)

    async def enqueue_command(self, bot_id: str, cmd: Dict[str, Any]) -> bool:
        async with self._lock:
            q = self.command_queues.get(bot_id)
            if not q:
                return False
            try:
                q.put_nowait(cmd)
                return True
            except Exception:
                return False

    async def get_command_queue(self, bot_id: str) -> Optional[asyncio.Queue]:
        async with self._lock:
            return self.command_queues.get(bot_id)

    async def cleanup_bot(self, bot_id: str) -> None:
        async with self._lock:
            self.command_queues.pop(bot_id, None)

    async def get_bot_id_for_link(self, meet_link: str) -> Optional[str]:
        async with self._lock:
            return self.by_meet_link.get(meet_link)

    async def set_state(self, bot_id: str, state: str) -> None:
        # Update state and capture webhook targets
        subs: List[Dict[str, Any]] = []
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return
            s.state = state
            s.updated_at = time.time()
            subs = list(self.webhooks.get(bot_id, []))
            payload = {
                "type": "bot.state_changed",
                "bot_id": s.bot_id,
                "event_id": s.event_id,
                "meet_link": s.meet_link,
                "state": s.state,
                "time": time.time(),
                "time_iso": datetime.utcnow().isoformat() + "Z",
            }
        # Dispatch outside the lock
        await self._dispatch_webhooks(subs, payload)

        # Finalize when meeting ends: build transcript, summarize, persist, and notify
        try:
            if str(state).lower().strip() == "ended":
                await self._finalize_session(bot_id)
        except Exception:
            pass

    async def add_utterance(self, bot_id: str, utterance: Dict[str, Any]) -> None:
        subs: List[Dict[str, Any]] = []
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return
            s.utterances.append(utterance)
            s.updated_at = time.time()
            subs = list(self.webhooks.get(bot_id, []))
            # fanout to SSE subscribers
            for q in list(s.subscribers):
                try:
                    q.put_nowait(utterance)
                except Exception:
                    pass
            payload = {
                "type": "transcript.update",
                "bot_id": s.bot_id,
                "event_id": s.event_id,
                "meet_link": s.meet_link,
                "utterance": utterance,
                "time": time.time(),
                "time_iso": datetime.utcnow().isoformat() + "Z",
            }
        await self._dispatch_webhooks(subs, payload)

    async def add_webhook(self, bot_id: str, sub: Dict[str, Any]) -> bool:
        async with self._lock:
            if bot_id not in self.by_bot_id:
                return False
            lst = self.webhooks.setdefault(bot_id, [])
            lst.append(sub)
            return True

    async def list_webhooks(self, bot_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            return list(self.webhooks.get(bot_id, []))

    async def _dispatch_webhooks(self, subs: List[Dict[str, Any]], payload: Dict[str, Any]) -> None:
        if not subs:
            return
        body = json.dumps(payload, ensure_ascii=False)
        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = []
            for sub in subs:
                events = (sub.get("events") or ["bot.state_changed", "transcript.update"])  # type: ignore
                if payload.get("type") not in events:
                    continue
                url = sub.get("url")
                if not url:
                    continue
                headers = {"Content-Type": "application/json"}
                secret = sub.get("secret")
                if secret:
                    sig = hmac.new(str(secret).encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
                    headers["X-Webhook-Signature"] = sig
                tasks.append(client.post(url, data=body.encode("utf-8"), headers=headers))
            # Fire-and-forget; ignore individual failures
            try:
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass

    @staticmethod
    def _build_transcript_text(items: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for it in items or []:
            try:
                speaker = str((it or {}).get("speaker") or (it or {}).get("from") or "").strip()
                text = str((it or {}).get("text") or (it or {}).get("content") or "").strip()
            except Exception:
                speaker = ""
                text = ""
            if not text:
                continue
            if speaker:
                lines.append(f"{speaker}: {text}")
            else:
                lines.append(text)
        return "\n".join(lines)

    @staticmethod
    def _simple_summarize(text: str, max_lines: int = 8) -> str:
        """Naive extractive summary: take first N sentences/lines."""
        s = (text or "").strip()
        if not s:
            return ""
        import re
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", s) if p.strip()]
        return "\n".join(parts[:max_lines])

    @staticmethod
    def _groq_summarize(text: str, title: str = "") -> str:
        """Generate a structured meeting summary using Groq."""
        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key or Groq is None:
            return ""

        s = (text or "").strip()
        if not s:
            return ""

        try:
            client = Groq(api_key=api_key)

            system = """
You are an expert meeting analyst.

Your task is to carefully analyze meeting transcripts and extract structured insights.

Rules:
- Do NOT invent information.
- Only use details present in the transcript.
- If something is implied but not explicitly stated, label it as "Implied".
- If no decisions were formally made, check whether the team aligned informally.
- Be precise and concise.
"""

            user = f"""
Meeting Title: {title}

Transcript:
{s}

Analyze the transcript step-by-step and generate a structured meeting report in the following format:

Overview:
(2–4 sentences summarizing purpose, context, and overall outcome)

Key Discussion Points:
- Bullet points explaining important discussions with context
- Avoid vague statements like "The team discussed the project"

Decisions Made:
- List explicit decisions
- If none explicitly stated, write:
  "No formal decisions were recorded. However, the team aligned on: ..."
- If truly none, write:
  "No decisions were made during this meeting."

Action Items:
- Format each as:
  Task – Owner (if mentioned, otherwise "Owner not specified") – Deadline (if mentioned, otherwise "No deadline specified")

Do not add any extra sections.
Be structured and clear.
"""

            model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
                temperature=0.2,
                max_tokens=1200,
            )

            content = (resp.choices[0].message.content or "").strip()
            return content

        except Exception:
            return ""

    async def _finalize_session(self, bot_id: str) -> None:
        # Gather session context
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return
            items = list(s.utterances)
            title = s.title
            meet_link = s.meet_link

        # Build transcript and summary
        text = self._build_transcript_text(items)
        # Prefer Groq when available; fall back to simple summary
        summary = self._groq_summarize(text, title=title) or self._simple_summarize(text)
        # Fallback: if no transcript was captured, provide a minimal summary
        if not summary:
            fallback_bits: List[str] = ["No transcript was captured."]
            if title:
                fallback_bits.append(f"Title: {title}")
            if meet_link:
                fallback_bits.append(f"Meeting: {meet_link}")
            try:
                when = datetime.utcnow().isoformat() + "Z"
                fallback_bits.append(f"Ended: {when}")
            except Exception:
                pass
            summary = "\n".join(fallback_bits)

        # Persist artifacts under bot/backend/data
        try:
            base_dir = Path(__file__).resolve().parents[1] / "data"
            tx_dir = base_dir / "transcripts"
            sm_dir = base_dir / "summaries"
            tx_dir.mkdir(parents=True, exist_ok=True)
            sm_dir.mkdir(parents=True, exist_ok=True)
            tx_path = tx_dir / f"{bot_id}.txt"
            sm_path = sm_dir / f"{bot_id}.txt"
            if text:
                tx_path.write_text(text, encoding="utf-8")
            if summary:
                content = summary
                if title and not content.startswith(title):
                    content = f"{title}\n\n" + content
                sm_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

        # Update in-memory summary and notify subscribers via webhook
        subs: List[Dict[str, Any]] = []
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if s:
                s.summary_text = summary or ""
                subs = list(self.webhooks.get(bot_id, []))
        payload = {
            "type": "meeting.summary.ready",
            "bot_id": bot_id,
            "title": title,
            "preview": (summary or "")[:220],
        }
        await self._dispatch_webhooks(subs, payload)

        # Persist meeting details to Supabase (best-effort)
        try:
            sb_user_id = s.user_id if s else ""
            ok = _sb_upsert(
                user_id=sb_user_id,
                event_id=s.event_id if s else "",
                title=title or "",
                start_time_iso=(s.start_time if s else ""),
                meet_link=meet_link or "",
                attendee_bot_id=bot_id,
                summary=summary or "",
            )
            if not ok:
                print(f"[bot-backend] supabase upsert (finalize) failed for bot_id={bot_id}")
        except Exception as e:
            print(f"[bot-backend] supabase upsert (finalize) error: {e}")
        # Structured RAG ingestion (summary → units → pgvector)
        try:
            from ..rag_pipeline import ingest_summary_units_for_bot
            if s:
                ingest_summary_units_for_bot(bot_session=s)
        except Exception as e:
            print("Structured RAG ingest failed:", e)


    async def regenerate_summary(self, bot_id: str) -> Optional[str]:
        """Re-run finalization summary generation (useful for manual trigger)."""
        await self._finalize_session(bot_id)
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return None
            return s.summary_text or ""

    async def add_subscriber(self, bot_id: str) -> Optional[asyncio.Queue]:
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return None
            q: asyncio.Queue = asyncio.Queue(maxsize=200)
            s.subscribers.append(q)
            return q

    async def remove_subscriber(self, bot_id: str, q: asyncio.Queue) -> None:
        async with self._lock:
            s = self.by_bot_id.get(bot_id)
            if not s:
                return
            try:
                s.subscribers.remove(q)
            except ValueError:
                pass


APP_STATE = AppState()
