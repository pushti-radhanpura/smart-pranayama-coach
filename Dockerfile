FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy full application code
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV HOST=0.0.0.0

EXPOSE 8000

# Start server via root entrypoint
CMD ["python", "main.py"]
