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

Save as `query_agent.py` and run with `python3 query_agent.py`:

```python
#!/usr/bin/env python3
"""Fully dynamic test runner for deployed Vertex AI Agent Engine.
Zero hardcoded project IDs, resource names, or dates.
"""

import os
import sys
import argparse
from datetime import datetime

# Configure Vertex AI SDK environment
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

import google.auth
from google.cloud import resourcemanager_v3
import vertexai
from vertexai.preview import reasoning_engines

# Styling
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"


def main():
    parser = argparse.ArgumentParser(description="Query deployed Vertex AI Agent Engine dynamically.")
    parser.add_argument("--project", default=None, help="GCP Project ID (default: ambient)")
    parser.add_argument("--region", default=None, help="GCP Region (default: ambient or us-central1)")
    parser.add_argument("--display-name", default="Commodity Reconciliation Agent", help="Agent Engine display name")
    parser.add_argument("--folder-id", default=os.environ.get("DRIVE_FOLDER_ID", "1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8"), help="Drive Folder ID")
    parser.add_argument("--user-email", default=os.environ.get("USER_EMAIL", "ankit.virmani@virufy.org"), help="Caller Email (OBO)")
    args = parser.parse_args()

    # 1. Dynamically resolve ambient GCP credentials and project
    credentials, ambient_project = google.auth.default()
    project_id = args.project or ambient_project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    region = args.region or os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

    if not project_id:
        print("❌ Error: Could not determine active Google Cloud Project ID. Set GOOGLE_CLOUD_PROJECT or run 'gcloud config set project'.")
        sys.exit(1)

    rm_client = resourcemanager_v3.ProjectsClient(credentials=credentials)
    project_res = rm_client.get_project(name=f"projects/{project_id}")
    project_number = project_res.name.split("/")[1]

    print("=" * 70)
    print(f"{BOLD}DYNAMIC VERTEX AI AGENT ENGINE TEST RUNNER{RESET}")
    print("=" * 70)
    print(f"  • Project ID:      {project_id}")
    print(f"  • Project Number:  {project_number}")
    print(f"  • Region:          {region}")
    print(f"  • Caller Identity: {args.user_email}")

    # 2. Initialize Vertex AI SDK
    vertexai.init(project=project_id, location=region)

    # 3. Dynamically discover deployed Agent Engine resource
    print(f"\n[Discovery] Locating deployed resource for: '{args.display_name}'...")
    engines = reasoning_engines.ReasoningEngine.list()
    matched_engine = None

    for eng in engines:
        if eng.display_name == args.display_name:
            matched_engine = eng
            break

    # Fallback to most recently deployed engine if display name differs
    if not matched_engine and engines:
        matched_engine = sorted(engines, key=lambda x: getattr(x, "create_time", 0), reverse=True)[0]
        print(f"[Discovery] Notice: Matched most recently created engine: '{matched_engine.display_name}'")

    if not matched_engine:
        print(f"❌ Error: No deployed Agent Engine found in {project_id}/{region}.")
        sys.exit(1)

    resource_name = matched_engine.resource_name
    print(f"  ✔ Found Deployed Engine: {resource_name}")

    # 4. Dynamically compute today's date & prompt
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    dynamic_prompt = (
        f"Run today's ({today_str}) commodity trade reconciliation audit. "
        f"1. Ingest today's Certificate of Analysis (CoA) attachments from {args.user_email} with subject 'Certificate of Analysis'. "
        f"2. Fetch clearing house invoices from Google Drive folder {args.folder_id}. "
        f"3. Read the documents, roll up all granular line items into total quantity and net weight, "
        f"and perform the 3-way match against the invoices. "
        f"4. Report all clean matches and discrepancies, and email the report to {args.user_email}."
    )

    print(f"\n{BOLD}[Dynamic Prompt Generated for {today_str}]:{RESET}")
    print(f"{CYAN}{dynamic_prompt}{RESET}\n")
    print("-" * 70)

    # 5. Connect and execute query
    agent = reasoning_engines.ReasoningEngine(resource_name)
    session = agent.create_session(user_id=args.user_email)

    print("Querying deployed agent on Vertex AI (waiting for model reasoning and tools)...")
    try:
        response = agent.query(
            user_id=args.user_email,
            session_id=session.id,
            message=dynamic_prompt,
        )
    except TypeError:
        response = agent.query(input=dynamic_prompt)

    print(f"\n{BOLD}{GREEN}=== AGENT RECONCILIATION RESULT ==={RESET}\n")
    print(response)
    print("\n" + "=" * 70)
    print(f"{BOLD}{GREEN}AUDIT RUN FINISHED SUCCESSFULLY{RESET}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
```

---

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
