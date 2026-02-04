# QSDsan Engine MCP - Google Cloud Deployment Script
#
# Prerequisites:
#   1. Google Cloud SDK (gcloud) installed and authenticated
#   2. Docker Desktop running
#   3. A Google Cloud project with billing enabled
#
# Usage:
#   .\deploy-gcp.ps1 -ProjectId "your-project-id" -Region "us-central1"

param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectId,

    [Parameter(Mandatory=$false)]
    [string]$Region = "us-central1",

    [Parameter(Mandatory=$false)]
    [string]$ServiceName = "qsdsan-engine-mcp",

    [Parameter(Mandatory=$false)]
    [string]$BucketName = ""
)

# Set default bucket name if not provided
if ([string]::IsNullOrEmpty($BucketName)) {
    $BucketName = "$ProjectId-qsdsan-jobs"
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "QSDsan Engine MCP - GCP Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Project ID:    $ProjectId"
Write-Host "  Region:        $Region"
Write-Host "  Service Name:  $ServiceName"
Write-Host "  GCS Bucket:    $BucketName"
Write-Host ""

# Step 1: Set the project
Write-Host "[1/6] Setting GCP project..." -ForegroundColor Green
gcloud config set project $ProjectId
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to set project. Make sure you're authenticated with 'gcloud auth login'" -ForegroundColor Red
    exit 1
}

# Step 2: Enable required APIs
Write-Host "[2/6] Enabling required APIs..." -ForegroundColor Green
$apis = @(
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com"
)
foreach ($api in $apis) {
    Write-Host "  Enabling $api..."
    gcloud services enable $api --quiet
}

# Step 3: Create GCS bucket for job storage
Write-Host "[3/6] Creating GCS bucket for job storage..." -ForegroundColor Green
$bucketExists = gsutil ls -b "gs://$BucketName" 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Bucket gs://$BucketName already exists" -ForegroundColor Yellow
} else {
    gsutil mb -l $Region "gs://$BucketName"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Failed to create bucket" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Created bucket gs://$BucketName" -ForegroundColor Green
}

# Set lifecycle policy to auto-delete old jobs (optional - 30 days)
Write-Host "  Setting lifecycle policy (delete after 30 days)..."
$lifecycleJson = @"
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
"@
$lifecycleFile = [System.IO.Path]::GetTempFileName()
$lifecycleJson | Out-File -FilePath $lifecycleFile -Encoding utf8
gsutil lifecycle set $lifecycleFile "gs://$BucketName"
Remove-Item $lifecycleFile

# Step 4: Create Artifact Registry repository
Write-Host "[4/6] Creating Artifact Registry repository..." -ForegroundColor Green
$repoName = "qsdsan-repo"
$repoExists = gcloud artifacts repositories describe $repoName --location=$Region 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  Repository $repoName already exists" -ForegroundColor Yellow
} else {
    gcloud artifacts repositories create $repoName `
        --repository-format=docker `
        --location=$Region `
        --description="QSDsan Engine MCP Docker images"
    Write-Host "  Created repository $repoName" -ForegroundColor Green
}

# Configure Docker to use Artifact Registry
Write-Host "  Configuring Docker authentication..."
gcloud auth configure-docker "$Region-docker.pkg.dev" --quiet

# Step 5: Build and push Docker image
Write-Host "[5/6] Building and pushing Docker image..." -ForegroundColor Green
$imageName = "$Region-docker.pkg.dev/$ProjectId/$repoName/$ServiceName"
$imageTag = "$imageName`:latest"

Write-Host "  Building image: $imageTag"
docker build -t $imageTag .
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker build failed" -ForegroundColor Red
    exit 1
}

Write-Host "  Pushing image to Artifact Registry..."
docker push $imageTag
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker push failed" -ForegroundColor Red
    exit 1
}

# Step 6: Deploy to Cloud Run
Write-Host "[6/6] Deploying to Cloud Run..." -ForegroundColor Green
gcloud run deploy $ServiceName `
    --image $imageTag `
    --region $Region `
    --platform managed `
    --allow-unauthenticated `
    --memory 4Gi `
    --cpu 2 `
    --timeout 3600 `
    --concurrency 10 `
    --min-instances 0 `
    --max-instances 3 `
    --set-env-vars "QSDSAN_ENV=cloud_run,QSDSAN_GCS_BUCKET=$BucketName,QSDSAN_MAX_JOBS=3,PYTHONUNBUFFERED=1"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Cloud Run deployment failed" -ForegroundColor Red
    exit 1
}

# Get the service URL
$serviceUrl = gcloud run services describe $ServiceName --region $Region --format "value(status.url)"

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Service URL: $serviceUrl" -ForegroundColor Yellow
Write-Host ""
Write-Host "Test the health endpoint:" -ForegroundColor Yellow
Write-Host "  curl $serviceUrl/health"
Write-Host ""
Write-Host "GCS Bucket for job storage:" -ForegroundColor Yellow
Write-Host "  gs://$BucketName"
Write-Host ""
Write-Host "View logs:" -ForegroundColor Yellow
Write-Host "  gcloud logs read --service=$ServiceName --region=$Region"
Write-Host ""
