from __future__ import annotations

import os
from typing import Optional, Dict, Any, List

try:
    from supabase import create_client, Client
except Exception:  # pragma: no cover
    create_client = None
    Client = object  # type: ignore

_client: Optional[Client] = None


def _get_client() -> Optional[Client]:
    global _client
    if _client is not None:
        return _client
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key or create_client is None:
        # Helpful signal in logs if config is missing
        print("[supabase] client not configured: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        return None
    try:
        _client = create_client(url, key)
        return _client
    except Exception:
        print("[supabase] failed to create client")
        return None


def upsert_meeting(
    *,
    user_id: str,
    event_id: str,
    title: str,
    start_time_iso: str,
    meet_link: str,
    attendee_bot_id: str,
    summary: str,
) -> bool:
    """Upsert a meeting row in public.meetings using Supabase service role.

    Requires env: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY.
    Uses unique index on (user_id, meet_link) when meet_link is present.
    """
    client = _get_client()
    if client is None:
        print("[supabase] upsert skipped: client not available")
        return False
    if not user_id or not event_id:
        # Schema requires user_id and event_id
        print("[supabase] upsert skipped: user_id or event_id missing")
        return False
    payload = {
        "user_id": user_id,
        "event_id": event_id,
        "title": title or None,
        "start_time": (start_time_iso or None),
        "meet_link": (meet_link or None),
        "attendee_bot_id": attendee_bot_id or None,
        "summary": summary or None,
    }
    # Prefer update-first to avoid relying on on_conflict when composite indexes differ
    try:
        q = client.table("meetings").select("id").eq("user_id", user_id)
        if event_id:
            q = q.eq("event_id", event_id)
        elif meet_link:
            q = q.eq("meet_link", meet_link)
        existing = q.limit(1).execute()
        rows = getattr(existing, "data", []) or []
        if rows:
            rid = rows[0].get("id")
            client.table("meetings").update({k: v for k, v in payload.items() if v is not None}).eq("id", rid).execute()
            print(f"[supabase] updated meeting id={rid} user_id={user_id}")
            return True
        else:
            client.table("meetings").insert(payload).execute()
            print(f"[supabase] inserted meeting user_id={user_id} meet_link={meet_link or '<none>'}")
            return True
    except Exception as e:
        print(f"[supabase] write failed: {e}")
        return False


def health() -> Dict[str, Any]:
    """Return diagnostic info about Supabase configuration and access.

    Does not mutate data. Attempts a lightweight select on `meetings`.
    """
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    has_config = bool(url and key)
    info: Dict[str, Any] = {
        "has_config": has_config,
        "url_set": bool(url),
        "key_set": bool(key),
        "client_ok": False,
        "can_select_meetings": False,
        "error": None,
    }
    if not has_config or create_client is None:
        if create_client is None:
            info["error"] = "supabase client not installed"
        return info
    try:
        client = _get_client()
        info["client_ok"] = client is not None
        if client is None:
            return info
        try:
            # Minimal access check: try to select 0 rows from meetings
            client.table("meetings").select("*", count="exact").limit(0).execute()
            info["can_select_meetings"] = True
        except Exception as e:
            info["error"] = f"select failed: {e}"
    except Exception as e:
        info["error"] = str(e)
    return info


def fetch_meetings(user_id: Optional[str] = None, meet_link: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    """Fetch meetings rows for quick debugging.

    Filters by user_id and/or meet_link when provided.
    Returns { ok, rows, error }.
    """
    res: Dict[str, Any] = {"ok": False, "rows": [], "error": None}
    client = _get_client()
    if client is None:
        res["error"] = "client not available"
        return res
    try:
        q = client.table("meetings").select("*")
        if user_id:
            q = q.eq("user_id", user_id)
        if meet_link:
            q = q.eq("meet_link", meet_link)
        data = q.limit(limit).execute()
        rows: List[Dict[str, Any]] = (getattr(data, "data", []) or [])  # supabase-py returns .data
        res["rows"] = rows
        res["ok"] = True
        return res
    except Exception as e:
        res["error"] = str(e)
        return res
