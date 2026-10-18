gcloud run deploy commodity-recon-agent-ge-v2 \
    --source . \
    --region us-central1 \
    --project gateway-everything \
    --set-env-vars GOOGLE_CLOUD_PROJECT=gateway-everything \
    --memory 2Gi \
    --cpu 2 \
    --timeout 300 \
    --allow-unauthenticated
