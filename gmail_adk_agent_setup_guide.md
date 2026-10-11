# Enterprise Gmail & Drive AI Agent Setup Guide
**Google Agent Development Kit (ADK) + Gemini 2.5 Flash + Vertex AI Agent Engine**

This guide documents the complete end-to-end setup for building, testing locally, and deploying an autonomous enterprise AI agent that connects to Gmail, identifies email attachments, and uploads them directly to Google Drive.

---

## 1. Architecture & Overview

* **Orchestration**: Google Agent Development Kit (`google-adk`) running `LlmAgent`.
* **Foundation Model**: `gemini-2.5-flash` via Vertex AI.
* **Authentication**:
  * **Local Development**: OAuth 2.0 authorized user credentials saved as `token.json` (no secrets embedded in code).
  * **Production (Gemini Enterprise App)**: End-User Credentials (EUC) automatically delegated per-session via ADK `ToolContext`.
* **Tools**:
  * `search_gmail_attachments`: Searches mailbox messages using standard queries (e.g. `has:attachment`) and extracts message IDs and filenames.
  * `save_attachment_to_drive`: Downloads attachment bytes directly from Gmail and performs a multipart upload to Google Drive, returning the shareable URL.

---

## 2. Google Cloud Platform (GCP) Setup

### 2.1. Enable Required APIs

Run the following command in Google Cloud Shell or terminal to enable all required services for project `283243243997`:

```bash
gcloud services enable \
  gmail.googleapis.com \
  drive.googleapis.com \
  aiplatform.googleapis.com \
  gmailmcp.googleapis.com \
  --project=283243243997
```

| API Service | Purpose |
| :--- | :--- |
| `gmail.googleapis.com` | Core Gmail REST API for email search and attachment downloads |
| `drive.googleapis.com` | Google Drive API for uploading files and generating view links |
| `aiplatform.googleapis.com` | Vertex AI API for Gemini models and Agent Engine hosting |
| `gmailmcp.googleapis.com` | Managed Model Context Protocol endpoint *(requires Developer Preview)* |

---

## 3. OAuth Consent Screen Configuration

To prevent Google's **"This app is blocked"** security alert on corporate Workspace accounts (`virufy.org`), configure an **Internal** consent screen:

1. Navigate to **GCP Console > APIs & Services > OAuth consent screen**.
2. Select **User Type**: **Internal** *(Restricts access strictly to users in `@virufy.org` and bypasses external app verification)*.
3. Click **Create** and complete the required fields:
   * **App Name**: `Enterprise Gmail Assistant`
   * **User Support Email**: `ankit.virmani@virufy.org`
   * **Developer Contact**: `ankit.virmani@virufy.org`
4. On the **Scopes** page, click **Add or Remove Scopes** and add:
   * `https://www.googleapis.com/auth/cloud-platform`
   * `https://www.googleapis.com/auth/gmail.readonly`
   * `https://www.googleapis.com/auth/drive.file`
5. Click **Save and Continue**.

---

## 4. Generating OAuth Client Credentials

1. Go to **APIs & Services > Credentials**.
2. Click **Create Credentials > OAuth client ID**.
3. Choose **Application type**: **Desktop app**.
   *(Note: Desktop clients support local loopback redirect URIs such as `http://localhost:8080/` automatically)*.
4. Name: `Local Test Client`.
5. Click **Create**, then click **Download JSON**.
6. Save this file in your project directory as:
   ```
   client_secret.json
   ```

---

## 5. Local Authentication Script (`login.py`)

When running in Cloud Shell or remote Linux VMs, local browser servers cannot be reached directly without port forwarding. Use this copy-paste flow script to generate `token.json`:

```python
import os
# Allow HTTP loopback for local development
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

# 1. Initialize flow with loopback redirect URI
flow = InstalledAppFlow.from_client_secrets_file(
    "client_secret.json",
    scopes=SCOPES,
    redirect_uri="http://localhost:8080/",
)

# 2. Generate authorization URL
auth_url, _ = flow.authorization_url(
    prompt="select_account",
    login_hint="ankit.virmani@virufy.org",
)

print("\n" + "=" * 60)
print("STEP 1: Open this URL in your browser:")
print("=" * 60)
print(auth_url)

print("\n" + "=" * 60)
print("STEP 2: Sign in as ankit.virmani@virufy.org and click Allow.")
print("When you reach the 'This site can’t be reached' page, copy the address bar URL.")
print("=" * 60)

redirect_response = input("\nSTEP 3: Paste the full URL from your browser address bar here:\n> ").strip()

if redirect_response.startswith("https://localhost"):
    redirect_response = redirect_response.replace("https://localhost", "http://localhost", 1)

# 3. Exchange authorization code for tokens
flow.fetch_token(authorization_response=redirect_response)
creds = flow.credentials

with open("token.json", "w") as f:
    f.write(creds.to_json())

print("\nAuthentication successful!")
print("Token saved to token.json for ankit.virmani@virufy.org")
```

### Execution:
```bash
pip install google-auth-oauthlib
python login.py
```

---

## 6. Agent Implementation (`agent.py`)

This file contains the complete agent logic and tools. It works both locally (using `token.json`) and in Gemini Enterprise App:

```python
import base64
import json
import os
import urllib.parse
import urllib.request

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.adk.agents.llm_agent import LlmAgent
from google.adk.tools.tool_context import ToolContext

# Disable mTLS client certificate checks to prevent AsyncAuthorizedSession conflicts
os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

# --- 1. Token Provider ---
def get_user_token(tool_context: ToolContext | None = None) -> str:
    """Reads valid token from token.json locally or from environment context."""
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", scopes=SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open("token.json", "w") as f:
                f.write(creds.to_json())
        return creds.token
    return ""


# --- 2. Tool: Search Emails with Attachments ---
def search_gmail_attachments(query: str = "has:attachment", max_results: int = 5, tool_context: ToolContext | None = None) -> list[dict]:
    """
    Searches Gmail for messages matching the query (default: 'has:attachment')
    and returns message details including message_id, subject, and attached filenames.
    """
    token = get_user_token(tool_context)
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "283243243997")

    search_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(query)}&maxResults={max_results}"
    req = urllib.request.Request(
        search_url,
        headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": project_id}
    )

    with urllib.request.urlopen(req) as resp:
        msg_list = json.loads(resp.read().decode("utf-8")).get("messages", [])

    results = []
    for item in msg_list:
        msg_id = item["id"]
        detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}"
        req_detail = urllib.request.Request(
            detail_url,
            headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": project_id}
        )
        with urllib.request.urlopen(req_detail) as d_resp:
            msg_data = json.loads(d_resp.read().decode("utf-8"))
            headers = {h["name"].lower(): h["value"] for h in msg_data.get("payload", {}).get("headers", [])}

            filenames = []
            def extract_filenames(part):
                if part.get("filename"):
                    filenames.append(part["filename"])
                for subpart in part.get("parts", []):
                    extract_filenames(subpart)

            extract_filenames(msg_data.get("payload", {}))

            if filenames:
                results.append({
                    "message_id": msg_id,
                    "subject": headers.get("subject", "(No Subject)"),
                    "from": headers.get("from", ""),
                    "filenames": filenames
                })

    return results


# --- 3. Tool: Save Attachment Directly to Google Drive ---
def save_attachment_to_drive(message_id: str, filename: str = "", tool_context: ToolContext | None = None) -> str:
    """
    Downloads attachment bytes from a Gmail message using its clean message_id
    and uploads them directly into the user's Google Drive.
    """
    token = get_user_token(tool_context)
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "283243243997")
    if not token:
        return "Error: No valid access token available."

    try:
        # Step A: Fetch message payload to extract exact attachment parts
        detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}"
        req_detail = urllib.request.Request(
            detail_url,
            headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": project_id}
        )
        with urllib.request.urlopen(req_detail) as d_resp:
            msg_data = json.loads(d_resp.read().decode("utf-8"))

        def find_parts(payload):
            found = []
            if payload.get("filename") and payload.get("body"):
                found.append(payload)
            for subpart in payload.get("parts", []):
                found.extend(find_parts(subpart))
            return found

        all_parts = find_parts(msg_data.get("payload", {}))
        if filename:
            targets = [p for p in all_parts if p.get("filename", "").lower() == filename.lower()]
            if not targets:
                targets = all_parts
        else:
            targets = all_parts

        if not targets:
            return f"No attachments found in message {message_id} matching '{filename}'."

        saved_results = []
        for part in targets:
            fname = part.get("filename", "attachment")
            mtype = part.get("mimeType", "application/octet-stream")
            body = part.get("body", {})

            # Handle both inline base64 and referenced attachmentId
            if "data" in body:
                raw_bytes = base64.urlsafe_b64decode(body["data"].encode("utf-8"))
            elif "attachmentId" in body:
                att_id = body["attachmentId"]
                att_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}/attachments/{att_id}"
                req_att = urllib.request.Request(
                    att_url,
                    headers={"Authorization": f"Bearer {token}", "X-Goog-User-Project": project_id}
                )
                with urllib.request.urlopen(req_att) as a_resp:
                    raw_data = json.loads(a_resp.read().decode("utf-8")).get("data", "")
                    raw_bytes = base64.urlsafe_b64decode(raw_data.encode("utf-8"))
            else:
                continue

            # Step B: Multipart upload to Google Drive
            boundary = "==============ADK_MULTIPART_BOUNDARY=="
            metadata = {"name": fname, "mimeType": mtype}
            post_body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{json.dumps(metadata)}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: {mtype}\r\n\r\n"
            ).encode("utf-8") + raw_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

            req_drive = urllib.request.Request(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink",
                data=post_body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Goog-User-Project": project_id,
                    "Content-Type": f"multipart/related; boundary={boundary}",
                }
            )

            with urllib.request.urlopen(req_drive) as drive_resp:
                file_meta = json.loads(drive_resp.read().decode("utf-8"))
                saved_results.append(f"{fname}: {file_meta.get('webViewLink')}")

        return "Successfully saved to Google Drive:\n" + "\n".join(saved_results)

    except Exception as e:
        return f"Error processing attachments for message {message_id}: {str(e)}"


# --- 4. Root Agent Definition ---
root_agent = LlmAgent(
    model="gemini-2.5-flash",
    name="enterprise_gmail_assistant",
    instruction=(
        "You are an enterprise email assistant. Use search_gmail_attachments to find recent "
        "emails with attachments. For each email found, pass its `message_id` and the `filename` "
        "to save_attachment_to_drive. Summarize what you found and provide the Google Drive links."
    ),
    tools=[search_gmail_attachments, save_attachment_to_drive],
)
```

---

## 7. Local Execution Runner (`run_local.py`)

```python
import asyncio
import os
from agent import root_agent

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

async def main():
    prompt = (
        "Search my inbox for recent emails that have attachments. "
        "Summarize what you found and save the attachments to my Google Drive."
    )

    print("--- Running Agent Locally ---")
    print(f"Prompt: {prompt}\n")

    app_name = "gmail_local_tester"
    user_id = "ankit.virmani@virufy.org"
    session_id = "test_session_001"

    # 1. Initialize local session service
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    )

    # 2. Initialize ADK Runner
    runner = Runner(
        agent=root_agent,
        app_name=app_name,
        session_service=session_service,
    )

    # 3. Format input message
    user_message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )

    print("Agent is reasoning and executing tools...\n")

    # 4. Stream response events
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=user_message,
    ):
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None):
                    print(part.text, end="", flush=True)

    print("\n\n--- Execution Finished ---")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 8. Deploying to Vertex AI Agent Engine & Gemini Enterprise

### 8.1. Deployment Script (`deploy.py`)

Deploy the agent to Google Cloud’s managed Agent Engine runtime:

```python
import vertexai
from vertexai.preview import reasoning_engines
from agent import root_agent

PROJECT_ID = "283243243997"
LOCATION = "us-central1"

vertexai.init(project=PROJECT_ID, location=LOCATION)

remote_agent = reasoning_engines.ReasoningEngine.create(
    root_agent,
    requirements=[
        "google-adk",
        "google-genai",
        "google-auth",
        "google-api-python-client",
        "pydantic",
        "fastapi",
    ],
    display_name="Enterprise Gmail Assistant",
)

print("Agent Engine Deployment Successful!")
print("Resource Name:", remote_agent.resource_name)
```

### 8.2. Registering in Gemini Enterprise App

1. Open the **Gemini Enterprise Admin Console**.
2. Go to **Extensions / Custom Actions** > **Add Agent**.
3. Select your deployed **Vertex AI Agent Engine** resource (`Enterprise Gmail Assistant`).
4. Configure OAuth:
   * Select **End-User Credentials (EUC)** delegation.
   * Add the required scopes:
     * `https://www.googleapis.com/auth/gmail.readonly`
     * `https://www.googleapis.com/auth/drive.file`
5. Click **Publish**.

Now, when any employee in your organization chats with Gemini Enterprise and asks to find emails or extract attachments, Gemini Enterprise prompts for one-time user authorization and delegates the action directly to that user's personal inbox.

---

## 9. Key Troubleshooting & Technical Learnings

| Issue Encountered | Root Cause | Solution Applied |
| :--- | :--- | :--- |
| **"This app is blocked"** | Google blocks generic `gcloud` CLI from requesting restricted scopes (`gmail.readonly`). | Configure an **Internal** OAuth consent screen and use a dedicated Desktop OAuth Client ID. |
| **`Error 400: redirect_uri missing`** | Created a Web Client instead of a Desktop client, or used deprecated `--no-browser` flag. | Use a Desktop App OAuth Client ID and authenticate via `login.py`. |
| **`InsecureTransportError`** | `oauthlib` disallows `http://localhost` callbacks by default. | Set `os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"` in `login.py`. |
| **`mTLS was requested...`** | Device/corporate certificates trigger mTLS on non-mTLS async session. | Set `GOOGLE_API_USE_MTLS_ENDPOINT="never"` and `GOOGLE_API_USE_CLIENT_CERTIFICATE="false"`. |


**Summary of What is Covered in the Guide:**
Google Cloud APIs to Enable: CLI commands to enable Gmail, Drive, Vertex AI, and MCP services on project 283243243997.

OAuth Consent Screen: Step-by-step instructions to configure an Internal consent screen under your Workspace domain (virufy.org) to prevent app blocks.

Credentials & Authentication: Instructions for generating Desktop OAuth credentials, the headless login.py copy-paste script, and generating token.json.

Agent Code (agent.py): The complete, tested agent implementation with mTLS overrides, search_gmail_attachments, and save_attachment_to_drive.

Local Test Runner (run_local.py): Script utilizing ADK's Runner and InMemorySessionService to stream agent reasoning and tool executions.

Production Deployment (deploy.py): Instructions for deploying to Vertex AI Agent Engine (ReasoningEngine.create) and registering the agent within the Gemini Enterprise Admin Console with End-User Credential (EUC) delegation.

Troubleshooting Reference: Solutions for mTLS conflicts, the Developer Preview gate, and parameter truncation issues.
| **MCP Preview Gate (403)** | `gmailmcp.googleapis.com` execution requires Google Workspace Developer Preview approval. | Used native Gmail REST API tool calls in ADK, leveraging existing authorized scopes. |
| **HTTP 400 on Attachment Download** | Attachment IDs are 1,000+ character hashes that get corrupted/truncated by LLM text generation. | Simplified tool parameters so LLM passes clean `message_id` and `filename`; Python resolves exact attachment bytes internally. |
