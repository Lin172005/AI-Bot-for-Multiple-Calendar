from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

from supabase import create_client, Client

try:
    from sentence_transformers import SentenceTransformer  # type: ignore
except Exception:
    SentenceTransformer = None  # type: ignore


@dataclass
class SummaryUnit:
    text: str
    type: str  # decision | action_item | open_question | other
    meeting_id: Optional[str] = None
    meeting_title: Optional[str] = None
    date: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


def parse_structured_summary(summary: str, title: str = "") -> List[SummaryUnit]:
    """Parse a structured summary into granular units with robust fallbacks.

    Primary: detect sections (Overview, Key Discussion Points/Key Points, Decisions Made, Action Items, Open Questions)
    and collect bullet lines under each.

    Fallbacks when headings/bullets are missing:
    - Split into sentences; classify with heuristics:
      * open_question: contains '?'
      * decision: contains keywords like decided/agreed/approved/committed/finalized
      * action_item: starts with imperative verbs like assign/create/send/prepare/follow up/schedule/update/implement/fix/investigate/review/write/document/deploy/test
      * other: anything else
    """
    import re

    text = (summary or "").strip()
    if not text:
        return []

    lines = [l.rstrip() for l in text.splitlines()]
    sec: Optional[str] = None
    decisions: List[str] = []
    actions: List[str] = []
    questions: List[str] = []
    other: List[str] = []
    overview_lines: List[str] = []

    def _is_bullet(l: str) -> bool:
        return bool(re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", l))

    def _set_section(l: str) -> bool:
        low = l.strip().lower().rstrip(':')
        if low.startswith("overview"):
            nonlocal sec; sec = "overview"; return True
        if low.startswith("key discussion points") or low.startswith("key points"):
            sec = "key_points"; return True
        if low.startswith("decisions") or low.startswith("decisions made"):
            sec = "decisions"; return True
        if low.startswith("action items"):
            sec = "actions"; return True
        if low.startswith("open questions"):
            sec = "open_questions"; return True
        return False

    # Pass 1: collect bullets under detected sections
    for l in lines:
        if not l.strip():
            continue
        if _set_section(l):
            continue
        if sec == "overview":
            if l.strip():
                overview_lines.append(l.strip())
            continue
        if _is_bullet(l):
            content = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", l).strip()
            if sec == "decisions":
                decisions.append(content)
            elif sec == "actions":
                actions.append(content)
            elif sec in ("key_points", "open_questions") and ("?" in content):
                questions.append(content)
            else:
                # If no active section, classify heuristically
                low = content.lower()
                if "?" in content:
                    questions.append(content)
                elif re.search(r"\b(decided|agreed|approved|committed|finalized|resolved)\b", low):
                    decisions.append(content)
                elif re.match(r"^(assign|create|send|prepare|follow up|schedule|update|implement|fix|investigate|review|write|document|deploy|test)\b", low):
                    actions.append(content)
                else:
                    other.append(content)
        else:
            # Non-bullet lines: keep questions, otherwise ignore here
            if (sec in ("key_points", "open_questions") or sec is None) and ("?" in l):
                questions.append(l.strip())
    
    units: List[SummaryUnit] = []
    if overview_lines:
        overview_text = " ".join(overview_lines).strip()
        units.append(SummaryUnit(text=overview_text, type="overview"))
    units += [SummaryUnit(text=d, type="decision") for d in decisions]
    units += [SummaryUnit(text=a, type="action_item") for a in actions]
    units += [SummaryUnit(text=q, type="open_question") for q in questions]
    units += [SummaryUnit(text=o, type="other") for o in other]

    if units:
        return units

    # Fallback: split into sentences and classify
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    for p in parts:
        low = p.lower()
        if "?" in p:
            units.append(SummaryUnit(text=p, type="open_question"))
        elif re.search(r"\b(decided|agreed|approved|committed|finalized|resolved)\b", low):
            units.append(SummaryUnit(text=p, type="decision"))
        elif re.match(r"^(assign|create|send|prepare|follow up|schedule|update|implement|fix|investigate|review|write|document|deploy|test)\b", low):
            units.append(SummaryUnit(text=p, type="action_item"))
        else:
            units.append(SummaryUnit(text=p, type="other"))
    return units


class LocalEmbedder:
    """Local sentence-transformers embedder (production-friendly).

    Default: all-MiniLM-L6-v2 (384 dims). Override via RAG_LOCAL_EMBED_MODEL.
    """
    def __init__(self, model_name: Optional[str] = None):
        # Prefer explicit model via env; otherwise allow dimension-driven defaults
        # If you use pgvector(1024), a good default is 'intfloat/e5-large-v2' (1024 dims)
        # If you use pgvector(384), default 'all-MiniLM-L6-v2' (384 dims)
        target_dim_env = os.getenv("RAG_VECTOR_DIM") or os.getenv("SUPABASE_VECTOR_DIM")
        default_model = None
        if target_dim_env:
            try:
                td = int(target_dim_env)
                if td == 1024:
                    default_model = "intfloat/e5-large-v2"
                elif td == 768:
                    default_model = "all-mpnet-base-v2"
                elif td == 384:
                    default_model = "all-MiniLM-L6-v2"
            except Exception:
                pass
        name = model_name or os.getenv("RAG_LOCAL_EMBED_MODEL", default_model or "all-MiniLM-L6-v2")
        if SentenceTransformer is None:
            raise RuntimeError("sentence-transformers not installed. pip install sentence-transformers")
        self.model = SentenceTransformer(name)

    def embed(self, texts: List[str]) -> List[List[float]]:
        vecs = self.model.encode(texts, batch_size=32, convert_to_numpy=True, normalize_embeddings=True)
        return [v.tolist() for v in vecs]


class RAGSupabase:
    def __init__(self):
        url = (os.getenv("SUPABASE_URL") or "").strip()
        key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not key:
            raise RuntimeError("Supabase config missing: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        self.sb: Client = create_client(url, key)

    def insert_units(self, user_id: str, units: List[SummaryUnit]) -> int:
        inserted = 0
        for u in units:
            emb = u.metadata.get("embedding") if u.metadata else None
            if not emb:
                continue
            bot_id = (u.metadata or {}).get("bot_id")
            meeting_link = (u.metadata or {}).get("meeting_link")
            metadata = dict(u.metadata or {})
            payload = {
                "p_bot_id": bot_id,
                "p_embedding": emb,
                "p_external_id": u.meeting_id,
                "p_meeting_link": meeting_link if meeting_link else None,
                "p_metadata": metadata,
                "p_source": "summary",
                "p_text": u.text,
                "p_title": u.meeting_title,
                "p_user_id": user_id,
            }
            try:
                self.sb.rpc("insert_rag_doc", payload).execute()
            except Exception as e:
                # Auto-recover if dimension mismatch (e.g., expected 1024, not 384)
                msg = str(e)
                import re as _re
                m = _re.search(r"expected\s+(\d+)\s+dimensions", msg)
                if m:
                    try:
                        target = int(m.group(1))
                        vec = list(emb)
                        if len(vec) < target:
                            vec = vec + [0.0] * (target - len(vec))
                        elif len(vec) > target:
                            vec = vec[:target]
                        payload["p_embedding"] = vec
                        self.sb.rpc("insert_rag_doc", payload).execute()
                    except Exception:
                        raise
                else:
                    raise
            inserted += 1
        return inserted

    def match(self, user_id: str, query_embedding: List[float], top_k: int, min_similarity: float, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        flt = {"user_id": (user_id or "local").strip()}
        if filters:
            try:
                flt.update(filters)
            except Exception:
                pass
        # Call RPC using the correct signature: (flt, match_count, query_embedding)
        res = self.sb.rpc(
            "match_rag_docs",
            {"flt": flt, "match_count": int(top_k), "query_embedding": query_embedding},
        ).execute()
        rows = res.data or []
        return rows


def ingest_summary_units_for_bot(*, bot_session: Any) -> Dict[str, Any]:
    """Split bot summary into units, embed locally, store in Supabase pgvector.

    Returns details including number of inserted units.
    """
    # Determine summary text: prefer existing summary_text; else regenerate via Groq/simple
    title = getattr(bot_session, "title", "")
    summary = (getattr(bot_session, "summary_text", "") or "").strip()
    if not summary:
        # Build from utterances and run same summarization path
        try:
            from .services.state import AppState
            text = AppState._build_transcript_text(list(getattr(bot_session, "utterances", [])))  # type: ignore[attr-defined]
            summary = AppState._groq_summarize(text, title=title) or AppState._simple_summarize(text)  # type: ignore[attr-defined]
        except Exception:
            summary = summary or ""
    # Fallback: load persisted summary from disk by bot_id
    if not summary:
        try:
            bid = getattr(bot_session, "bot_id", None)
            if bid:
                base_dir = Path(__file__).resolve().parents[1] / "data" / "summaries"
                p = base_dir / f"{bid}.txt"
                if p.exists():
                    summary = (p.read_text(encoding="utf-8") or "").strip()
        except Exception:
            pass

    units = parse_structured_summary(summary, title=title)
    if not units:
        return {"ok": False, "reason": "no-units"}

    # Embed locally
    embedder = LocalEmbedder()
    embeds = embedder.embed([u.text for u in units])
    # Optionally enforce target vector dimension via env (pads/truncates if needed)
    target_dim_env = os.getenv("RAG_VECTOR_DIM") or os.getenv("SUPABASE_VECTOR_DIM")
    if target_dim_env:
        try:
            td = int(target_dim_env)
            fixed: List[List[float]] = []
            for v in embeds:
                if len(v) == td:
                    fixed.append(v)
                elif len(v) < td:
                    # pad with zeros
                    fixed.append(v + [0.0] * (td - len(v)))
                else:
                    # truncate to target
                    fixed.append(v[:td])
            embeds = fixed
        except Exception:
            # If parsing fails, continue with original dims (may error downstream)
            pass

    # Attach metadata and meeting context
    user_id = (getattr(bot_session, "user_id", "") or os.getenv("RAG_USER_ID") or "local").strip() or "local"
    meeting_id = getattr(bot_session, "event_id", None)
    meeting_title = getattr(bot_session, "title", None)
    date_iso = getattr(bot_session, "start_time", None)
    for u, e in zip(units, embeds):
        u.meeting_id = meeting_id
        u.meeting_title = meeting_title
        u.date = date_iso
        ml = (
            getattr(bot_session, "meeting_link", None)
            or getattr(bot_session, "meet_link", None)
            or getattr(bot_session, "meeting_url", None)
        )
        u.metadata = {
            "bot_id": getattr(bot_session, "bot_id", None),
            "embedding": e,
            "meeting_link": ml,
            "type": u.type,
            "date": u.date,
            "meeting_title": meeting_title,
        }

    # Store in Supabase
    backend = (os.getenv("RAG_BACKEND") or "supabase").lower()
    if backend != "supabase":
        return {"ok": False, "reason": "backend-not-supabase", "hint": "Set RAG_BACKEND=supabase to use pgvector in Supabase."}

    supa = RAGSupabase()
    inserted = supa.insert_units(user_id=user_id, units=units)
    return {"ok": True, "inserted": inserted, "units": len(units)}


def answer_question_with_rag(*, question: str, user_id: str, filters: Optional[Dict[str, Any]] = None, top_k: int = 6, min_similarity: float = 0.65) -> Dict[str, Any]:
    """Embed the question, retrieve contexts from Supabase, and answer via Groq.

    Applies a similarity threshold; if no contexts pass, returns a fallback message.
    """
    embedder = LocalEmbedder()
    q_emb = embedder.embed([question])[0]
    supa = RAGSupabase()
    rows = supa.match(user_id=user_id, query_embedding=q_emb, top_k=top_k, min_similarity=min_similarity, filters=filters)

    # Fallback: try without additional filters if no match
    if not rows:
        rows = supa.match(user_id=user_id, query_embedding=q_emb, top_k=top_k, min_similarity=min_similarity, filters=None)

    contexts: List[str] = []
    if not rows:
        # If LLM is configured, still make the call and ask it to state that context is insufficient.
        try:
            from groq import Groq  # type: ignore
        except Exception:
            Groq = None  # type: ignore
        api_key = (os.getenv("GROQ_API_KEY") or "").strip()
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
        if not api_key or Groq is None:
            return {"ok": False, "answer": "I do not have sufficient context from previous meetings.", "contexts": []}
        client = Groq(api_key=api_key)
        system = "You are a helpful assistant. If no prior meeting context is available, say so explicitly."
        user_msg = f"Question: {question}\n\nNo prior meeting context is available. Respond by stating that context is insufficient."
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
                temperature=0.2,
                max_tokens=200,
            )
            content = (resp.choices[0].message.content or "").strip()
        except Exception:
            content = "I do not have sufficient context from previous meetings."
        return {"ok": False, "answer": content, "contexts": []}
    # Build context string
    for r in rows:
        t = (r.get("text") or "").strip()
        md = r.get("metadata") or {}
        typ = (md.get("type") or "").strip()
        ttl = (md.get("meeting_title") or "").strip()
        ctx = f"[{typ or 'unit'}] {ttl}: {t}" if ttl else f"[{typ or 'unit'}] {t}"
        contexts.append(ctx)

    # Call Groq with contexts
    try:
        from groq import Groq  # type: ignore
    except Exception:
        Groq = None  # type: ignore
    api_key = (os.getenv("GROQ_API_KEY") or "").strip()
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    if not api_key or Groq is None:
        return {"ok": True, "answer": "Context found, but LLM is not configured.", "contexts": contexts}

    client = Groq(api_key=api_key)
    system = "You are a helpful assistant. Answer the user's question using ONLY the provided prior meeting context. If the context is insufficient, say so explicitly."
    ctx_block = "\n\n".join(contexts)
    user_msg = f"Question: {question}\n\nPrior meeting context:\n{ctx_block}\n\nAnswer concisely and cite relevant points from the context."
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_msg}],
        temperature=0.2,
        max_tokens=800,
    )
    content = (resp.choices[0].message.content or "").strip()
    return {"ok": True, "answer": content, "contexts": contexts}
