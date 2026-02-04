# QSDsan Engine MCP - Google Cloud Deployment Guide

This guide walks you through deploying the QSDsan Engine MCP server to Google Cloud Run with Google Cloud Storage for job persistence.

## Prerequisites

1. **Google Cloud Account** with billing enabled
2. **Google Cloud SDK** installed and authenticated
   - Download: https://cloud.google.com/sdk/docs/install
   - Run: `gcloud auth login`
3. **Docker Desktop** installed and running
4. **A GCP Project** (create one at https://console.cloud.google.com)

## Quick Start

### Option 1: Automated Deployment (Windows PowerShell)

```powershell
cd C:\Users\gaierr\Energy_Projects\projects\WasteWater\qsdsan-engine-mcp
.\deploy-gcp.ps1 -ProjectId "your-project-id" -Region "us-central1"
```

### Option 2: Manual Deployment

#### Step 1: Set Up GCP Project

```bash
# Set your project ID
export PROJECT_ID="your-project-id"
export REGION="us-central1"
export BUCKET_NAME="${PROJECT_ID}-qsdsan-jobs"

# Set the active project
gcloud config set project $PROJECT_ID

# Enable required APIs
gcloud services enable run.googleapis.com
gcloud services enable artifactregistry.googleapis.com
gcloud services enable storage.googleapis.com
gcloud services enable cloudbuild.googleapis.com
```

#### Step 2: Create GCS Bucket

```bash
# Create bucket for job storage
gsutil mb -l $REGION gs://$BUCKET_NAME

# Optional: Set lifecycle policy to auto-delete old jobs after 30 days
cat > /tmp/lifecycle.json << 'EOF'
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 30}
      }
    ]
  }
}
EOF
gsutil lifecycle set /tmp/lifecycle.json gs://$BUCKET_NAME
```

#### Step 3: Create Artifact Registry Repository

```bash
# Create Docker repository
gcloud artifacts repositories create qsdsan-repo \
    --repository-format=docker \
    --location=$REGION \
    --description="QSDsan Engine MCP Docker images"

# Configure Docker authentication
gcloud auth configure-docker $REGION-docker.pkg.dev
```

#### Step 4: Build and Push Docker Image

```bash
# Build the image
docker build -t $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest .

# Push to Artifact Registry
docker push $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest
```

#### Step 5: Deploy to Cloud Run

```bash
gcloud run deploy qsdsan-engine-mcp \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest \
    --region $REGION \
    --platform managed \
    --allow-unauthenticated \
    --memory 4Gi \
    --cpu 2 \
    --timeout 3600 \
    --concurrency 10 \
    --min-instances 0 \
    --max-instances 3 \
    --set-env-vars "QSDSAN_ENV=cloud_run,QSDSAN_GCS_BUCKET=$BUCKET_NAME,QSDSAN_MAX_JOBS=3,PYTHONUNBUFFERED=1"
```

#### Step 6: Verify Deployment

```bash
# Get the service URL
SERVICE_URL=$(gcloud run services describe qsdsan-engine-mcp --region $REGION --format "value(status.url)")
echo "Service URL: $SERVICE_URL"

# Test health endpoint
curl $SERVICE_URL/health
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `QSDSAN_ENV` | Set to `cloud_run` for GCS storage | Yes |
| `QSDSAN_GCS_BUCKET` | GCS bucket name for job storage | Yes |
| `QSDSAN_MAX_JOBS` | Maximum concurrent jobs (default: 3) | No |
| `QSDSAN_DEBUG` | Enable debug logging (`true`/`false`) | No |
| `PYTHONUNBUFFERED` | Set to `1` for real-time logging | Recommended |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Google Cloud Run                         │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              QSDsan Engine MCP Container                 │ │
│  │  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │ │
│  │  │ HTTP Health │    │  MCP Tools  │    │  CLI Engine │  │ │
│  │  │   Server    │    │  (35 tools) │    │ (subprocess)│  │ │
│  │  └─────────────┘    └─────────────┘    └─────────────┘  │ │
│  └─────────────────────────────────────────────────────────┘ │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                  Google Cloud Storage                        │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  gs://your-project-qsdsan-jobs/                         │ │
│  │  ├── {job_id}/                                          │ │
│  │  │   ├── job.json                                       │ │
│  │  │   ├── simulation_results.json                        │ │
│  │  │   ├── flowsheet.svg                                  │ │
│  │  │   └── stdout.log                                     │ │
│  │  └── flowsheets/{session_id}/                           │ │
│  │      └── session.json                                   │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

## Accessing Results

### Via HTTP Endpoints

```bash
# Health check
curl $SERVICE_URL/health

# Readiness check (shows tool count)
curl $SERVICE_URL/ready
```

### Via GCS (Signed URLs)

When running in cloud mode, the `get_artifact` MCP tool returns signed URLs that provide temporary access to artifacts:

```json
{
  "job_id": "abc123",
  "artifact_type": "diagram",
  "url": "https://storage.googleapis.com/your-bucket/abc123/flowsheet.svg?X-Goog-Algorithm=..."
}
```

### Direct GCS Access

```bash
# List jobs
gsutil ls gs://$BUCKET_NAME/

# Download a result
gsutil cp gs://$BUCKET_NAME/{job_id}/simulation_results.json .

# View logs
gsutil cat gs://$BUCKET_NAME/{job_id}/stdout.log
```

## Cost Considerations

- **Cloud Run**: Pay per request, scales to zero when idle
- **GCS Storage**: ~$0.02/GB/month for Standard storage
- **Artifact Registry**: ~$0.10/GB/month for Docker images

For a typical development workload:
- 100 simulations/month
- Average 1MB per job
- Estimated cost: < $5/month

## Troubleshooting

### View Logs

```bash
gcloud logs read --service=qsdsan-engine-mcp --region=$REGION --limit=50
```

### Check Service Status

```bash
gcloud run services describe qsdsan-engine-mcp --region=$REGION
```

### Common Issues

1. **"Permission denied" on GCS**
   - Ensure the Cloud Run service account has `storage.objectAdmin` role on the bucket

2. **Container exits immediately**
   - Check that `QSDSAN_ENV=cloud_run` is set (enables HTTP server mode)

3. **Timeout errors**
   - Increase `--timeout` in the deploy command (max 3600s)
   - For long simulations, consider using background jobs

## Security Notes

- The service is deployed with `--allow-unauthenticated` for easy testing
- For production, remove this flag and configure IAM authentication
- GCS signed URLs expire after 60 minutes by default
- Consider enabling VPC connector for private networking

## Updating the Deployment

```bash
# Rebuild and push new image
docker build -t $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest .
docker push $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest

# Deploy new revision
gcloud run deploy qsdsan-engine-mcp \
    --image $REGION-docker.pkg.dev/$PROJECT_ID/qsdsan-repo/qsdsan-engine-mcp:latest \
    --region $REGION
```

## Cleanup

To remove all resources:

```bash
# Delete Cloud Run service
gcloud run services delete qsdsan-engine-mcp --region=$REGION

# Delete GCS bucket (and all contents)
gsutil rm -r gs://$BUCKET_NAME

# Delete Artifact Registry repository
gcloud artifacts repositories delete qsdsan-repo --location=$REGION
```
