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

### 3. Configure environment variables

Create a `.env` file in the project root:

```bash
SECRET_KEY=your-secret-key
ADMIN_EMAILS=you@example.com
```

Optional, if using Firebase:

```bash
FIREBASE_SERVICE_ACCOUNT=path/to/firebase-credentials.json
FIREBASE_PROJECT_ID=your-firebase-project-id
```

### 4. Run the app

```bash
python app.py
```

The server starts at `http://localhost:5000`.

### Deactivating

When you're done:

```bash
deactivate
```
