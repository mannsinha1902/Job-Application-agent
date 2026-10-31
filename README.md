# CareerPilot — AI Job Application Agent

A local-first Streamlit assistant for discovering public job listings, comparing them with your resume, preparing tailored document drafts, and tracking applications. The app requires explicit per-application approval before marking an application approved. **It does not click Submit or Apply; final submission is always manual.**

## Requirements

- Python 3.10 or later
- Windows is supported; the optional scheduled task uses Windows Task Scheduler.
- OpenAI API key for AI matching and document drafts.
- Public Greenhouse board slugs and/or Lever site names for discovery.
- Playwright Chromium browser for visible form preparation.

## Setup

In PowerShell from the project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\streamlit.exe run app.py
```

Alternatively, run `streamlit run app.py` after activating your virtual environment. Enter an OpenAI key in **Settings** or in the local `.env` file. Add public Greenhouse/Lever board names and locations under **Job Discovery**. Upload your private master resume under **Resume Builder**.

## Features

- **Job discovery:** Queries public, documented Greenhouse and Lever job-board endpoints. Board/site identifiers must be supplied by you; the app does not crawl career pages or bypass authentication, CAPTCHA, or anti-automation controls. Filters include titles, locations, experience level, workplace type, full-time-only, and posted date. Listings are deduplicated in SQLite.
- **AI matching:** Produces a 0–100 explainable fit score, matching and missing qualifications, experience compatibility, language requirements, and a priority. The score is a comparison aid, not a hiring probability.
- **Documents:** Extracts text from PDF/DOCX resumes, generates English or German draft content, and saves editable DOCX documents in the private `data` directory. Review every generated claim: language models can make mistakes despite instructions not to invent facts.
- **Approval and tracking:** Drafts are previewed in the approval queue. Approval is recorded for one application at a time; it does not submit anything. Track status, dates, document versions, recruiter response, and interviews.
- **Browser preparation:** After approval, an optional visible Playwright browser can open the posting, fill a limited set of clearly identified contact fields, and upload the selected files. It does not answer screening questions or activate any Submit/Apply control. Inspect all fields and complete unanswered questions yourself.
- **Daily discovery:** `scripts\install_daily_task.ps1` optionally registers an 8:00 AM Windows Task Scheduler job. It uses saved public feed settings and only discovers/saves listings.

## Privacy and limitations

SQLite data, resumes, generated documents, and temporary browser manifests live under `data\` by default and are excluded from Git. API keys belong in the ignored `.env` for local use, or in **App settings → Secrets** when deployed to Streamlit Community Cloud. Cloud secrets should use TOML syntax such as `OPENAI_API_KEY = "sk-..."`. Keys are not stored in SQLite or logged. Streamlit binds to `127.0.0.1` by default so the local dashboard is local-only. Do not upload sensitive information you do not want sent to OpenAI. AI features send the provided resume text and job description to the OpenAI API when invoked.

The public feeds expose different fields; experience, work arrangement, and employment type can be unknown. The Greenhouse feed used here does not provide a reliable original posting date, so those listings are treated as undated and excluded when a posted-date filter is active. Feed access and browser form compatibility depend on external providers and must be checked manually. Some PDFs (for example scanned image-only documents) may not contain extractable text. Playwright's browser binary is installed separately using the command above.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests use temporary databases and local document fixtures; they do not call external job feeds or OpenAI.
