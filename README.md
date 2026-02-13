# AI Assistant Bot (Multi-Calendar)

This repo contains a Google Meet caption bot, a FastAPI backend for scheduling and transcript management, and a React (Vite) frontend for the UI.

## Structure

- ai_assistant_bot/ - Python services and bot
  - backend/app/main.py - FastAPI API (primary backend)
  - backend/server.py - Flask captions receiver (optional)
  - backend/bot.py - Playwright Meet bot
- frontend/ - React + Vite client

## Prerequisites

- Python 3.9+
- Node.js 18+
- Playwright browsers (installed via Python)
- Supabase project credentials for the FastAPI backend

## Backend (FastAPI)

```bash
cd ai_assistant_bot
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

set SUPABASE_URL=your_supabase_url
set SUPABASE_SERVICE_ROLE_KEY=your_service_role_key

uvicorn backend.app.main:app --reload --port 8010
```

The API will be available at http://localhost:8010.

## Captions Receiver (Flask, optional)

If you only need a lightweight captions endpoint for the bot:

The captions endpoint will be available at http://localhost:5000/captions
.

## Meet Bot (Playwright)

```bash
cd backend
.venv\Scripts\activate
python -m playwright install

set MEET_LINK=https://meet.google.com/abc-defg-hij
set BACKEND_URL=http://localhost:5000/captions
set HEADLESS=false

python -m backend.bot
```

## Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```

The dev server runs at http://localhost:5173 by default.

## Frontend Environment

Create frontend/.env with:

- VITE_SUPABASE_URL
- VITE_SUPABASE_ANON_KEY
- VITE_BACKEND_URL (e.g. http://localhost:8010)

## Notes

- The FastAPI backend requires Supabase credentials and embedding configuration at startup.
- The bot can post captions to either the Flask receiver or the FastAPI backend if you adapt BACKEND_URL accordingly.
- Uses SentenceTransformers (intfloat/e5-large-v2) for 1024-d embeddings
- Structured summaries are stored in Supabase pgvector
- RAG pipeline retrieves relevant meeting context
- Groq Llama 3.3 70B used for summary and contextual QA


