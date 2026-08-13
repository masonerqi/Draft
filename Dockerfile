FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching — only re-runs if
# requirements.txt changes, not on every code edit)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

EXPOSE 5000

CMD gunicorn --preload --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 120 "app:create_app()"
