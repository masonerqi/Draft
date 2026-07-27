FROM python:3.11-slim

WORKDIR /app
RUN mkdir -p /app/data

# Install dependencies first (better layer caching — only re-runs if
# requirements.txt changes, not on every code edit)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

VOLUME ["/app/data"]

EXPOSE 5000

CMD ["python", "app.py"]
