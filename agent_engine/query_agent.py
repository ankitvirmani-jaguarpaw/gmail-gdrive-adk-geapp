#!/usr/bin/env python3
"""Fully dynamic test runner for deployed Vertex AI Agent Engine.

Zero hardcoded project IDs, resource names, or dates.
"""

import argparse
from datetime import datetime
import os
import sys

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
    parser = argparse.ArgumentParser(
        description="Query deployed Vertex AI Agent Engine dynamically."
    )
    parser.add_argument(
        "--project", default=None, help="GCP Project ID (default: ambient)"
    )
    parser.add_argument(
        "--region",
        default=None,
        help="GCP Region (default: ambient or us-central1)",
    )
    parser.add_argument(
        "--display-name",
        default="Commodity Reconciliation Agent",
        help="Agent Engine display name",
    )
    parser.add_argument(
        "--folder-id",
        default=os.environ.get(
            "DRIVE_FOLDER_ID", "1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8"
        ),
        help="Drive Folder ID",
    )
    parser.add_argument(
        "--user-email",
        default=os.environ.get("USER_EMAIL", "ankit.virmani@virufy.org"),
        help="Caller Email (OBO)",
    )
    args = parser.parse_args()

    # 1. Dynamically resolve ambient GCP credentials and project
    credentials, ambient_project = google.auth.default()
    project_id = (
        args.project or ambient_project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    )
    region = args.region or os.environ.get(
        "GOOGLE_CLOUD_LOCATION", "us-central1"
    )

    if not project_id:
        print(
            "❌ Error: Could not determine active Google Cloud Project ID. Set"
            " GOOGLE_CLOUD_PROJECT or run 'gcloud config set project'."
        )
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
    print(
        f"\n[Discovery] Locating deployed resource for: '{args.display_name}'..."
    )
    engines = reasoning_engines.ReasoningEngine.list()
    matched_engine = None

    for eng in engines:
        if eng.display_name == args.display_name:
            matched_engine = eng
            break

    # Fallback to most recently deployed engine if display name differs
    if not matched_engine and engines:
        matched_engine = sorted(
            engines, key=lambda x: getattr(x, "create_time", 0), reverse=True
        )[0]
        print(
            "[Discovery] Notice: Matched most recently created engine:"
            f" '{matched_engine.display_name}'"
        )

    if not matched_engine:
        print(f"❌ Error: No deployed Agent Engine found in {project_id}/{region}.")
        sys.exit(1)

    resource_name = matched_engine.resource_name
    print(f"  ✔ Found Deployed Engine: {resource_name}")

    # 4. Dynamically compute today's date & prompt
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    dynamic_prompt = (
        f"Run today's ({today_str}) commodity trade reconciliation audit. 1."
        f" Ingest today's Certificate of Analysis (CoA) attachments from"
        f" {args.user_email} with subject 'Certificate of Analysis'. 2. Fetch"
        f" clearing house invoices from Google Drive folder {args.folder_id}. 3."
        " Read the documents, roll up all granular line items into total"
        " quantity and net weight, and perform the 3-way match against the"
        f" invoices. 4. Report all clean matches and discrepancies, and email"
        f" the report to {args.user_email}."
    )

    print(f"\n{BOLD}[Dynamic Prompt Generated for {today_str}]:{RESET}")
    print(f"{CYAN}{dynamic_prompt}{RESET}\n")
    print("-" * 70)

    # 5. Connect and execute query
    agent = reasoning_engines.ReasoningEngine(resource_name)
    session = agent.create_session(user_id=args.user_email)

    print(
        "Querying deployed agent on Vertex AI (waiting for model reasoning and"
        " tools)..."
    )
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
