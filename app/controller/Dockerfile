# ─────────────────────────────────────────────────────────────────────────────
# Dockerfile for the Python Health Monitor Controller
# ─────────────────────────────────────────────────────────────────────────────
# STAGE: slim Python image keeps the container small (~130 MB)
FROM python:3.11-slim

# Set a working directory inside the container
WORKDIR /app

# Copy only requirements first — Docker caches this layer separately.
# If requirements.txt doesn't change, pip install is skipped on rebuilds.
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application source code
COPY . .

# Create the log directory (matches config.LOG_FILE default)
RUN mkdir -p /app/logs

# The container runs the monitor script directly
CMD ["python", "health_monitor.py"]
