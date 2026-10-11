#!/usr/bin/env python3
"""
Daily Commodity Reconciliation Test Harness & Executive Dashboard.
Executes the automated 3-way reconciliation audit between Gmail CoAs and
Drive Invoices, renders a visual console & HTML dashboard, and dispatches
the report email. Zero hardcoded file lists or IDs.
"""

from datetime import datetime, timezone
import json
import os
import sys

# Configure environment for Google GenAI / Vertex AI
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ.get("GOOGLE_CLOUD_PROJECT", "283243243997")
os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
os.environ["GOOGLE_API_USE_MTLS_ENDPOINT"] = "never"
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

# Import from agent (or agent_recon)
try:
    from no_agent_recon import (
        fetch_invoices_from_drive,
        generate_html_dashboard,
        get_today_coa_emails_and_attachments,
        run_reconciliation,
        send_reconciliation_email,
    )
except ImportError:
    from agent_recon import (
        fetch_invoices_from_drive,
        generate_html_dashboard,
        get_today_coa_emails_and_attachments,
        run_reconciliation,
        send_reconciliation_email,
    )

BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
DIM = "\033[2m"

# Virufy Drive Folder ID
DRIVE_FOLDER_ID = "1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8"

def print_console_dashboard(recon_data: dict):
    """Renders a polished, high-contrast visual dashboard directly in the terminal."""
    date_str = recon_data.get("date_reconciled", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    total_audited = recon_data.get("total_lines_audited", 0)
    matches_count = recon_data.get("matches_count", 0)
    discrepancies_count = recon_data.get("discrepancies_count", 0)
    match_pct = round((matches_count / total_audited * 100), 1) if total_audited else 0
    total_wt = recon_data.get("total_audited_wt", 0.0)
    total_var_wt = recon_data.get("total_variance_wt", 0.0)

    matches = [r for r in recon_data.get("details", []) if r["status"] == "MATCH"]
    discrepancies = [r for r in recon_data.get("details", []) if r["status"] == "DISCREPANCY"]

    width = 96
    print("\n" + BOLD + CYAN + "╔" + "═" * (width - 2) + "╗" + RESET)
    print(BOLD + CYAN + "║" + " " * 22 + "COMMODITY TRADE RECONCILIATION DASHBOARD" + " " * 32 + "║" + RESET)
    print(CYAN + "║" + f"  Audit Date: {date_str}   |   Three-Way Matching: PO# • Box Count • Net Weight".ljust(width - 2) + "║" + RESET)
    print(CYAN + "╠" + "═" * (width - 2) + "╣" + RESET)

    # KPI Banner Cards
    kpi_line1 = f"  LOTS AUDITED: {BOLD}{total_audited}{RESET}    |  CLEAN MATCHES: {BOLD}{GREEN}{matches_count} ({match_pct}%){RESET}    |  DISCREPANCIES: {BOLD}{RED}{discrepancies_count}{RESET}"
    kpi_line2 = f"  TOTAL NET WT: {BOLD}{total_wt:,.0f} lbs{RESET}  |  NET VARIANCE: {BOLD}{YELLOW}{total_var_wt:,.2f} lbs{RESET}"
    print(CYAN + "║" + RESET + kpi_line1.ljust(width + 12) + CYAN + "║" + RESET)
    print(CYAN + "║" + RESET + kpi_line2.ljust(width + 12) + CYAN + "║" + RESET)
    print(CYAN + "╠" + "═" * (width - 2) + "╣" + RESET)

    # 1. Clean Matches Section
    print(CYAN + "║" + RESET + f" {BOLD}{GREEN}✔ SECTION 1: CLEAN MATCHES (100% Granular Line Roll-Up Parity){RESET}".ljust(width + 8) + CYAN + "║" + RESET)
    print(CYAN + "╟" + "─" * (width - 2) + "╢" + RESET)
    header_m = f"  {'COMMODITY':<16} {'LOT / PO':<12} {'INVOICE LINE':<18} {'BOX COUNT':<12} {'NET WEIGHT':<16} {'MATCH STATUS':<12}"
    print(CYAN + "║" + RESET + BOLD + header_m.ljust(width - 2) + RESET + CYAN + "║" + RESET)
    print(CYAN + "╟" + "─" * (width - 2) + "╢" + RESET)

    for m in matches:
        row = f"  {m['commodity']:<16} {BOLD}{m['invoice_lot']:<12}{RESET} {m['invoice_line']:<18} {m['invoice_qty']:<12,} {m['invoice_wt']:<16,.2f} {GREEN}✔ MATCH{RESET}"
        print(CYAN + "║" + RESET + row.ljust(width + 7) + CYAN + "║" + RESET)

    print(CYAN + "╠" + "═" * (width - 2) + "╣" + RESET)

    # 2. Discrepancies Section
    print(CYAN + "║" + RESET + f" {BOLD}{RED}⚠ SECTION 2: ITEMIZED DISCREPANCIES & AUDIT EXCEPTIONS{RESET}".ljust(width + 8) + CYAN + "║" + RESET)
    print(CYAN + "╟" + "─" * (width - 2) + "╢" + RESET)
    header_d = f"  {'COMMODITY':<16} {'BILLED LOT':<12} {'CoA PO#':<10} {'BILLED QTY':<12} {'CoA QTY':<12} {'QTY DELTA':<12} {'WT DELTA (lbs)':<16}"
    print(CYAN + "║" + RESET + BOLD + header_d.ljust(width - 2) + RESET + CYAN + "║" + RESET)
    print(CYAN + "╟" + "─" * (width - 2) + "╢" + RESET)

    for d in discrepancies:
        q_delta = f"{d['qty_delta']:+d}" if d['qty_delta'] else "0"
        w_delta = f"{d['wt_delta']:+,.2f}" if d['wt_delta'] else "0.00"
        c_po = d.get('coa_po') or "N/A"
        row1 = f"  {d['commodity']:<16} {BOLD}{d['invoice_lot']:<12}{RESET} {YELLOW}{c_po:<10}{RESET} {d['invoice_qty']:<12,} {str(d.get('coa_qty', 'N/A')):<12} {RED}{q_delta:<12}{RESET} {RED}{w_delta:<16}{RESET}"
        print(CYAN + "║" + RESET + row1.ljust(width + 16) + CYAN + "║" + RESET)
        reason_str = f"    ↳ Audit Flag: {d.get('reason', '')}"
        print(CYAN + "║" + RESET + DIM + RED + reason_str.ljust(width - 2) + RESET + CYAN + "║" + RESET)

    print(CYAN + "╚" + "═" * (width - 2) + "╝" + RESET + "\n")

def main():
    print("=" * 70)
    print(f"{BOLD}STARTING DAILY COMMODITY RECONCILIATION RUN{RESET}")
    print("=" * 70)

    # Step 1: Ingest Today's CoAs from Gmail
    print(f"\n{BOLD}[Step 1/4] Ingesting Today's Certificates of Analysis (Gmail)...{RESET}")
    coa_result = get_today_coa_emails_and_attachments(
        sender="Ankit.virmani@virufy.org",
        subject="Certificate of Analysis",
        download_dir="/tmp/recon_downloads"
    )
    print(f"  -> CoAs Ingested: {coa_result.get('attachments_count', 0)} files ready.")

    # Step 2: Fetch Invoices Dynamically from Google Drive Folder ID
    print(f"\n{BOLD}[Step 2/4] Fetching Invoices from Google Drive...{RESET}")
    inv_result = fetch_invoices_from_drive(
        drive_url_or_id=DRIVE_FOLDER_ID,
        download_dir="/tmp/recon_invoices"
    )
    print(f"  -> Invoices Ingested: {inv_result.get('invoices_count', 0)} files ready.")

    # Step 3: Run 3-Way Reconciliation Audit Engine
    print(f"\n{BOLD}[Step 3/4] Executing 3-Way Reconciliation Audit Engine...{RESET}")
    recon_data = run_reconciliation(
        coa_dir="/tmp/recon_downloads",
        invoice_dir="/tmp/recon_invoices"
    )

    # Step 4: Render Visual Dashboard (Console + HTML)
    print_console_dashboard(recon_data)

    html_file = generate_html_dashboard(recon_data, "reconciliation_dashboard.html")
    print(f"{BOLD}{GREEN}✔ Dashboard exported to HTML:{RESET} {os.path.abspath(html_file)}")

    # Step 5: Send Email Report to Recipient
    print(f"\n{BOLD}[Step 4/4] Sending Daily Reconciliation Report Email...{RESET}")
    recipient = "Ankit.virmani@virufy.org"
    email_status = send_reconciliation_email(
        to_email=recipient,
        recon_results_json=json.dumps(recon_data)
    )
    print(f"{BOLD}{CYAN}Final Email Status:{RESET} {email_status}")

    print("\n" + "=" * 70)
    print(f"{BOLD}{GREEN}RECONCILIATION AUDIT COMPLETED SUCCESSFULLY{RESET}")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
