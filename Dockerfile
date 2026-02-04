# QSDsan Engine MCP - Docker Image
#
# Build: docker build -t qsdsan-engine-mcp .
# Run locally: docker run -v $(pwd)/jobs:/app/jobs qsdsan-engine-mcp
# Run for Cloud Run: Set QSDSAN_GCS_BUCKET environment variable

FROM python:3.11-slim

# Install system dependencies
# - graphviz: Required for flowsheet diagram generation
# - gcc/build-essential: Required for some Python packages with C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    graphviz \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
# Copy requirements first for better layer caching
COPY requirements.txt requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt

# Copy application code
COPY . .

# Create jobs directory for local storage fallback
RUN mkdir -p /app/jobs

# Environment configuration
ENV PYTHONUNBUFFERED=1
ENV QSDSAN_JOBS_DIR=/app/jobs

# Health check for Cloud Run
# Cloud Run expects HTTP health checks on port 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "from cloud.config import get_config; print('OK')" || exit 1

# Expose port for HTTP health endpoint (optional, for Cloud Run)
EXPOSE 8080

# Run the MCP server
# Note: FastMCP handles stdio transport, not HTTP
CMD ["python", "server.py"]
