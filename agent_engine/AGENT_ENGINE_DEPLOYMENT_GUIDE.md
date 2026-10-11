# Deploying ADK Commodity Reconciliation Agent to Vertex AI Agent Engine

This guide provides the complete, step-by-step runbook for packaging and deploying the **Commodity Reconciliation Agent** to **Vertex AI Agent Engine** (formerly Reasoning Engine) and registering it in the **Gemini Enterprise (GE) App** with **On-Behalf-Of (OBO)** delegated user identity.

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------------+
| 1. Gemini Enterprise App (Web Interface)                                                |
|    - User prompt from ankit.virmani@virufy.org                                          |
|    - Attaches active OAuth Bearer Token (OBO context)                                   |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
| 2. Vertex AI Agent Engine (Fully Managed Serverless Agent Runtime)                       |
|    - Executes ADK agent logic, multi-turn state & session memory                        |
|    - Calls Vertex AI Gemini 2.5 Flash for reasoning & math roll-ups                      |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼ (OBO Delegated Caller Token)
+-----------------------------------------------------------------------------------------+
| 3. Enterprise Workspace APIs (Gmail & Google Drive)                                     |
|    - Gmail API: Fetches today's Certificate of Analysis (CoA) attachments               |
|    - Drive API: Ingests spot invoices from Drive folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8 |
+-----------------------------------------------------------------------------------------+
```

---

## 1. Directory Structure

Keep your deployment folder lightweight and isolated. No Dockerfile or web server code is needed:

```text
cme-recon/
├── agent.py            # ADK agent definition (contains root_agent = LlmAgent(...))
└── requirements.txt    # Managed container dependencies
```

---

## 2. Dependencies (`requirements.txt`)

Specify the runtime dependencies for Agent Engine:

```text
google-adk>=0.1.0
google-genai>=1.0.0
google-cloud-aiplatform>=1.75.0
pypdf>=5.0.0
```

---

## 3. Deploy via the ADK CLI (Single Command)

Set your target GCP environment variables and deploy directly using the Google ADK CLI:

```bash
# 1. Set Google Cloud project and regional location
export GOOGLE_CLOUD_PROJECT="283243243997"
export GOOGLE_CLOUD_LOCATION="us-central1"
export STAGING_BUCKET="gs://283243243997-agent-engine-staging"

# 2. (Optional) Create staging bucket if it does not exist
gcloud storage buckets create $STAGING_BUCKET \
  --project=$GOOGLE_CLOUD_PROJECT \
  --location=$GOOGLE_CLOUD_LOCATION \
  --uniform-bucket-level-access 2>/dev/null || true

# 3. Deploy package to Vertex AI Agent Engine
adk deploy agent_engine \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_LOCATION \
  --staging_bucket=$STAGING_BUCKET \
  --display_name="Commodity Reconciliation Agent" \
  .
```

### Expected Output
```text
Building agent package...
Uploading package to gs://283243243997-agent-engine-staging/...
Provisioning Vertex AI Agent Engine runtime...
AgentEngine deployed successfully!
Resource name: projects/283243243997/locations/us-central1/reasoningEngines/751619551677906944
```

---

## 4. Grant IAM Permissions to the Agent Engine Service Identity

Vertex AI Agent Engine runs under a managed Google Cloud Service Agent. Ensure it has permissions to call Vertex AI models and access project storage:

```bash
# Resolve Google Cloud Project Number
PROJECT_NUMBER=$(gcloud projects describe $GOOGLE_CLOUD_PROJECT --format="value(projectNumber)")
AGENT_ENGINE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

# Grant Vertex AI User role
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:${AGENT_ENGINE_SA}" \
  --role="roles/aiplatform.user"

# Grant Storage Object Viewer for dependency loading
gcloud projects add-iam-policy-binding $GOOGLE_CLOUD_PROJECT \
  --member="serviceAccount:${AGENT_ENGINE_SA}" \
  --role="roles/storage.objectViewer"
```

---

## 5. Testing Your Deployed Agent

### Option A: Google Cloud Console Playground (No Code)
1. In Google Cloud Console, navigate to: **Vertex AI** $\rightarrow$ **Agent Engine** (or **Reasoning Engines**).
2. Click **`Commodity Reconciliation Agent`**.
3. Select the **Playground** tab to interactively chat, inspect tool invocations, and review execution logs in real time.

### Option B: Programmatic Python Query
Run a test script using the official `vertexai` SDK:

```python
import vertexai

# Initialize Vertex AI
vertexai.init(project="283243243997", location="us-central1")

# Load deployed Agent Engine resource
agent = vertexai.agent_engines.get(
    "projects/283243243997/locations/us-central1/reasoningEngines/751619551677906944"
)

# Create managed session
session = agent.create_session(user_id="ankit.virmani@virufy.org")

# Dispatch prompt
response = agent.query(
    user_id="ankit.virmani@virufy.org",
    session_id=session.id,
    message=(
        "Run today's commodity reconciliation audit. Ingest today's CoAs from "
        "Ankit.virmani@virufy.org and invoices from Drive folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8. "
        "Report all matches and discrepancies."
    ),
)

print(response)
```

---

## 6. Registering in the Gemini Enterprise (GE) App

Link your deployed Agent Engine resource into the Gemini Enterprise chat interface for domain users:

1. **Navigate to App Settings**:
   - Go to **Google Cloud Console** $\rightarrow$ **Gemini Enterprise Agent Platform** (Discovery Engine).
   - Select your enterprise web application.
2. **Add Agent**:
   - Click **Agents** in the left navigation $\rightarrow$ click **`+ Add agent`**.
   - Under **Runtime Engine**, select **Vertex AI Agent Engine**.
   - Choose **`Commodity Reconciliation Agent`** from the dropdown list.
3. **Configure Semantic Routing & Descriptions**:
   - **Display Name**: `Commodity Reconciliation Agent`
   - **Invocation Description**:
     > *"Automates daily 3-way commodity trade reconciliation between dairy spot clearing house invoices (in Google Drive) and supplier Certificates of Analysis (in Gmail). Verifies PO numbers, lot numbers, rolled-up pallet box counts, and rolled-up net weights."*
4. **Enable On-Behalf-Of (OBO) Delegation**:
   - In the **Authentication & Identity** section, select **User Identity Forwarding (OBO)**.
   - Required Scopes:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/drive.readonly`
     - `https://www.googleapis.com/auth/drive.file`
5. **Publish & Access Control**:
   - Assign access to target users/groups (e.g. `ankit.virmani@virufy.org`).
   - Set status to **Active / Enabled** $\rightarrow$ click **Save & Register**.

---

## Verification in Gemini Enterprise

Open the Gemini Enterprise app in your browser and submit your prompt:

```text
@Commodity Reconciliation Agent Reconcile today's spot dairy trades. Pull the invoices from Drive folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8 and compare with the Certificates of Analysis received from ankit.virmani@virufy.org in my email today.
```

The agent runs on Vertex AI Agent Engine, accesses your Gmail and Drive under your delegated identity, and streams the completed reconciliation dashboard directly into your chat window.
