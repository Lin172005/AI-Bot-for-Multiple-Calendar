from __future__ import annotations
from supabase import create_client
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv
load_dotenv()

# Allow running this file directly (python main.py) by setting package context
# so relative imports like `from .models import ...` work.
if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    # Add parent directory (bot/backend) to sys.path and mark package name.
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    __package__ = "app"

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse, StreamingResponse, JSONResponse
import httpx

from .models import (
    BotsStatusRequest,
    PastMeeting,
    PlannedMessageCreate,
    ScheduleBotRequest,
    SummarizeRequest,
    RAGIngestBotRequest,
    RAGQueryRequest,
    RAGIngestGmailRequest,
    WebhookSubscribeRequest,
)
from .services.state import APP_STATE
from .supabase_integration import upsert_meeting as _sb_upsert
from .supabase_integration import fetch_meetings as _sb_fetch
from .supabase_integration import health as _sb_health
from .supabase_integration import fetch_meetings as _sb_fetch
from .rag_pipeline import ingest_summary_units_for_bot, answer_question_with_rag

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Supabase environment variables not set")

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
def _load_env_files() -> None:
    """Best-effort .env loader.

    This lets you set config (e.g., HEADLESS=1) in bot/.env and have it apply
    no matter whether the bot is launched via UI (FastAPI) or manually.
    """

    try:
        from dotenv import load_dotenv  # type: ignore
    except Exception:
        return

    bot_root = Path(__file__).resolve().parents[2]  # .../bot
    for name in (".env", ".env.local"):
        p = bot_root / name
        if p.exists():
            load_dotenv(dotenv_path=p, override=False)


_load_env_files()


FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
PORT = int(os.getenv("PORT", "8010"))

# Optional defaults for webhook auto-subscription when creating bots
DEFAULT_WEBHOOK_URL = (os.getenv("WEBHOOK_URL", "") or "").strip()
_env_events = (os.getenv("WEBHOOK_EVENTS", "") or "").strip()
DEFAULT_WEBHOOK_EVENTS: Optional[List[str]] = None
if _env_events:
    try:
        DEFAULT_WEBHOOK_EVENTS = [e.strip() for e in _env_events.split(",") if e.strip()]
    except Exception:
        DEFAULT_WEBHOOK_EVENTS = None
DEFAULT_WEBHOOK_SECRET = (os.getenv("WEBHOOK_SECRET", "") or "").strip() or None

# main.py is located at: bot/backend/app/main.py
# We want to run:        bot/backend/bot.py
BOT_PY_PATH = Path(__file__).resolve().parents[1] / "bot.py"  # bot/backend/bot.py

# Prefer the bot workspace venv if present: bot/.venv/Scripts/python.exe
BOT_VENV_PY = Path(__file__).resolve().parents[2] / ".venv" / "Scripts" / "python.exe"

# Keep logs next to the existing backend data dir: bot/backend/data/
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Track running bot processes and scheduled start tasks so we can stop them.
BOT_PROCESSES: Dict[str, subprocess.Popen] = {}
BOT_START_TASKS: Dict[str, asyncio.Task] = {}
BOT_PLANNED_MESSAGE_TASKS: Dict[str, List[asyncio.Task]] = {}

app = FastAPI(title="Meet Caption Bot Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ok_auth(_: Optional[str]) -> None:
    # For now, dev-only: accept any token. Frontend passes Supabase JWT.
    # Later you can add real verification here.
    return

def _extract_user_id_from_jwt(auth_header: Optional[str]) -> Optional[str]:
    """Best-effort decode of a Supabase JWT to extract user id (sub).

    Accepts "Bearer <jwt>"; returns the 'sub' claim if present.
    """
    try:
        if not auth_header:
            return None
        parts = auth_header.split()
        token = parts[-1] if parts else auth_header
        segs = token.split(".")
        if len(segs) < 2:
            return None
        import base64, json as _json
        def b64url_decode(s: str) -> bytes:
            pad = "=" * ((4 - len(s) % 4) % 4)
            return base64.urlsafe_b64decode(s + pad)
        payload_raw = b64url_decode(segs[1])
        data = _json.loads(payload_raw.decode("utf-8", errors="ignore"))
        sub = str(data.get("sub") or data.get("user_id") or "").strip()
        return sub or None
    except Exception:
        return None


@app.get("/")
async def root():
    return {"ok": True, "service": "bot-backend", "time": time.time()}


@app.get("/debug/supabase")
async def debug_supabase():
    """Diagnostic endpoint to check Supabase configuration and access."""
    h = _sb_health()
    return {"supabase": h, "env": {
        "SUPABASE_USER_ID_set": bool((os.getenv("SUPABASE_USER_ID") or "").strip()),
    }}


@app.get("/debug/meetings")
async def debug_meetings(user_id: Optional[str] = Query(default=None), meet_link: Optional[str] = Query(default=None)):
    """Fetch meetings from Supabase to verify writes."""
    # Default to env user_id if not provided
    uid = (user_id or os.getenv("SUPABASE_USER_ID") or "").strip() or None
    ml = (meet_link or "").strip() or None
    res = _sb_fetch(user_id=uid, meet_link=ml, limit=10)
    return {"user_id": uid, "meet_link": ml, **res}


@app.get("/bots/{bot_id}/transcript")
async def get_bot_transcript(bot_id: str, format: str = Query("text")):
    """Return transcript for a bot.

    - format=text: returns plain concatenated text
    - format=json: returns raw items array
    """
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    items = list(s.utterances)
    if (format or "").lower() == "json":
        return {"bot_id": bot_id, "items": items}
    # Build transcript text via helper
    try:
        text = APP_STATE._build_transcript_text(items)  # type: ignore[attr-defined]
    except Exception:
        text = "".join([(it or {}).get("text") or "" for it in items])
    return {"bot_id": bot_id, "text": text}


@app.post("/bots/summarize")
async def summarize(req: SummarizeRequest, authorization: Optional[str] = Header(default=None)):
    """Regenerate the summary for a bot using Groq if configured.

    - Reads GROQ_API_KEY and GROQ_MODEL from env.
    - Persists the new summary and dispatches meeting.summary.ready.
    """
    _ok_auth(authorization)
    await APP_STATE.regenerate_summary(req.bot_id)
    s = await APP_STATE.get_bot(req.bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    return {"bot_id": req.bot_id, "summary": s.summary_text or ""}


@app.get("/bots/{bot_id}/summary")
async def get_bot_summary(bot_id: str):
    """Return generated summary for a bot if available."""
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    summary = (s.summary_text or "").strip()
    if not summary:
        # Try loading from persisted file
        try:
            base_dir = Path(__file__).resolve().parents[1] / "data" / "summaries"
            p = base_dir / f"{bot_id}.txt"
            if p.exists():
                summary = p.read_text(encoding="utf-8")
        except Exception:
            summary = ""
    return {"bot_id": bot_id, "summary": summary}


@app.get("/events")
async def events(
    links_only: bool = Query(default=True),
    calendar_ids: Optional[List[str]] = Query(default=None),
    time_min: Optional[str] = Query(default=None),
    time_max: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    x_provider_token: Optional[str] = Header(default=None),
):
    """Fetch events from Google Calendar (fallback service).

    - Provide `x_provider_token` as a Google OAuth access token with `https://www.googleapis.com/auth/calendar.readonly`.
    - If `calendar_ids` is omitted, uses the user's calendar list.
    - Returns `deleted_ids` for events with status=cancelled so UI can remove them.
    - When `links_only` is true, returns only unique Meet links.
    """
    _ok_auth(authorization)
    token = (x_provider_token or "").strip()
    if not token:
        # No provider token; nothing to fetch.
        return {"events": [], "deleted_ids": [], "links": []}

    async with httpx.AsyncClient(timeout=10.0) as client:
        # Resolve calendar IDs if not provided
        cals: List[str] = []
        if calendar_ids:
            cals = [c for c in calendar_ids if (c or "").strip()]
        else:
            try:
                r = await client.get(
                    "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if r.status_code == 200:
                    data = r.json() or {}
                    for item in (data.get("items") or []):
                        cid = (item.get("id") or "").strip()
                        if cid:
                            cals.append(cid)
            except Exception:
                pass

        events: List[Dict[str, Any]] = []
        deleted_ids: List[str] = []
        unique_links: Dict[str, bool] = {}

        for cid in cals:
            params = {
                "singleEvents": "true",
                "orderBy": "startTime",
                "showDeleted": "true",
            }
            if time_min:
                params["timeMin"] = time_min
            if time_max:
                params["timeMax"] = time_max
            try:
                r = await client.get(
                    f"https://www.googleapis.com/calendar/v3/calendars/{httpx.utils.escape_xml(str(cid))}/events",
                    headers={"Authorization": f"Bearer {token}"},
                    params=params,
                )
                if r.status_code != 200:
                    continue
                data = r.json() or {}
                for ev in (data.get("items") or []):
                    status = (ev.get("status") or "").strip().lower()
                    if status == "cancelled":
                        eid = (ev.get("id") or "").strip()
                        if eid:
                            deleted_ids.append(eid)
                        continue
                    # Extract Meet link
                    meet_link = (ev.get("hangoutLink") or "").strip()
                    if not meet_link:
                        try:
                            conf = ev.get("conferenceData") or {}
                            for ep in (conf.get("entryPoints") or []):
                                if (ep.get("entryPointType") or "").lower() == "video":
                                    meet_link = (ep.get("uri") or "").strip()
                                    if meet_link:
                                        break
                        except Exception:
                            pass
                    if meet_link:
                        unique_links[meet_link] = True
                    events.append({
                        "id": ev.get("id"),
                        "summary": ev.get("summary"),
                        "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date"),
                        "end": (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date"),
                        "updated": ev.get("updated"),
                        "status": status,
                        "meet_link": meet_link,
                        "calendar_id": cid,
                    })
            except Exception:
                continue

    links = list(unique_links.keys())
    if links_only:
        return {"links": links, "deleted_ids": deleted_ids}
    return {"events": events, "deleted_ids": deleted_ids, "links": links}


@app.get("/calendars")
async def calendars(authorization: Optional[str] = Header(default=None), x_provider_token: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    return {"calendars": []}


@app.post("/bots/status")
async def bots_status(req: BotsStatusRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    status: Dict[str, str] = {}
    detail: Dict[str, Any] = {}
    for link in req.meet_links:
        bot_id = await APP_STATE.get_bot_id_for_link(link)
        if bot_id:
            status[link] = bot_id
            s = await APP_STATE.get_bot(bot_id)
            if s:
                detail[link] = {"state": s.state, "bot_id": s.bot_id}
    return {"status": status, "detail": detail}


@app.post("/schedule-bot")
async def schedule_bot(req: ScheduleBotRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    if not req.meet_link:
        raise HTTPException(status_code=400, detail={"error": "meet_link is required"})

    # Tie meeting rows to the logged-in Supabase user id for UI visibility
    user_id = _extract_user_id_from_jwt(authorization)
    session = await APP_STATE.create_bot(
        event_id=req.event_id,
        meet_link=req.meet_link,
        title=req.title or "",
        start_time=req.start_time or "",
        user_id=user_id or "",
    )

    # Record meeting in Supabase (best-effort)
    try:
        ok = _sb_upsert(
            user_id=session.user_id,
            event_id=session.event_id,
            title=session.title,
            start_time_iso=session.start_time,
            meet_link=session.meet_link,
            attendee_bot_id=session.bot_id,
            summary="",
        )
        if not ok:
            print(f"[bot-backend] supabase upsert failed for bot_id={session.bot_id}")
    except Exception as e:
        print(f"[bot-backend] supabase upsert error: {e}")

    # Fire-and-forget: start the Playwright bot.
    # If a start_time is provided, wait until then; otherwise start immediately.
    print(f"[bot-backend] scheduled bot_id={session.bot_id} start_time={session.start_time!r} meet_link={session.meet_link}")
    task = asyncio.create_task(
        _start_bot_process_at_time(
            session.bot_id,
            session.meet_link,
            session.start_time,
            chat_on_join=(req.chat_on_join or "").strip(),
        )
    )
    BOT_START_TASKS[session.bot_id] = task

    # Best-effort planned message scheduling (chat messages like Attendee).
    asyncio.create_task(
        _schedule_planned_messages_for_bot(
            session.bot_id,
            event_id=session.event_id,
            meet_link=session.meet_link,
            start_time_iso=session.start_time,
        )
    )

    # Subscribe webhook: use request-provided values, else fall back to env defaults.
    try:
        effective_url = (req.webhook_url or DEFAULT_WEBHOOK_URL or "").strip()
        if effective_url:
            # Prefer request-provided events, else env defaults, else safe defaults
            events = req.webhook_events or DEFAULT_WEBHOOK_EVENTS or ["bot.state_changed", "transcript.update", "meeting.summary.ready"]
            if "meeting.summary.ready" not in events:
                events.append("meeting.summary.ready")
            # Prefer request-provided secret, else env default
            secret = (req.webhook_secret or DEFAULT_WEBHOOK_SECRET)
            sub = {"url": effective_url, "events": events, "secret": (secret or None)}
            await APP_STATE.add_webhook(session.bot_id, sub)
    except Exception:
        # ignore webhook subscribe failures for now
        pass

    return {
        "bot_id": session.bot_id,
        "attendee_response": {
            "id": session.bot_id,
            "bot_id": session.bot_id,
            "meet_link": session.meet_link,
            "state": session.state,
        },
        "webhook_subscribed": bool((req.webhook_url or DEFAULT_WEBHOOK_URL or "").strip()),
    }


@app.post("/webhooks/subscribe")
async def webhook_subscribe(req: WebhookSubscribeRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    # Resolve bot_id via meet_link if necessary
    bot_id = (req.bot_id or "").strip()
    if not bot_id and (req.meet_link or "").strip():
        maybe = await APP_STATE.get_bot_id_for_link((req.meet_link or "").strip())
        if maybe:
            bot_id = maybe
    if not bot_id:
        raise HTTPException(status_code=400, detail={"error": "bot_id or meet_link is required"})

    events = req.events or ["bot.state_changed", "transcript.update"]
    if "meeting.summary.ready" not in events:
        events.append("meeting.summary.ready")
    sub = {"url": req.url, "events": events, "secret": req.secret or None}
    ok = await APP_STATE.add_webhook(bot_id, sub)
    if not ok:
        raise HTTPException(status_code=404, detail={"error": "bot session not found"})
    return {"ok": True, "bot_id": bot_id, "events": events}


@app.get("/webhooks/subscriptions")
async def webhook_list(bot_id: Optional[str] = Query(default=None), meet_link: Optional[str] = Query(default=None), authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    b = (bot_id or "").strip()
    if not b and (meet_link or "").strip():
        maybe = await APP_STATE.get_bot_id_for_link((meet_link or "").strip())
        if maybe:
            b = maybe
    if not b:
        raise HTTPException(status_code=400, detail={"error": "bot_id or meet_link is required"})
    subs = await APP_STATE.list_webhooks(b)
    # Do not return secrets
    sanitized = [{"url": s.get("url"), "events": s.get("events", [])} for s in subs]
    return {"bot_id": b, "subscriptions": sanitized}


def _parse_iso_datetime(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if not s:
        return None

    # Handle common "Z" form.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None

    # If no timezone info, assume LOCAL timezone (frontend/Google calendar often uses local time).
    if dt.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        dt = dt.replace(tzinfo=local_tz)
    return dt


async def _enqueue_chat(bot_id: str, text: str, *, source: str = "api") -> bool:
    text = (text or "").strip()
    if not text:
        return False
    cmd = {"type": "chat", "text": text, "source": source, "ts": time.time()}
    return await APP_STATE.enqueue_command(bot_id, cmd)


def _pm_matches_session(pm: Dict[str, Any], *, event_id: str, meet_link: str) -> bool:
    pm_event_id = (pm.get("event_id") or "").strip()
    pm_meet_link = (pm.get("meet_link") or "").strip()
    if pm_event_id and pm_event_id != event_id:
        return False
    if pm_meet_link and pm_meet_link != meet_link:
        return False
    return True


def _pm_mark_posted(pm: Dict[str, Any], *, bot_id: str, source: str, extra: Optional[Dict[str, Any]] = None) -> None:
    pm["status"] = "posted"
    pm["posted_at"] = datetime.now(timezone.utc).isoformat()
    pm["posted_bot_id"] = bot_id
    pm["posted_source"] = source
    if extra:
        try:
            pm.update(extra)
        except Exception:
            pass


async def _maybe_trigger_keyword_planned_messages(bot_id: str, *, meet_link: str, text: str) -> None:
    s = await APP_STATE.get_bot(bot_id)
    if not s or s.state == "ended":
        return

    event_id = s.event_id
    msg_text = (text or "").strip().lower()
    if not msg_text:
        return

    for pm_id, pm in list(APP_STATE.planned_messages.items()):
        try:
            if not isinstance(pm, dict):
                continue
            if not _pm_matches_session(pm, event_id=event_id, meet_link=meet_link):
                continue

            trigger_type = (pm.get("trigger_type") or "").strip().lower()
            if trigger_type not in {"keyword", "keywords", "keyword_cues"}:
                continue

            # Only post once per planned message for now.
            if (pm.get("status") or "").strip().lower() == "posted":
                continue

            keywords = pm.get("keywords") or []
            if not isinstance(keywords, list) or not keywords:
                continue

            matched = None
            for kw in keywords:
                k = (kw or "").strip().lower()
                if k and k in msg_text:
                    matched = k
                    break

            if not matched:
                continue

            say = (pm.get("text") or "").strip()
            if not say:
                continue

            ok = await _enqueue_chat(bot_id, say, source="planned_keyword")
            if ok:
                _pm_mark_posted(pm, bot_id=bot_id, source="planned_keyword", extra={"matched_keyword": matched})
                APP_STATE.planned_messages[pm_id] = pm
        except Exception:
            continue


async def _schedule_planned_messages_for_bot(bot_id: str, *, event_id: str, meet_link: str, start_time_iso: str) -> None:
    # Cancel existing tasks for this bot.
    for t in BOT_PLANNED_MESSAGE_TASKS.pop(bot_id, []):
        try:
            t.cancel()
        except Exception:
            pass

    start_dt = _parse_iso_datetime(start_time_iso) if start_time_iso else None
    now_utc = datetime.now(timezone.utc)
    tasks: List[asyncio.Task] = []

    planned_items = list(APP_STATE.planned_messages.items())
    for pm_id, pm in planned_items:
        try:
            if not isinstance(pm, dict):
                continue
            if not _pm_matches_session(pm, event_id=event_id, meet_link=meet_link):
                continue

            trigger_type = (pm.get("trigger_type") or "").strip().lower()
            text = (pm.get("text") or "").strip()
            if not text:
                continue

            # Keyword-cue triggers are handled on incoming captions.
            if trigger_type in {"keyword", "keywords", "keyword_cues"}:
                continue

            if (pm.get("status") or "").strip().lower() == "posted":
                continue

            due: Optional[datetime] = None

            scheduled_at = (pm.get("scheduled_at") or "").strip()
            if trigger_type in {"scheduled", "scheduled_at", "time"} and scheduled_at:
                due = _parse_iso_datetime(scheduled_at)

            offset_minutes = pm.get("offset_minutes")
            if due is None and trigger_type in {"offset", "offset_minutes", "start_offset"} and offset_minutes is not None:
                base = start_dt or datetime.now().astimezone()
                due = base + timedelta(minutes=int(offset_minutes))

            if due is None:
                continue

            due_utc = due.astimezone(timezone.utc)
            delay = (due_utc - now_utc).total_seconds()
            if delay < 0:
                delay = 0

            async def _run_later(planned_id: str, message_text: str, seconds: float) -> None:
                if seconds:
                    await asyncio.sleep(seconds)
                s = await APP_STATE.get_bot(bot_id)
                if not s or s.state == "ended":
                    return
                pm_current = APP_STATE.planned_messages.get(planned_id)
                if not isinstance(pm_current, dict):
                    return
                # If deleted or already posted, don't enqueue.
                if (pm_current.get("status") or "").strip().lower() == "posted":
                    return
                if not _pm_matches_session(pm_current, event_id=event_id, meet_link=meet_link):
                    return
                ok = await _enqueue_chat(bot_id, message_text, source="planned")
                if ok:
                    _pm_mark_posted(pm_current, bot_id=bot_id, source="planned")
                    APP_STATE.planned_messages[planned_id] = pm_current

            tasks.append(asyncio.create_task(_run_later(pm_id, text, delay)))
        except Exception:
            continue

    BOT_PLANNED_MESSAGE_TASKS[bot_id] = tasks


@app.post("/bots/{bot_id}/chat")
async def bot_send_chat(bot_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    payload = await request.json()
    text = (payload.get("text") or payload.get("message") or "").strip()
    ok = await _enqueue_chat(bot_id, text, source="api")
    return {"ok": ok, "bot_id": bot_id}


@app.get("/bots/{bot_id}/commands/next")
async def bot_next_command(bot_id: str, timeout: int = Query(default=25), authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})

    q = await APP_STATE.get_command_queue(bot_id)
    if q is None:
        return {"command": None}

    try:
        cmd = await asyncio.wait_for(q.get(), timeout=max(1, min(int(timeout), 60)))
        return {"command": cmd}
    except asyncio.TimeoutError:
        return {"command": None}


async def _start_bot_process_at_time(bot_id: str, meet_link: str, start_time_iso: str, chat_on_join: str = "") -> None:
    dt = _parse_iso_datetime(start_time_iso)
    if dt is None:
        print(f"[bot-backend] starting bot_id={bot_id} immediately (no/invalid start_time)")
        await _start_bot_process(bot_id, meet_link, chat_on_join=chat_on_join)
        return

    now = datetime.now(timezone.utc)
    target = dt.astimezone(timezone.utc)
    delay = (target - now).total_seconds()
    if delay <= 0:
        print(f"[bot-backend] starting bot_id={bot_id} immediately (start_time already passed)")
        await _start_bot_process(bot_id, meet_link, chat_on_join=chat_on_join)
        return

    # Mark as scheduled (already is by default) and sleep until time.
    await APP_STATE.set_state(bot_id, "scheduled")
    print(f"[bot-backend] bot_id={bot_id} will start in {int(delay)}s at {target.isoformat()}")
    # Cap sleep chunks so cancellation/state changes can be added later.
    remaining = delay
    while remaining > 0:
        await asyncio.sleep(min(remaining, 15))
        remaining -= 15
        s = await APP_STATE.get_bot(bot_id)
        if not s or s.state == "ended":
            return

    await _start_bot_process(bot_id, meet_link, chat_on_join=chat_on_join)


async def _stop_bot_process(bot_id: str) -> bool:
    """Stop scheduled start and terminate a running bot process.

    Returns True if a process/task was stopped, False if nothing was running.
    """

    stopped_any = False

    task = BOT_START_TASKS.pop(bot_id, None)
    if task is not None and not task.done():
        task.cancel()
        stopped_any = True

    for t in BOT_PLANNED_MESSAGE_TASKS.pop(bot_id, []):
        try:
            t.cancel()
            stopped_any = True
        except Exception:
            pass

    proc = BOT_PROCESSES.pop(bot_id, None)
    if proc is None:
        await APP_STATE.cleanup_bot(bot_id)
        return stopped_any

    stopped_any = True

    # Try graceful terminate first.
    try:
        proc.terminate()
    except Exception:
        pass

    # Wait a bit; if it doesn't exit, kill the whole tree.
    try:
        await asyncio.get_running_loop().run_in_executor(None, lambda: proc.wait(timeout=10))
        return True
    except Exception:
        pass

    pid = getattr(proc, "pid", None)
    if pid:
        try:
            # Windows: kill process tree (bot python + chromium)
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return True
        except Exception:
            pass

    try:
        proc.kill()
    except Exception:
        pass

    await APP_STATE.cleanup_bot(bot_id)

    return True


@app.delete("/schedule-bot/{bot_id}")
async def remove_bot(bot_id: str, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    await APP_STATE.set_state(bot_id, "ended")
    await _stop_bot_process(bot_id)
    return {"ok": True, "bot_id": bot_id}


@app.post("/bots/{bot_id}/finalize")
async def finalize_bot(bot_id: str, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    await APP_STATE.set_state(bot_id, "ended")
    await _stop_bot_process(bot_id)
    return {"ok": True, "bot_id": bot_id}


@app.get("/bots/{bot_id}/transcript")
async def bot_transcript(bot_id: str, format: str = Query(default="text"), authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})

    if format == "json":
        return {"bot_id": bot_id, "utterances": s.utterances}

    lines: List[str] = []
    for u in s.utterances:
        speaker = u.get("speaker") or u.get("speaker_name") or "Unknown"
        text = (u.get("transcription") or {}).get("transcript") or u.get("text") or ""
        ts_ms = u.get("timestamp_ms")
        if ts_ms:
            t = datetime.fromtimestamp(ts_ms / 1000.0).strftime("%H:%M:%S")
            lines.append(f"[{t}] {speaker}: {text}")
        else:
            lines.append(f"{speaker}: {text}")

    return PlainTextResponse("\n".join(lines))


@app.get("/bots/{bot_id}/transcript/stream")
async def transcript_stream(bot_id: str, request: Request, access_token: Optional[str] = Query(default=None), authorization: Optional[str] = Header(default=None)):
    # Authenticate via Authorization header OR access_token query param (EventSource limitation).
    _ok_auth(authorization or access_token)

    q = await APP_STATE.add_subscriber(bot_id)
    if q is None:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})

    async def event_generator():
        try:
            # initial comment to open the stream
            yield ": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    utter = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                payload = json.dumps(utter, ensure_ascii=False)
                yield f"event: utterance\ndata: {payload}\n\n"
        finally:
            await APP_STATE.remove_subscriber(bot_id, q)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "Content-Type": "text/event-stream",
    }
    return StreamingResponse(event_generator(), headers=headers, media_type="text/event-stream")


@app.post("/captions")
async def receive_caption(payload: Dict[str, Any]):
    # Accept captions posted from Playwright bot.
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty text"}, status_code=400)

    meet_link = (payload.get("meet_link") or "").strip()
    bot_id = (payload.get("bot_id") or "").strip()

    if not bot_id and meet_link:
        bot_id = await APP_STATE.get_bot_id_for_link(meet_link) or ""

    if not bot_id:
        # If we can't map it, drop it (or you can auto-create a session here)
        return {"ok": False, "error": "unknown bot_id"}

    speaker = (payload.get("speaker") or "Unknown").strip() or "Unknown"
    ts = payload.get("ts") or time.time()
    try:
        ts_f = float(ts)
    except Exception:
        ts_f = time.time()

    utter = {
        "id": payload.get("id") or uuid.uuid4().hex,
        "speaker": speaker,
        "speaker_name": speaker,
        "timestamp_ms": int(ts_f * 1000),
        "text": text,
        "transcription": {"transcript": text},
        "meeting_id": payload.get("meeting_id"),
        "meet_link": meet_link,
        "bot_id": bot_id,
    }

    await APP_STATE.add_utterance(bot_id, utter)

    try:
        await _auto_reply_if_question(bot_id, speaker, text)
    except Exception as e:
        print("Auto reply error:", e)

    # Existing keyword triggers
    try:
        await _maybe_trigger_keyword_planned_messages(bot_id, meet_link=meet_link, text=text)
    except Exception:
        pass

    return {"ok": True}
    # Trigger any keyword-based planned messages using incoming transcript.
    try:
        await _maybe_trigger_keyword_planned_messages(bot_id, meet_link=meet_link, text=text)
    except Exception:
        pass
    return {"ok": True}


@app.post("/bots/summarize")
async def bots_summarize(req: SummarizeRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(req.bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    # Minimal placeholder summary: last ~10 utterances concatenated.
    last = s.utterances[-10:]
    text = " ".join(((u.get("transcription") or {}).get("transcript") or u.get("text") or "").strip() for u in last if u)
    return {"summary": text.strip()}


@app.get("/meetings/past", response_model=List[PastMeeting])
async def meetings_past(
    days: str = Query(default="30"),
    limit: int = Query(default=200),
    order: str = Query(default="desc"),
    verify: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _ok_auth(authorization)
    # Try to derive the Supabase user id to fetch persisted meetings
    user_id = _extract_user_id_from_jwt(authorization)

    items: List[PastMeeting] = []

    # Prefer persisted meetings from Supabase when configured
    try:
        if user_id:
            res = _sb_fetch(user_id=user_id, limit=limit)
            if res.get("ok"):
                for row in (res.get("rows") or []):
                    try:
                        items.append(PastMeeting(
                            event_id=row.get("event_id"),
                            attendee_bot_id=row.get("attendee_bot_id"),
                            title=row.get("title"),
                            start_time=row.get("start_time"),
                            meet_link=row.get("meet_link"),
                            summary=row.get("summary"),
                        ))
                    except Exception:
                        pass
    except Exception:
        # fall through to in-memory
        pass

    # Fallback: use in-memory sessions seen in this process
    if not items:
        for bot_id, s in list(APP_STATE.by_bot_id.items()):
            if not s.utterances:
                continue
            items.append(PastMeeting(
                event_id=s.event_id,
                attendee_bot_id=s.bot_id,
                title=s.title,
                start_time=s.start_time,
                meet_link=s.meet_link,
                summary=None,
            ))

    # Optional: filter by days window when a numeric value is provided
    try:
        import datetime as _dt
        if str(days).lower().strip() not in ("all", "0"):
            dval = int(str(days))
            cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=dval)
            def _parse_iso(s: Optional[str]) -> Optional[_dt.datetime]:
                if not s:
                    return None
                try:
                    # Accept both with and without trailing Z
                    s2 = s.rstrip("Z")
                    return _dt.datetime.fromisoformat(s2)
                except Exception:
                    return None
            items = [i for i in items if (_parse_iso(i.start_time) or _dt.datetime.min) >= cutoff]
    except Exception:
        pass

    # Order and limit
    if order == "desc":
        try:
            # Sort by start_time descending when available
            import datetime as _dt
            def _key(i: PastMeeting) -> _dt.datetime:
                try:
                    s = (i.start_time or "").rstrip("Z")
                    return _dt.datetime.fromisoformat(s)
                except Exception:
                    return _dt.datetime.min
            items.sort(key=_key, reverse=True)
        except Exception:
            items.reverse()
    return items[:limit]


@app.get("/planned-messages")
async def list_planned_messages(
    event_id: Optional[str] = Query(default=None),
    meet_link: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _ok_auth(authorization)
    items = list(APP_STATE.planned_messages.values())
    if not event_id and not meet_link:
        return {"items": items}

    e = (event_id or "").strip()
    m = (meet_link or "").strip()
    filtered: List[Dict[str, Any]] = []
    for pm in items:
        if not isinstance(pm, dict):
            continue
        if e and (pm.get("event_id") or "").strip() != e:
            continue
        if m and (pm.get("meet_link") or "").strip() != m:
            continue
        filtered.append(pm)
    return {"items": filtered}


@app.post("/planned-messages")
async def create_planned_message(req: PlannedMessageCreate, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    pm_id = f"pm_{uuid.uuid4().hex[:10]}"
    payload = {"id": pm_id, "status": "pending", **req.model_dump()}
    APP_STATE.planned_messages[pm_id] = payload
    # Best-effort: apply to existing bots too.
    try:
        for bot_id, s in list(APP_STATE.by_bot_id.items()):
            if not s or s.state == "ended":
                continue
            asyncio.create_task(_schedule_planned_messages_for_bot(
                bot_id,
                event_id=s.event_id,
                meet_link=s.meet_link,
                start_time_iso=s.start_time,
            ))
    except Exception:
        pass
    return payload


@app.delete("/planned-messages/{pm_id}")
async def delete_planned_message(pm_id: str, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    APP_STATE.planned_messages.pop(pm_id, None)
    return {"ok": True}


@app.post("/rag/ingest-bot")
async def rag_ingest_bot(req: RAGIngestBotRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(req.bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})

    # Build transcript text and generate a summary ONLY (do not ingest full transcript)
    try:
        text = APP_STATE._build_transcript_text(list(s.utterances))  # type: ignore[attr-defined]
    except Exception:
        text = ""
    summary = APP_STATE._groq_summarize(text, title=s.title) or APP_STATE._simple_summarize(text)  # type: ignore[attr-defined]

    # Fallback minimal summary if nothing captured
    if not (summary or "").strip():
        bits: List[str] = ["No transcript was captured."]
        if s.title:
            bits.append(f"Title: {s.title}")
        if s.meet_link:
            bits.append(f"Meeting: {s.meet_link}")
        try:
            when = datetime.utcnow().isoformat() + "Z"
            bits.append(f"Ingested: {when}")
        except Exception:
            pass
        summary = "\n".join(bits)

    # Try to get the RAG store (prefers Supabase pgvector when RAG_BACKEND=supabase)
    backend = (os.getenv("RAG_BACKEND") or "chroma").lower()
    try:
        from .rag import get_rag_store
        store = get_rag_store()
    except Exception as e:
        # If Supabase backend requested but not configured, return guidance
        if backend == "supabase":
            return JSONResponse({
                "ok": False,
                "reason": "supabase-not-configured",
                "error": str(e),
                "instructions": {
                    "env": [
                        "Set RAG_BACKEND=supabase",
                        "Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY",
                        "Optionally set OPENAI_API_KEY for embeddings (else local embeddings are used)",
                    ],
                    "database": "Create pgvector table/functions: insert_rag_doc and match_rag_docs as described in bot/backend/app/rag.py (SupabaseVectorStore).",
                },
            }, status_code=400)
        # Otherwise generic store unavailable
        return JSONResponse({
            "ok": False,
            "reason": "rag-store-unavailable",
            "error": str(e),
        }, status_code=400)

    # Prepare metadata and ingest only the summary
    user_id = (s.user_id or os.getenv("RAG_USER_ID") or "local").strip() or "local"
    metadata: Dict[str, Any] = {
        "source": "summary",
        "bot_id": s.bot_id,
        "title": s.title,
    }
    if s.meet_link:
        metadata["meeting_link"] = s.meet_link

    try:
        ingested = store.ingest_text(user_id=user_id, text=summary or "", metadata=metadata, external_id=f"{s.bot_id}:summary")
    except Exception as e:
        # Surface Supabase-specific guidance when backend is supabase
        if backend == "supabase":
            return JSONResponse({
                "ok": False,
                "reason": "supabase-ingest-failed",
                "error": str(e),
                "hint": "Ensure pgvector functions exist and SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY are correct.",
            }, status_code=400)
        return JSONResponse({
            "ok": False,
            "reason": "ingest-failed",
            "error": str(e),
        }, status_code=400)

    return {
        "ok": True,
        "backend": backend,
        "bot_id": s.bot_id,
        "ingested_chunks": int(ingested or 0),
    }


@app.post("/rag/ingest-summary-units")
async def rag_ingest_summary_units(
    req: RAGIngestBotRequest,
    authorization: Optional[str] = Header(default=None)
):
    _ok_auth(authorization)

    uid = _extract_user_id_from_jwt(authorization)

    # Fetch meeting from Supabase
    result = supabase.table("meetings") \
        .select("*") \
        .eq("event_id", req.event_id) \
        .eq("user_id", uid) \
        .single() \
        .execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Meeting not found")

    row = result.data

    bot_id = row.get("attendee_bot_id")
    if not bot_id:
        raise HTTPException(status_code=404, detail="Bot ID missing for this meeting")

    from types import SimpleNamespace

    synth = SimpleNamespace(
        bot_id=bot_id,
        event_id=row.get("event_id") or "",
        meet_link=row.get("meet_link") or "",
        user_id=row.get("user_id") or uid,
        title=row.get("title") or "",
        start_time=row.get("start_time") or "",
        summary_text=row.get("summary") or "",
        utterances=[],
    )

    try:
        res = ingest_summary_units_for_bot(bot_session=synth)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return res


@app.post("/rag/answer")
async def rag_answer(req: RAGQueryRequest, authorization: Optional[str] = Header(default=None)):
    """RAG answer endpoint: embed question, retrieve contexts from Supabase, answer via Groq.

    Body fields:
    - question: str
    - top_k: int
    - min_similarity: float (0..1)
    - meeting_link/bot_id/sources -> translated to filters if provided
    """
    _ok_auth(authorization)
    filters: Dict[str, Any] = {}
    # Map request fields to filters
    if req.bot_id:
        filters["bot_id"] = req.bot_id
    if req.meeting_link:
        filters["meeting_id"] = req.meeting_link  # treat meeting_link as id if needed
    user_id = os.getenv("RAG_USER_ID", "local")
    res = answer_question_with_rag(
        question=req.question or "",
        user_id=user_id,
        filters=filters,
        top_k=req.top_k or 6,
        min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.65")),
    )
    return res


@app.get("/meetings/resolve-bot-id")
async def resolve_bot_id(
    event_id: Optional[str] = Query(default=None),
    meet_link: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    """Resolve bot_id for a meeting using Supabase records when available.

    Falls back to in-memory mapping only if Supabase does not return a result.
    """
    _ok_auth(authorization)
    # Attempt Supabase lookup
    try:
        uid = _extract_user_id_from_jwt(authorization)
        res = _sb_fetch(user_id=uid, meet_link=(meet_link or None), limit=50)
        if res.get("ok"):
            rows = res.get("rows") or []
            # Prefer exact meet_link match, otherwise match event_id
            bot_id = None
            for r in rows:
                if meet_link and (r.get("meet_link") or "").strip() == (meet_link or "").strip():
                    b = (r.get("attendee_bot_id") or "").strip()
                    if b:
                        bot_id = b
                        break
            if not bot_id and event_id:
                for r in rows:
                    if (r.get("event_id") or "").strip() == (event_id or "").strip():
                        b = (r.get("attendee_bot_id") or "").strip()
                        if b:
                            bot_id = b
                            break
            if bot_id:
                return {"bot_id": bot_id}
    except Exception:
        # ignore supabase errors and fall back
        pass

    # Fallback: attempt in-memory mapping by meet_link
    if meet_link:
        try:
            b = await APP_STATE.get_bot_id_for_link(meet_link)
            if b:
                return {"bot_id": b}
        except Exception:
            pass

    # Not found
    return JSONResponse({"bot_id": None, "error": "not-found"}, status_code=404)


@app.post("/rag/query")
async def rag_query(req: RAGQueryRequest, authorization: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    # Use the unified RAG pipeline to both retrieve and answer
    user_id = os.getenv("RAG_USER_ID", "local")
    filters: Dict[str, Any] = {}
    # Avoid over-filtering by source; rely on user_id/bot_id/meeting_link
    if req.meeting_link:
        filters["meeting_link"] = req.meeting_link
    if req.bot_id:
        filters["bot_id"] = req.bot_id
    out = answer_question_with_rag(
        question=req.question or "",
        user_id=user_id,
        filters=filters,
        top_k=req.top_k or 6,
        min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.65")),
    )
    # Normalize response shape for frontend
    return {"answer": out.get("answer") or "", "contexts": out.get("contexts") or []}


@app.post("/rag/ingest-gmail")
async def rag_ingest_gmail(req: RAGIngestGmailRequest, authorization: Optional[str] = Header(default=None), x_provider_token: Optional[str] = Header(default=None)):
    _ok_auth(authorization)
    return {"ok": True, "ingested_messages": 0, "ingested_chunks": 0}


async def _start_bot_process(bot_id: str, meet_link: str, chat_on_join: str = "") -> None:
    if not BOT_PY_PATH.exists():
        await APP_STATE.set_state(bot_id, "error")
        return

    # Bot process launching; mark as joining. Bot will flip to running when in-call
    await APP_STATE.set_state(bot_id, "joining")

    py = str(BOT_VENV_PY) if BOT_VENV_PY.exists() else sys.executable
    env = os.environ.copy()
    env["MEET_LINK"] = meet_link
    env["BOT_ID"] = bot_id
    env["BACKEND_URL"] = f"http://localhost:{PORT}/captions"
    env["API_BASE_URL"] = f"http://localhost:{PORT}"
    if chat_on_join:
        env["CHAT_ON_JOIN"] = chat_on_join
    # Optional: run Playwright headless (set on the API server env): HEADLESS=1
    if os.getenv("HEADLESS") is not None:
        env["HEADLESS"] = os.getenv("HEADLESS", "")
    # Ensure consistent Unicode handling on Windows.
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    # Ensure bot logs flush promptly to file.
    env.setdefault("PYTHONUNBUFFERED", "1")

    log_path = DATA_DIR / f"{bot_id}.log"
    print(f"[bot-backend] launching bot_id={bot_id} using {py} -> {BOT_PY_PATH}")
    print(f"[bot-backend] bot log: {log_path}")

    # Run the Playwright bot as a separate process (keeps API server responsive).
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"\n--- launch {datetime.now().isoformat()} bot_id={bot_id} meet_link={meet_link} ---\n")
            f.flush()
            proc = subprocess.Popen(
                [py, "-u", str(BOT_PY_PATH)],
                env=env,
                stdout=f,
                stderr=f,
            )
            BOT_PROCESSES[bot_id] = proc
            # Watch for process exit and finalize the meeting
            asyncio.create_task(_watch_bot_exit(bot_id))
    except Exception:
        await APP_STATE.set_state(bot_id, "error")
        return


async def _watch_bot_exit(bot_id: str) -> None:
    """Background watcher: when the bot process exits, mark meeting as ended.

    This provides a reliable finalize trigger even if end-of-meeting webhooks are missing.
    """
    proc = BOT_PROCESSES.get(bot_id)
    if proc is None:
        return
    try:
        # Poll until the process exits
        while True:
            code = proc.poll()
            if code is not None:
                break
            await asyncio.sleep(1.0)
    except Exception:
        pass
    # Process ended; mark state and finalize
    try:
        await APP_STATE.set_state(bot_id, "ended")
    except Exception:
        pass


# Entrypoint for local dev:
#   python -m bot.backend.app.main (from repo root) isn't used; run via uvicorn:
#   uvicorn bot.backend.app.main:app --reload --port 8010

# Optional: allow direct execution for convenience
if __name__ == "__main__":
    try:
        import uvicorn  # type: ignore
    except Exception:
        print("[bot-backend] uvicorn not installed. Install with: pip install uvicorn[standard]")
        raise
    # Run the FastAPI app directly. For auto-reload, prefer the uvicorn CLI.
    uvicorn.run(app, host="127.0.0.1", port=PORT, reload=False)


@app.post("/bots/{bot_id}/state")
async def set_bot_state(bot_id: str, payload: Dict[str, Any], authorization: Optional[str] = Header(default=None)):
    """Allow the bot process to update its state: scheduled|joining|running|ended|error.

    This helps the frontend reflect progress more accurately.
    """
    _ok_auth(authorization)
    state = (payload.get("state") or "").strip()
    allowed = {"scheduled", "joining", "running", "ended", "error"}
    if state not in allowed:
        raise HTTPException(status_code=400, detail={"error": "invalid state", "allowed": sorted(list(allowed))})
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    await APP_STATE.set_state(bot_id, state)
    return {"ok": True, "bot_id": bot_id, "state": state}


@app.post("/bots/{bot_id}/finalize")
async def finalize_bot(bot_id: str, authorization: Optional[str] = Header(default=None)):
    """Manually trigger finalize for a bot: build transcript, generate summary, persist, and notify.

    Useful for debugging if the automatic end-state webhook or monitor didn’t fire.
    """
    _ok_auth(authorization)
    s = await APP_STATE.get_bot(bot_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "bot not found"})
    # Reuse set_state which will run finalize on "ended"
    await APP_STATE.set_state(bot_id, "ended")
    return {"ok": True, "bot_id": bot_id, "finalized": True}

async def _auto_reply_if_question(bot_id: str, speaker: str, text: str):
    q = (text or "").strip()
    if not q:
        return

    lower = q.lower()

    # Prevent bot replying to itself
    if speaker.lower() in ["ai assistant", "bot", "assistant"]:
        return

    # Only respond if someone calls the bot
    if "assistant" not in lower:
        return

    if "?" not in lower:
        return

    question = lower.split("assistant", 1)[1].strip()


    try:
        # Get meeting info
        session = await APP_STATE.get_bot(bot_id)
        if not session:
            return

        filters = {
            "meeting_link": session.meet_link,
            "bot_id": bot_id,
        }

        # 🔹 Directly call RAG core function
        out = answer_question_with_rag(
            question=question,
            user_id=os.getenv("RAG_USER_ID", "local"),
            filters=filters,
            top_k=6,
            min_similarity=float(os.getenv("RAG_MIN_SIMILARITY", "0.65")),
        )

        answer = (out.get("answer") or "").strip()

        if answer:
            reply_text = answer[:400]  # prevent flooding
            await _enqueue_chat(bot_id, reply_text, source="rag_auto_reply")

    except Exception as e:
        print("Auto RAG reply failed:", e)

