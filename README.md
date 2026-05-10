# TruthLens AI Fake News Detection System

A production-style Flask SaaS application for AI-powered fake news detection, live news browsing, PDF verification reports, featured admin articles, user history, feedback, and an advanced admin panel.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:5000`.

Default admin credentials:

- Email: `admin@truthlens.ai`
- Password: `Admin@12345`

Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before first run to override the seeded admin.

## API Keys

The app loads environment settings automatically from `.env`. Edit that file, paste your API keys, then restart Flask.

The app runs without valid API keys using a local heuristic analysis fallback and demo live-news data.

For AI analysis:

```powershell
$env:AI_PROVIDER="openai"
$env:OPENAI_API_KEY="sk-..."
```

or with OpenRouter:

```powershell
$env:AI_PROVIDER="openrouter"
$env:OPENROUTER_API_KEY="sk-or-v1-..."
$env:OPENROUTER_MODEL="openai/gpt-4o-mini"
```

or:

```powershell
$env:AI_PROVIDER="gemini"
$env:GEMINI_API_KEY="..."
```

For live news:

No key is required by default. The Live News page uses GDELT DOC API article lists and automatically falls back to Google News RSS if GDELT rate-limits:

```powershell
$env:GDELT_TIMESPAN="48h"
$env:GOOGLE_NEWS_TIMESPAN="2d"
```

Optional keyed providers are still supported:

```powershell
$env:NEWS_API_KEY="..."
```

or:

```powershell
$env:GNEWS_API_KEY="..."
```

## Included Features

- Secure registration, login, logout, sessions, password hashing, forgot-password token flow
- AI news verification dashboard with confidence visualization and animated result card
- Professional PDF report generation via ReportLab
- Live news feed with category filtering, search, pagination, refresh
- Admin-published featured articles with image upload
- User verification history with report downloads and deletion
- Contact/feedback database storage
- Admin analytics, users, password resets, blocks, removals, logs, feedback, featured news management
- SQLite database with a clean repository-style data access layer
- Premium responsive glassmorphism UI with GSAP, AOS, Chart.js, Bootstrap, and animated particles
