# Draft — Meeting Summarizer

## Setup

### 1. Create and activate the virtual environment

```bash
python3 -m venv venv
```

Activate it:

```bash
# macOS / Linux
source venv/bin/activate

# Windows (PowerShell)
venv\Scripts\Activate.ps1
```

You'll know it's active when your shell prompt is prefixed with `(venv)`.

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Start a Postgres database

The app stores all data (accounts, sessions, quota history) in Postgres — there's no local-file fallback. For local dev, the quickest option is a disposable container:

```bash
docker run -d --name draft-app-db -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=draft_app -p 5432:5432 postgres:16-alpine
```

### 4. Configure environment variables

Create a `.env` file in the project root:

```bash
SECRET_KEY=your-secret-key
ADMIN_EMAILS=you@example.com
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/draft_app
```

Optional, if using Firebase:

```bash
FIREBASE_SERVICE_ACCOUNT=path/to/firebase-credentials.json
FIREBASE_PROJECT_ID=your-firebase-project-id
```

### 5. Run the app

```bash
python app.py
```

The server starts at `http://localhost:5000`.

### Deactivating

When you're done:

```bash
deactivate
```

## Deploying (Vercel — Container preset)

This repo has a [Dockerfile](Dockerfile), so Vercel will detect it and offer **Container** as the application preset — use that, with root directory `./`.

Set these environment variables in the Vercel project settings:

| Variable | Required | Notes |
| --- | --- | --- |
| `SECRET_KEY` | Yes | Flask session signing key. Don't use the dev default in production. |
| `ADMIN_EMAILS` | Yes | Comma-separated list of admin emails. |
| `FIREBASE_PROJECT_ID` | Yes (for login) | Your Firebase project id. |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes (for login) | The full contents of your Firebase service account JSON, pasted as-is. Used instead of a file path since `firebase-credentials.json` is gitignored and never reaches the built image. |
| `DATABASE_URL` | Yes | Connection string for a hosted Postgres database (e.g. Vercel Postgres/Neon, Supabase). `POSTGRES_URL` is also accepted, since that's the name some Postgres integrations set automatically. |
| `PORT` | No | Vercel injects this; the app binds to it automatically. |

**Why Postgres, not SQLite:** Vercel's container filesystem is ephemeral and Vercel can route requests to different instances of the same deployment, each with its own independent disk. A local SQLite file previously caused accounts to randomly "disappear" mid-session — a signup or login on one instance wasn't visible to the next request if it landed on another. A shared, hosted Postgres database fixes this since every instance reads and writes the same data.
