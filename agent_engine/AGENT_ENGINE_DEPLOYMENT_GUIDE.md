# Deploying ADK Commodity Reconciliation Agent to Vertex AI Agent Engine
## Fully Dynamic Production Runbook (Zero Hardcoded Values)

This runbook guides you through deploying the **Commodity Reconciliation Agent** to **Vertex AI Agent Engine** (formerly Reasoning Engine) and binding it to the **Gemini Enterprise (GE) App** with **On-Behalf-Of (OBO)** delegated identity.

All commands and scripts dynamically discover your **active GCP Project**, **Project Number**, **Region**, **Service Account**, and **Deployed Resource IDs** at runtime.

---

## Architecture Overview

```
+-----------------------------------------------------------------------------------------+
| 1. Gemini Enterprise App (Web Interface)                                                |
|    - User prompt from authenticated domain user                                         |
|    - Dynamically forwards caller's active OAuth Bearer Token (OBO context)              |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
| 2. Vertex AI Agent Engine (Fully Managed Serverless Agent Runtime)                       |
|    - Dynamically discovered by Agent Platform via display name                          |
|    - Executes ADK agent logic, multi-turn state & session memory                        |
|    - Calls Vertex AI Gemini models via ambient Project ADC                              |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼ (OBO Delegated Caller Token)
+-----------------------------------------------------------------------------------------+
| 3. Enterprise Workspace APIs (Gmail & Google Drive)                                     |
|    - Gmail API: Fetches today's Certificate of Analysis (CoA) attachments               |
|    - Drive API: Ingests clearing house invoices from user's Drive folder link/ID        |
+-----------------------------------------------------------------------------------------+
```

---

## 1. Directory Structure

Keep your deployment folder lightweight and isolated. No Dockerfile or web server code is needed:

```text
cme-recon/
├── agent.py               # Pure ADK agent definition (contains root_agent = LlmAgent(...))
├── requirements.txt       # Managed container dependencies
└── query_agent.py         # Dynamic CLI test runner (zero hardcoded values)
```

---

## 2. Dependencies (`requirements.txt`)

Specify the runtime dependencies for Agent Engine:

```text
google-adk>=0.1.0
google-genai>=1.0.0
google-cloud-aiplatform>=1.75.0
google-cloud-resourcemanager>=1.12.0
pypdf>=5.0.0
```

---

## 3. Dynamic Deployment via the ADK CLI

Run this script to deploy without hardcoding project numbers, bucket names, or regions:

```bash
# 1. Dynamically resolve GCP environment from active gcloud configuration
# 1. Export standard GCP environment variables
export PROJECT_ID=$(gcloud config get-value project)
export REGION="us-central1"

export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_LOCATION="${REGION}"
export GOOGLE_CLOUD_REGION="${REGION}"

# 2. Deploy without --staging_bucket
adk deploy agent_engine \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --display_name="Commodity Reconciliation Agent" \
  .
```

---

## 4. Dynamic IAM Permissions for Agent Engine Service Identity

Vertex AI Agent Engine runs under a managed Google Cloud Service Agent. Bind the required permissions dynamically using the active project number:

```bash
# 1. Resolve Project Number dynamically
PROJECT_ID=$(gcloud config get-value project)
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
AGENT_ENGINE_SA="service-${PROJECT_NUMBER}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"

echo "Configuring IAM for Service Agent: ${AGENT_ENGINE_SA}"

# 2. Grant Vertex AI User role
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${AGENT_ENGINE_SA}" \
  --role="roles/aiplatform.user"

# 3. Grant Storage Object Viewer for package hydration
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${AGENT_ENGINE_SA}" \
  --role="roles/storage.objectViewer"
```

---

## 5. Fully Dynamic Agent Testing (`query_agent.py`)

This test runner automatically discovers:
1. **Active Project ID & Number** via ambient Google credentials.
2. **Deployed Resource Name** by searching Vertex AI for `"Commodity Reconciliation Agent"`.
3. **Today's Date** in your local timezone.
4. **Target User Email & Drive Folder ID** from environment variables or command-line flags.

Save as `query_agent.py` and run with `python3 query_agent.py`

## 6. Registering in the Gemini Enterprise (GE) App

1. **Open Gemini Enterprise Admin Settings**:
   - Navigate to **Google Cloud Console** $\rightarrow$ **Gemini Enterprise Agent Platform** (Discovery Engine).
   - Select your enterprise web application.
2. **Add Custom Agent**:
   - In the left navigation, click **Agents** $\rightarrow$ click **`+ Add agent`**.
   - Under **Runtime Engine**, select **Vertex AI Agent Engine**.
   - Select **`Commodity Reconciliation Agent`** from the auto-populated list (no need to type resource strings).
3. **Semantic Intent Description**:
   - **Display Name**: `Commodity Reconciliation Agent`
   - **Trigger / Intent Description**:
     > *"Automates daily 3-way commodity trade reconciliation between dairy spot clearing house invoices (in Google Drive) and supplier Certificates of Analysis (in Gmail). Verifies PO numbers, lot numbers, rolled-up pallet box counts, and rolled-up net weights."*
4. **Configure On-Behalf-Of (OBO) Delegation**:
   - Under **Authentication**, enable **User Identity Forwarding (OBO)**.
   - Required Scopes:
     - `https://www.googleapis.com/auth/gmail.readonly`
     - `https://www.googleapis.com/auth/drive.readonly`
     - `https://www.googleapis.com/auth/drive.file`
5. **Publish**:
   - Assign access to target users/groups $\rightarrow$ Set status to **Active / Enabled** $\rightarrow$ Click **Save**.

---

## End-to-End User Invocation in Gemini Enterprise

Any authorized domain user can now invoke the agent directly from the Gemini Enterprise chat window:

```text
@Commodity Reconciliation Agent Reconcile today's spot dairy trades. Pull the invoices from Drive folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8 and compare with the Certificates of Analysis received from ankit.virmani@virufy.org in my email today.
```

The agent runs on Vertex AI Agent Engine, accesses Gmail and Drive under the user's active login via OBO delegation, rolls up the sublot/pallet lines, and streams the finished reconciliation dashboard directly into the chat interface.
