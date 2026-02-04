# GCP VM Multi-Service Deployment Guide

**Purpose:** Deploy multiple MCP/API services on a shared GCP VM with common infrastructure.

**Last Updated:** February 2026

---

## Overview

This guide explains how to deploy additional services (like EnergyPlus MCP) alongside existing services (QSDsan Engine) on the same GCP VM, sharing common components like Gotenberg (PDF conversion).

### Current VM Setup (qsdsan-vm)

| Service | Port | Container Name | Image |
|---------|------|----------------|-------|
| QSDsan Engine | 8080 | qsdsan-engine-mcp | gcr.io/lotsawatts/qsdsan-engine-mcp:3.0.9 |
| Gotenberg (PDF) | 3000 | gotenberg | gotenberg/gotenberg:8 |
| n8n | 5678 | n8n | n8nio/n8n:latest |

### Target Setup (with EnergyPlus)

| Service | Port | Container Name | Image |
|---------|------|----------------|-------|
| QSDsan Engine | 8080 | qsdsan-engine-mcp | gcr.io/lotsawatts/qsdsan-engine-mcp:3.0.9 |
| **EnergyPlus MCP** | **8081** | **energyplus-mcp** | **gcr.io/lotsawatts/energyplus-mcp:latest** |
| Gotenberg (PDF) | 3000 | gotenberg | gotenberg/gotenberg:8 (shared) |
| n8n | 5678 | n8n | n8nio/n8n:latest |

---

## Step 1: Create Dockerfile for EnergyPlus MCP

Create a `Dockerfile` in your EnergyPlus project:

```dockerfile
# EnergyPlus MCP - Docker Image
#
# Build: docker build -t energyplus-mcp .
# Run: docker run -p 8081:8081 energyplus-mcp

FROM python:3.11-slim

# Install system dependencies
# Add any EnergyPlus-specific dependencies here
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create jobs directory for local storage
RUN mkdir -p /app/jobs

# Environment configuration
ENV PYTHONUNBUFFERED=1
ENV ENERGYPLUS_JOBS_DIR=/app/jobs

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "print('OK')" || exit 1

# Expose port (use different port than QSDsan)
EXPOSE 8081

# Run the server (adjust command as needed)
CMD ["python", "server.py"]
```

---

## Step 2: Create .dockerignore

Create a `.dockerignore` file to reduce image size:

```
# Git
.git/
.gitignore

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
.venv/
env/
.env.local

# IDE
.vscode/
.idea/
*.swp
*.swo

# Tests
tests/
pytest_cache/
.coverage
htmlcov/

# Documentation (optional - include if needed in container)
docs/
*.md
!README.md

# Build artifacts
dist/
build/
*.egg-info/

# Local development
.claude/
jobs/
*.log
```

---

## Step 3: Create .env.example

Create an `.env.example` for environment configuration:

```bash
# EnergyPlus MCP - Environment Configuration
#
# Copy this file to .env and customize for your environment.

# =============================================================================
# Environment Detection
# =============================================================================
# Override auto-detection. Options: local_dev, local_docker, cloud_run
# ENERGYPLUS_ENV=local_dev

# =============================================================================
# Storage Configuration
# =============================================================================
# Local jobs directory
ENERGYPLUS_JOBS_DIR=./jobs

# =============================================================================
# Server Configuration
# =============================================================================
# Server port (must be different from other services)
ENERGYPLUS_PORT=8081

# Maximum concurrent background jobs
ENERGYPLUS_MAX_JOBS=3

# Enable debug logging
ENERGYPLUS_DEBUG=false

# =============================================================================
# AI Analysis Configuration (Optional)
# =============================================================================
# OpenAI API key for AI-powered analysis
# Note: Can share the same key as QSDsan
# OPENAI_API_KEY=sk-your-api-key-here

# Default model for analysis
# OPENAI_MODEL=gpt-4o
```

---

## Step 4: Create docker-compose.vm.yaml

Create a `docker-compose.vm.yaml` for VM deployment:

```yaml
# Docker Compose for GCP VM deployment
# Shares Gotenberg with other services on the VM
# Run with: docker compose -f docker-compose.vm.yaml up -d

services:
  energyplus-mcp:
    image: gcr.io/lotsawatts/energyplus-mcp:latest
    container_name: energyplus-mcp
    ports:
      - "8081:8081"
    environment:
      - ENERGYPLUS_ENV=LOCAL_DOCKER
      - OPENAI_API_KEY=${OPENAI_API_KEY:-}
    volumes:
      - energyplus_jobs:/app/jobs
    networks:
      - shared-network
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s
    restart: unless-stopped

volumes:
  energyplus_jobs:

networks:
  shared-network:
    external: true
    name: qsdsan-network  # Use existing network from QSDsan deployment
```

---

## Step 5: Build and Push Docker Image

### On your development machine:

```powershell
# Navigate to your EnergyPlus project
cd C:\Users\gaierr\Energy_Projects\projects\EnergyPlus\energyplus-mcp

# Build the Docker image
docker build -t gcr.io/lotsawatts/energyplus-mcp:latest .

# Authenticate with GCR (if needed)
gcloud auth configure-docker

# Push to Google Container Registry
docker push gcr.io/lotsawatts/energyplus-mcp:latest
```

---

## Step 6: Deploy to GCP VM

### 6.1 SSH to VM

```bash
gcloud compute ssh qsdsan-vm --zone=us-central1-a
```

### 6.2 Authenticate Docker with GCR

```bash
# Get access token and login
# Run this from your local machine first:
gcloud auth print-access-token

# Then on the VM:
echo "YOUR_ACCESS_TOKEN" | docker login -u oauth2accesstoken --password-stdin gcr.io
```

### 6.3 Create/Update .env file

```bash
# On the VM, edit .env to add shared configuration
nano /home/gaierr/.env

# Add or verify these lines:
# OPENAI_API_KEY=sk-your-key-here  (shared with QSDsan)
```

### 6.4 Pull and Run the Container

**Option A: Using docker run (simple, standalone)**

```bash
# Pull the image
docker pull gcr.io/lotsawatts/energyplus-mcp:latest

# Run the container
source /home/gaierr/.env
docker run -d \
  --name energyplus-mcp \
  --restart unless-stopped \
  --network qsdsan-network \
  -p 8081:8081 \
  -e ENERGYPLUS_ENV=LOCAL_DOCKER \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  gcr.io/lotsawatts/energyplus-mcp:latest
```

**Option B: Using docker-compose (if installed)**

```bash
# Copy docker-compose.vm.yaml to the VM first
# Then run:
cd /home/gaierr
docker compose -f docker-compose.energyplus.yaml up -d
```

### 6.5 Verify Deployment

```bash
# Check container is running
docker ps

# Check health endpoint
curl http://localhost:8081/health

# Check from external (use VM's external IP)
curl http://34.28.104.162:8081/health
```

---

## Step 7: Configure Firewall (if needed)

If the new port (8081) isn't accessible externally, add a firewall rule:

```bash
gcloud compute firewall-rules create allow-energyplus-8081 \
    --direction=INGRESS \
    --priority=1000 \
    --network=default \
    --action=ALLOW \
    --rules=tcp:8081 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=http-server
```

---

## Shared Services

### Gotenberg (PDF Conversion)

Both QSDsan and EnergyPlus can use the same Gotenberg instance:

- **Internal URL (within Docker network):** `http://gotenberg:3000`
- **External URL:** `http://34.28.104.162:3000`

To use Gotenberg in your EnergyPlus code:

```python
import httpx

async def convert_html_to_pdf(html_content: str) -> bytes:
    """Convert HTML to PDF using shared Gotenberg service."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://gotenberg:3000/forms/chromium/convert/html",
            files={"files": ("index.html", html_content, "text/html")},
            data={"marginTop": "0.5", "marginBottom": "0.5"},
        )
        return response.content
```

### OpenAI API Key

Both services can share the same `OPENAI_API_KEY` environment variable. Just ensure it's in the `.env` file on the VM.

---

## Quick Reference

### Service Endpoints on VM (34.28.104.162)

| Service | Health Check | API Docs |
|---------|--------------|----------|
| QSDsan Engine | `curl http://34.28.104.162:8080/health` | `http://34.28.104.162:8080/docs` |
| EnergyPlus MCP | `curl http://34.28.104.162:8081/health` | `http://34.28.104.162:8081/docs` |
| Gotenberg | `curl http://34.28.104.162:3000/health` | N/A |
| n8n | N/A | `http://34.28.104.162:5678` |

### Container Management

```bash
# SSH to VM
gcloud compute ssh qsdsan-vm --zone=us-central1-a

# List all containers
docker ps -a

# View logs
docker logs energyplus-mcp --tail 100

# Restart container
docker restart energyplus-mcp

# Stop container
docker stop energyplus-mcp

# Remove container
docker rm energyplus-mcp

# Update to new image
docker stop energyplus-mcp && docker rm energyplus-mcp
docker pull gcr.io/lotsawatts/energyplus-mcp:latest
source /home/gaierr/.env
docker run -d --name energyplus-mcp ... (full command from Step 6.4)
```

### Deployment Checklist

- [ ] Create Dockerfile in EnergyPlus project
- [ ] Create .dockerignore
- [ ] Create .env.example
- [ ] Create docker-compose.vm.yaml (optional)
- [ ] Build Docker image locally
- [ ] Push image to GCR
- [ ] SSH to VM
- [ ] Authenticate Docker with GCR
- [ ] Update .env file if needed
- [ ] Pull and run container
- [ ] Verify health endpoint
- [ ] Configure firewall if needed
- [ ] Test from n8n workflow

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `unauthorized` when pulling image | Run `docker login` with fresh access token |
| Port already in use | Check with `docker ps`, stop conflicting container |
| Container exits immediately | Check logs with `docker logs energyplus-mcp` |
| Can't connect externally | Check firewall rules, ensure port is exposed |
| Network not found | Create network: `docker network create qsdsan-network` |
