#!/usr/bin/env python3
"""Fully dynamic test runner for deployed Vertex AI Agent Engine.

Queries the deployed Commodity Reconciliation Agent on Vertex AI
without sending any emails, directing the agent to publish the light-mode
reconciliation dashboard to a GCS bucket and return its link.

Uses the gRPC StreamQueryReasoningEngine API to parse streaming events cleanly.
"""

import argparse
from datetime import datetime
import json
import os
import sys

# Configure environment for Google Cloud / Vertex AI on Cloudtop
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

try:
    import google.auth
    from google.cloud import aiplatform
    from vertexai.preview import reasoning_engines
except ImportError as e:
    print(f"Missing required dependencies: {e}")
    print("Run inside the virtual environment with google-cloud-aiplatform installed.")
    sys.exit(1)

BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"

def main():
    parser = argparse.ArgumentParser(
        description="Query the deployed Commodity Reconciliation Agent on Vertex AI (GCS Dashboard Publication Mode)."
    )
    parser.add_argument(
        "--folder-id",
        default="1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8",
        help="Google Drive Folder ID containing clearing house settlement invoices",
    )
    parser.add_argument(
        "--user-email",
        default="ankit.virmani@virufy.org",
        help="User email identity for supplier CoA attachments and session context",
    )
    parser.add_argument(
        "--bucket",
        default=os.environ.get("RECON_DASHBOARD_BUCKET", "cme-recon-dashboards-white-stacker-405921"),
        help="Target GCS bucket to host the reconciliation dashboard HTML",
    )
    parser.add_argument(
        "--project-id",
        default="agents-demo-495412",
        help="GCP Project ID where Agent Engine is deployed (default: agents-demo-495412)",
    )
    parser.add_argument(
        "--region",
        default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        help="GCP Region where Agent Engine is deployed (default: us-central1)",
    )
    parser.add_argument(
        "--resource-name",
        default=None,
        help="Exact Reasoning Engine resource name (e.g. projects/.../reasoningEngines/...)",
    )
    args = parser.parse_args()

    # 1. Resolve GCP credentials and Project ID
    credentials, detected_project = google.auth.default()
    project_id = args.project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or detected_project
    region = args.region

    if not project_id:
        print(f"{RED}❌ Error: Could not determine active Google Cloud Project ID.{RESET}")
        sys.exit(1)

    print("\n" + "=" * 70)
    print(f"{BOLD}{GREEN}QUERYING DEPLOYED VERTEX AI AGENT ENGINE (GCS DASHBOARD MODE){RESET}")
    print("=" * 70)
    print(f"  • Project ID:        {project_id}")
    print(f"  • Region:            {region}")
    print(f"  • Caller Identity:   {args.user_email}")
    print(f"  • Drive Folder ID:   {args.folder_id}")
    print(f"  • Target GCS Bucket: {args.bucket}")
    print(f"  • Email Dispatch:    DISABLED (Zero emails will be sent)")
    print("-" * 70)

    aiplatform.init(project=project_id, location=region, credentials=credentials)

    # 2. Discover deployed reasoning engine
    if args.resource_name:
        resource_name = args.resource_name
    else:
        print(f"\n[1/3] Discovering deployed Agent Engines in {project_id}/{region}...")
        engines = list(reasoning_engines.ReasoningEngine.list())

        matched_engine = None
        for engine in engines:
            display_name = getattr(engine, "display_name", "")
            if "commodity" in display_name.lower() or "recon" in display_name.lower():
                matched_engine = engine
                break

        if not matched_engine and engines:
            matched_engine = sorted(
                engines, key=lambda x: getattr(x, "create_time", 0), reverse=True
            )[0]
            print(f"  ℹ Selected most recent Reasoning Engine: '{matched_engine.display_name}'")

        if not matched_engine:
            print(f"{RED}❌ Error: No deployed Agent Engine found in {project_id}/{region}.{RESET}")
            sys.exit(1)

        resource_name = matched_engine.resource_name
        print(f"  ✔ Found Deployed Engine: {resource_name}")

    # 3. Construct dynamic prompt (no email request; mandates GCS publishing)
    today_str = datetime.now().astimezone().strftime("%Y-%m-%d")
    dynamic_prompt = (
        f"Run today's ({today_str}) commodity trade reconciliation audit. "
        f"1. Ingest today's Certificate of Analysis (CoA) attachments from {args.user_email} with subject 'Certificate of Analysis'. "
        f"2. Fetch clearing house settlement invoices from Google Drive folder {args.folder_id}. "
        "3. Read the documents, roll up all granular sublots and pallet lines into total quantity and net weight, and perform the 3-way match against the invoices. "
        f"4. Do NOT send any emails. Publish the light-mode executive reconciliation dashboard to GCS bucket '{args.bucket}' using `publish_reconciliation_dashboard_to_gcs`. "
        "5. In your response, return the direct GCS Web Link, the Cloud Console Link, and a concise summary table of clean matches and discrepancies."
    )

    print(f"\n{BOLD}[2/3] Dynamic Prompt Generated for {today_str}:{RESET}")
    print(f"{CYAN}{dynamic_prompt}{RESET}\n")
    print("-" * 70)

    # 4. Connect and execute query against Reasoning Engine via stream_query
    print("[3/3] Querying deployed agent on Vertex AI (waiting for model reasoning and tools)...")

    from google.cloud import aiplatform_v1beta1
    client = aiplatform_v1beta1.ReasoningEngineExecutionServiceClient(
        client_options={"api_endpoint": f"{region}-aiplatform.googleapis.com"}
    )

    stream_req = aiplatform_v1beta1.StreamQueryReasoningEngineRequest(
        name=resource_name,
        input={"message": dynamic_prompt, "user_id": args.user_email},
        class_method="stream_query",
    )

    print(f"\n{BOLD}{GREEN}=== AGENT RECONCILIATION OUTPUT STREAM ==={RESET}\n")
    for chunk in client.stream_query_reasoning_engine(request=stream_req):
        raw_data = getattr(chunk, "data", None)
        if not raw_data:
            continue
        try:
            event = json.loads(raw_data)
            parts = event.get("content", {}).get("parts", [])
            for part in parts:
                if "function_call" in part:
                    fn = part["function_call"]
                    print(f"\n{BOLD}{CYAN}⚙ Tool Call:{RESET} {fn.get('name')}({json.dumps(fn.get('args', {}))})")
                elif "function_response" in part:
                    resp = part["function_response"]
                    print(f"{BOLD}{GREEN}✔ Tool Response [{resp.get('name')}]:{RESET} status={resp.get('response', {}).get('status', 'ok')}")
                elif "text" in part:
                    print(part["text"], end="", flush=True)
        except Exception:
            print(raw_data, end="", flush=True)

    print("\n\n" + "=" * 70)
    print(f"{BOLD}{GREEN}QUERY EXECUTION FINISHED{RESET}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()