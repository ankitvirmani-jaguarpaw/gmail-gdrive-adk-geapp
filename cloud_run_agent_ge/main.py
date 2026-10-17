#!/usr/bin/env python3
"""Enterprise Commodity Trade Reconciliation Agent Service on Cloud Run.

Full dual-protocol support:
1. Native A2A (Agent-to-Agent) v0.3 protocol compliant with SendMessageResponse.
2. REST POST /reconcile for direct CLI/curl testing and script pipelines.
Dynamic natural-language filter extraction (subject, dates, folders), smart zero-match discovery,
rich narrative chat summaries, and executive light-mode dashboard artifact publishing.
"""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from google.cloud import storage
from google.genai import types
from google.genai import Client as GenAIClient
from pydantic import BaseModel, Field

app = FastAPI(title="Commodity Reconciliation A2A Agent Service", version="16.0.0")

DEFAULT_FOLDER_ID = "1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8"

def get_active_project_id() -> str:
    """Discovers the active Google Cloud project dynamically."""
    if os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return os.environ["GOOGLE_CLOUD_PROJECT"].strip()
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/project/project-id",
            headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return resp.read().decode().strip()
    except Exception:
        pass
    return "gateway-everything"

# --- Query Parameter Extraction Schema ---
class ReconciliationQueryParams(BaseModel):
    subject_filter: str = Field(
        default="Certificate of Analysis",
        description="Gmail subject filter keyword or phrase, e.g. 'Certificate of Analysis', 'CoA', 'Inspection'"
    )
    date_after: Optional[str] = Field(
        default=None,
        description="Filter emails received on or after this date in YYYY/MM/DD format"
    )
    date_before: Optional[str] = Field(
        default=None,
        description="Filter emails received on or before this date in YYYY/MM/DD format"
    )
    folder_id: str = Field(
        default=DEFAULT_FOLDER_ID,
        description="Google Drive Folder ID or URL containing settlement invoices"
    )

# --- Universal Pydantic Extraction Schemas ---
class UniversalInvoiceLine(BaseModel):
    line_number: str = Field(description="Line item number, e.g. '1.1', '2.1'")
    commodity_description: str = Field(description="Product or commodity name")
    lot_or_po_identifier: str = Field(description="Primary trade lot, USDA/Storage lot, or PO number")
    billed_quantity: int = Field(description="Total billed unit/box count as integer")
    billed_net_weight: float = Field(description="Total billed net weight or volume as float")
    unit_of_measure: str = Field(default="lbs", description="Unit of measure (lbs, kg, MT)")

class UniversalInvoiceDocument(BaseModel):
    is_invoice: bool = Field(description="True if this document is a billing/settlement invoice")
    invoice_number: Optional[str] = Field(default=None, description="Invoice reference number")
    line_items: List[UniversalInvoiceLine] = Field(default_factory=list, description="Itemized lines")

class UniversalInspectionDocument(BaseModel):
    is_inspection_or_coa: bool = Field(description="True if this document is a Certificate of Analysis (CoA) or Manifest")
    certified_lot_or_po: str = Field(description="Primary PO number, Release reference, or Lot number")
    commodity_description: str = Field(description="Certified product description")
    certified_total_quantity: int = Field(description="Total roll-up quantity across all sublots/pallets")
    certified_total_net_weight: float = Field(description="Total roll-up net weight across all sublots/pallets")
    unit_of_measure: str = Field(default="lbs", description="Unit of measure (lbs, kg, MT)")

# --- Dynamic Parameter Parser from User Message ---
def extract_query_parameters(user_text: str, client: GenAIClient) -> ReconciliationQueryParams:
    if not user_text:
        return ReconciliationQueryParams()

    prompt = (
        f"Analyze the following user instruction to identify any specific filtering criteria for email attachments and invoices:\n"
        f"Instruction: \"{user_text}\"\n\n"
        f"Rules for date extraction:\n"
        f"- If the user asks for a specific single date (e.g. 'for September 11th, 2026'), Gmail syntax requires a 1-day bounding box: "
        f"set date_after to the day before ('2026/09/10') and date_before to the day after ('2026/09/12').\n"
        f"- If the user asks for 'after <date>', set date_after in YYYY/MM/DD.\n"
        f"- If the user asks for 'before <date>', set date_before in YYYY/MM/DD.\n"
        f"- subject_filter: email subject phrase (default to 'Certificate of Analysis' if unspecified).\n"
        f"- folder_id: Google Drive folder ID or folder URL (default to '{DEFAULT_FOLDER_ID}')."
    )
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ReconciliationQueryParams,
                temperature=0.0,
            ),
        )
        params = ReconciliationQueryParams.model_validate_json(response.text)
        if "folders/" in params.folder_id:
            params.folder_id = params.folder_id.split("folders/")[1].split("?")[0].strip()
        return params
    except Exception as e:
        print(f"[Query Extraction Note] Using defaults: {e}")
        return ReconciliationQueryParams()

# --- Diagnostic Fallback: Find Available CoAs When Filter Misses ---
def inspect_available_coas_in_inbox(user_token: str, subject_filter: str) -> list:
    """Discovers what CoAs actually exist in the mailbox to give helpful feedback."""
    search_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote('has:attachment ' + subject_filter)}&maxResults=5"
    headers = {"Authorization": f"Bearer {user_token}"}
    req = urllib.request.Request(search_url, headers=headers)
    found_summaries = []
    try:
        with urllib.request.urlopen(req) as resp:
            messages = json.loads(resp.read().decode("utf-8")).get("messages", [])
        for m in messages[:3]:
            d_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}?format=metadata&metadataHeaders=Subject&metadataHeaders=Date"
            with urllib.request.urlopen(urllib.request.Request(d_url, headers=headers)) as d_resp:
                d_data = json.loads(d_resp.read().decode("utf-8"))
                headers_list = d_data.get("payload", {}).get("headers", [])
                subj = next((h["value"] for h in headers_list if h["name"].lower() == "subject"), "No Subject")
                dt = next((h["value"] for h in headers_list if h["name"].lower() == "date"), "Unknown Date")
                found_summaries.append(f"\"{subj}\" (Date: {dt})")
    except Exception as e:
        print(f"[Inbox Discovery Note] {e}")
    return found_summaries

# --- Deduplicated Document Ingestion with Dynamic Filters ---
def fetch_coas_from_gmail(user_token: str, query_params: ReconciliationQueryParams, download_dir: str = "/tmp/recon_downloads") -> tuple:
    os.makedirs(download_dir, exist_ok=True)

    query_parts = [f'subject:"{query_params.subject_filter}"', "has:attachment"]
    if query_params.date_after:
        query_parts.append(f"after:{query_params.date_after}")
    if query_params.date_before:
        query_parts.append(f"before:{query_params.date_before}")

    gmail_query = " ".join(query_parts)
    print(f"[Gmail Search Query] {gmail_query}")

    search_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages?q={urllib.parse.quote(gmail_query)}"
    headers = {"Authorization": f"Bearer {user_token}"}

    req = urllib.request.Request(search_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            messages = json.loads(resp.read().decode("utf-8")).get("messages", [])
    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8")
        raise RuntimeError(f"Gmail API Error ({he.code}): {err_body}")

    # If zero messages matched the filtered query, look up what CoAs DO exist in the inbox
    if not messages:
        available_coas = inspect_available_coas_in_inbox(user_token, query_params.subject_filter)
        return [], gmail_query, available_coas

    downloaded = []
    seen_hashes = set()

    for item in messages:
        msg_id = item["id"]
        detail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}?format=full"
        try:
            with urllib.request.urlopen(urllib.request.Request(detail_url, headers=headers)) as d_resp:
                msg_data = json.loads(d_resp.read().decode("utf-8"))

            def walk_parts(payload):
                parts = []
                if payload.get("filename") and payload.get("body"):
                    parts.append(payload)
                for sub in payload.get("parts", []):
                    parts.extend(walk_parts(sub))
                return parts

            for part in walk_parts(msg_data.get("payload", {})):
                fname = part.get("filename", "")
                att_id = part.get("body", {}).get("attachmentId")
                raw_bytes = None
                if "data" in part.get("body", {}):
                    raw_bytes = base64.urlsafe_b64decode(part["body"]["data"].encode("utf-8"))
                elif att_id:
                    att_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}/attachments/{att_id}"
                    with urllib.request.urlopen(urllib.request.Request(att_url, headers=headers)) as a_resp:
                        raw_data = json.loads(a_resp.read().decode("utf-8")).get("data", "")
                        raw_bytes = base64.urlsafe_b64decode(raw_data.encode("utf-8"))

                if raw_bytes and fname.lower().endswith(".pdf"):
                    f_hash = hashlib.sha256(raw_bytes).hexdigest()
                    if f_hash in seen_hashes:
                        continue
                    seen_hashes.add(f_hash)

                    clean_name = f"{f_hash[:8]}_{re.sub(r'[^a-zA-Z0-9._-]', '_', fname)}"
                    dest = os.path.join(download_dir, clean_name)
                    with open(dest, "wb") as f_out:
                        f_out.write(raw_bytes)

                    downloaded.append({
                        "filename": fname,
                        "path": dest,
                        "sha256": f_hash,
                        "size_bytes": len(raw_bytes),
                        "source": "GMAIL_INBOX"
                    })
        except Exception as e:
            print(f"[Gmail Parse Note] {msg_id}: {e}")

    return downloaded, gmail_query, []

def fetch_invoices_from_drive(user_token: str, folder_id: str, download_dir: str = "/tmp/recon_invoices") -> list:
    os.makedirs(download_dir, exist_ok=True)
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    list_url = f"https://www.googleapis.com/drive/v3/files?q={urllib.parse.quote(query)}&supportsAllDrives=true&includeItemsFromAllDrives=true&fields=files(id,name,size)"
    headers = {"Authorization": f"Bearer {user_token}"}

    req = urllib.request.Request(list_url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            files = json.loads(resp.read().decode("utf-8")).get("files", [])
    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8")
        raise RuntimeError(f"Google Drive API Error ({he.code}): {err_body}")

    downloaded = []
    seen_hashes = set()

    for f in files:
        fname = f["name"]
        fid = f["id"]
        dl_url = f"https://www.googleapis.com/drive/v3/files/{fid}?alt=media&supportsAllDrives=true"
        try:
            with urllib.request.urlopen(urllib.request.Request(dl_url, headers=headers)) as dl_resp:
                content = dl_resp.read()

            f_hash = hashlib.sha256(content).hexdigest()
            if f_hash in seen_hashes:
                continue
            seen_hashes.add(f_hash)

            clean_name = f"{f_hash[:8]}_{re.sub(r'[^a-zA-Z0-9._-]', '_', fname)}"
            dest = os.path.join(download_dir, clean_name)
            with open(dest, "wb") as out_f:
                out_f.write(content)

            downloaded.append({
                "filename": fname,
                "path": dest,
                "sha256": f_hash,
                "size_bytes": len(content),
                "source": "GOOGLE_DRIVE_FOLDER",
                "drive_file_id": fid
            })
        except Exception as e:
            print(f"[Drive Download Note] {fname}: {e}")

    return downloaded

# --- Universal Multimodal Extraction ---
def parse_document_universal(client: GenAIClient, pdf_path: str, doc_role: str):
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    doc_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")

    if doc_role == "invoice":
        prompt = (
            "Analyze this commercial settlement or trade invoice. "
            "Extract all billed line items including: line number, exact commodity description, lot or PO reference, "
            "billed quantity/box count, and billed net weight in lbs. "
            "Ensure lot_or_po_identifier is extracted accurately. "
            "If this document is not an invoice, set is_invoice to false."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[doc_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=UniversalInvoiceDocument,
                temperature=0.0,
            ),
        )
        return UniversalInvoiceDocument.model_validate_json(response.text)
    else:
        prompt = (
            "Analyze this Certificate of Analysis (CoA), Manifest, or Inspection Report. "
            "Extract the primary certified Purchase Order (PO) number, Release reference, or Lot identifier. "
            "Calculate or sum the grand total certified package/unit count across all pallets and sublots. "
            "Calculate or sum the grand total certified net weight in lbs across all pallets and sublots. "
            "If this document is not an inspection or CoA document, set is_inspection_or_coa to false."
        )
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[doc_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=UniversalInspectionDocument,
                temperature=0.0,
            ),
        )
        return UniversalInspectionDocument.model_validate_json(response.text)

# --- 3-Way Reconciliation Math Engine ---
def execute_universal_audit(coa_files: list, invoice_files: list, ai_client: GenAIClient) -> dict:
    invoice_records = []
    seen_invoice_keys = set()

    for inv in invoice_files:
        try:
            parsed = parse_document_universal(ai_client, inv["path"], doc_role="invoice")
            if not parsed.is_invoice or not parsed.line_items:
                continue

            for line in parsed.line_items:
                raw_lot = line.lot_or_po_identifier.strip()
                if not raw_lot:
                    continue

                dedup_key = f"{line.commodity_description.lower()}::{raw_lot.lower()}"
                if dedup_key in seen_invoice_keys:
                    continue
                seen_invoice_keys.add(dedup_key)

                invoice_records.append({
                    "source_file": inv["filename"],
                    "line_no": line.line_number,
                    "commodity": line.commodity_description,
                    "lot_number": raw_lot,
                    "box_count": line.billed_quantity,
                    "net_weight": line.billed_net_weight,
                    "uom": line.unit_of_measure,
                })
        except Exception as e:
            print(f"[Parse Error] Invoice {inv['filename']}: {e}")

    coa_records = []
    seen_coa_keys = set()

    for coa in coa_files:
        try:
            parsed = parse_document_universal(ai_client, coa["path"], doc_role="inspection")
            if not parsed.is_inspection_or_coa or not parsed.certified_lot_or_po:
                continue

            raw_po = parsed.certified_lot_or_po.strip()
            dedup_key = f"{parsed.commodity_description.lower()}::{raw_po.lower()}"
            if dedup_key in seen_coa_keys:
                continue
            seen_coa_keys.add(dedup_key)

            coa_records.append({
                "source_file": coa["filename"],
                "po_number": raw_po,
                "commodity": parsed.commodity_description,
                "total_quantity": parsed.certified_total_quantity,
                "total_weight": parsed.certified_total_net_weight,
                "uom": parsed.unit_of_measure,
            })
        except Exception as e:
            print(f"[Parse Error] CoA {coa['filename']}: {e}")

    results = []
    for inv in invoice_records:
        inv_lot = inv["lot_number"]
        inv_qty = inv["box_count"]
        inv_wt = inv["net_weight"]

        clean_inv = re.sub(r"[^a-zA-Z0-9]", "", inv_lot).lower()
        matched_coa = next(
            (c for c in coa_records if clean_inv == re.sub(r"[^a-zA-Z0-9]", "", c["po_number"]).lower()
             or clean_inv in re.sub(r"[^a-zA-Z0-9]", "", c["po_number"]).lower()
             or re.sub(r"[^a-zA-Z0-9]", "", c["po_number"]).lower() in clean_inv),
            None
        )

        if not matched_coa:
            results.append({
                "commodity": inv["commodity"],
                "invoice_file": inv["source_file"],
                "invoice_line": inv["line_no"],
                "invoice_lot": inv_lot,
                "coa_file": "MISSING / UNMATCHED",
                "coa_po": "NONE",
                "invoice_qty": inv_qty,
                "coa_qty": 0,
                "qty_delta": -inv_qty,
                "invoice_wt": inv_wt,
                "coa_wt": 0.0,
                "wt_delta": -inv_wt,
                "uom": inv["uom"],
                "status": "DISCREPANCY",
                "reason": f"No inspection or CoA document found matching billed reference {inv_lot}",
            })
            continue

        coa_qty = matched_coa["total_quantity"]
        coa_wt = matched_coa["total_weight"]
        qty_diff = coa_qty - inv_qty
        wt_diff = round(coa_wt - inv_wt, 2)
        po_match = (clean_inv == re.sub(r"[^a-zA-Z0-9]", "", matched_coa["po_number"]).lower())

        if qty_diff == 0 and abs(wt_diff) <= 0.5 and po_match:
            results.append({
                "commodity": inv["commodity"],
                "invoice_file": inv["source_file"],
                "invoice_line": inv["line_no"],
                "invoice_lot": inv_lot,
                "coa_file": matched_coa["source_file"],
                "coa_po": matched_coa["po_number"],
                "invoice_qty": inv_qty,
                "coa_qty": coa_qty,
                "qty_delta": 0,
                "invoice_wt": inv_wt,
                "coa_wt": coa_wt,
                "wt_delta": 0.0,
                "uom": inv["uom"],
                "status": "MATCH",
                "reason": "Exact parity across PO/Lot, unit quantity, and certified net weight.",
            })
        else:
            issues = []
            if not po_match:
                issues.append(f"PO Mismatch: Billed {inv_lot} vs Certified {matched_coa['po_number']}")
            if qty_diff != 0:
                issues.append(f"Quantity Variance: {qty_diff:+d} units ({inv_qty} billed vs {coa_qty} certified)")
            if abs(wt_diff) > 0.5:
                issues.append(f"Weight Variance: {wt_diff:+.2f} {inv['uom']} ({inv_wt:,.2f} billed vs {coa_wt:,.2f} certified)")
            results.append({
                "commodity": inv["commodity"],
                "invoice_file": inv["source_file"],
                "invoice_line": inv["line_no"],
                "invoice_lot": inv_lot,
                "coa_file": matched_coa["source_file"],
                "coa_po": matched_coa["po_number"],
                "invoice_qty": inv_qty,
                "coa_qty": coa_qty,
                "qty_delta": qty_diff,
                "invoice_wt": inv_wt,
                "coa_wt": coa_wt,
                "wt_delta": wt_diff,
                "uom": inv["uom"],
                "status": "DISCREPANCY",
                "reason": "; ".join(issues),
            })

    matches_count = sum(1 for r in results if r["status"] == "MATCH")
    discrepancies_count = sum(1 for r in results if r["status"] == "DISCREPANCY")
    total_audited_wt = sum(r["invoice_wt"] for r in results)
    total_variance_wt = sum(r["wt_delta"] for r in results if r["status"] == "DISCREPANCY")

    return {
        "status": "success" if results else "warning",
        "date_reconciled": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_lines_audited": len(results),
        "matches_count": matches_count,
        "discrepancies_count": discrepancies_count,
        "total_audited_wt": total_audited_wt,
        "total_variance_wt": round(total_variance_wt, 2),
        "details": results,
    }

# --- Executive Chat Narrative Builder ---
def build_executive_chat_narrative(recon_data: dict, gcs_url: str, params: ReconciliationQueryParams) -> str:
    total = recon_data.get("total_lines_audited", 0)
    matches = recon_data.get("matches_count", 0)
    discrepancies = recon_data.get("discrepancies_count", 0)
    match_pct = round((matches / total * 100), 1) if total else 0
    total_wt = recon_data.get("total_audited_wt", 0.0)
    net_var = recon_data.get("total_variance_wt", 0.0)

    # Filter details
    filter_desc = f"- **Gmail Search Filter**: `subject:\"{params.subject_filter}\"`"
    if params.date_after:
        filter_desc += f" `after:{params.date_after}`"
    if params.date_before:
        filter_desc += f" `before:{params.date_before}`"
    filter_desc += f"\n- **Drive Folder Source**: `{params.folder_id}`"

    verdict = "**CLEAN AUDIT** (100% Parity)" if discrepancies == 0 else f"**DISCREPANCIES DETECTED** ({discrepancies} variance{'s' if discrepancies > 1 else ''})"

    narrative = (
        f"### Commodity Trade Reconciliation Audit Results\n\n"
        f"**Audit Verdict**: {verdict}\n\n"
        f"**Key Findings & Summary**:\n"
        f"- **Audited Lots**: **{total}** total line items reconciled\n"
        f"- **Clean Matches**: **{matches}** ({match_pct}% parity rate)\n"
        f"- **Discrepancies**: **{discrepancies}** lines requiring settlement review\n"
        f"- **Billed Volume**: **{total_wt:,.2f} lbs**\n"
        f"- **Net Variance**: **{net_var:+,.2f} lbs**\n\n"
        f"**Audit Scope & Parameters Applied**:\n{filter_desc}\n\n"
        f"**Itemized Audit Breakdown**:\n\n"
        f"| Commodity | Line | Billed Lot | Certified PO | Billed Qty | CoA Qty | Variance | Status |\n"
        f"| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
    )

    for r in recon_data.get("details", []):
        is_match = r.get("status") == "MATCH"
        status_icon = "MATCH" if is_match else "DISCREPANCY"
        var_text = "0" if is_match else f"{r.get('qty_delta'):+d} units / {r.get('wt_delta'):+,.2f} lbs"
        narrative += f"| {r.get('commodity')} | {r.get('invoice_line')} | `{r.get('invoice_lot')}` | `{r.get('coa_po')}` | {r.get('invoice_qty'):,} | {r.get('coa_qty'):,} | {var_text} | {status_icon} |\n"

    discrepancy_lines = [r for r in recon_data.get("details", []) if r.get("status") != "MATCH"]
    if discrepancy_lines:
        narrative += "\n**Detailed Issue Analysis**:\n"
        for d in discrepancy_lines:
            narrative += f"- **{d.get('commodity')} (Line {d.get('invoice_line')}, Lot `{d.get('invoice_lot')}`)**: {d.get('reason')}\n"

    narrative += f"\n**Interactive Executive Dashboard**:\n[View Light-Mode Audit Dashboard]({gcs_url})\n"
    return narrative

# --- Light-Mode Executive Dashboard Builder ---
def build_light_mode_html(recon_data: dict, coa_proof: list, invoice_proof: list) -> str:
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y, %H:%M UTC")
    total_audited = recon_data.get("total_lines_audited", 0)
    matches_count = recon_data.get("matches_count", 0)
    discrepancies_count = recon_data.get("discrepancies_count", 0)
    match_pct = round((matches_count / total_audited * 100), 1) if total_audited else 0
    total_wt = recon_data.get("total_audited_wt", 0.0)
    total_var_wt = recon_data.get("total_variance_wt", 0.0)

    rows = []
    for r in recon_data.get("details", []):
        is_match = r.get("status") == "MATCH"
        status_badge = '<span class="badge badge-match">&#10004; MATCH</span>' if is_match else '<span class="badge badge-disc">&#9888; DISCREPANCY</span>'
        q_d = f"{r.get('qty_delta', 0):+d}" if not is_match else '<span class="delta-zero">0</span>'
        w_d = f"{r.get('wt_delta', 0.0):+,.2f} {r.get('uom', 'lbs')}" if not is_match else f'<span class="delta-zero">0.00 {r.get("uom", "lbs")}</span>'
        po_color = 'style="color: var(--warning-text); font-weight: 700;"' if r.get('coa_po') != r.get('invoice_lot') else ""

        rows.append(f"""
        <tr class="row-item {'row-match' if is_match else 'row-disc'}">
          <td class="text-bold text-dark">{r.get('commodity')}</td>
          <td>{r.get('invoice_line')}</td>
          <td class="font-mono text-bold text-dark">{r.get('invoice_lot')}</td>
          <td class="font-mono" {po_color}>{r.get('coa_po')}</td>
          <td class="font-mono">{r.get('invoice_qty'):,}</td>
          <td class="font-mono">{r.get('coa_qty'):,}</td>
          <td><span class="{'delta-shortfall' if not is_match else ''}">{q_d}</span></td>
          <td class="font-mono">{r.get('invoice_wt'):,.2f} {r.get('uom', 'lbs')}</td>
          <td class="font-mono">{r.get('coa_wt'):,.2f} {r.get('uom', 'lbs')}</td>
          <td><span class="{'delta-shortfall' if not is_match else ''}">{w_d}</span></td>
          <td>{status_badge}</td>
          <td class="{'text-bold' if not is_match else ''}" style="{'color: var(--error-text);' if not is_match else ''}">{r.get('reason')}</td>
        </tr>""")

    rendered_rows = "\n".join(rows)
    proof_items = "".join(f"<li><b>{p['filename']}</b> ({p['size_bytes']:,} bytes) &bull; SHA-256: <code>{p['sha256'][:16]}...</code> &bull; Source: {p['source']}</li>" for p in coa_proof + invoice_proof)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Commodity Trade Reconciliation Dashboard</title>
<style>
  :root {{
    --bg-page: #f8fafc;
    --bg-surface: #ffffff;
    --bg-subtle: #f1f5f9;
    --border-color: #e2e8f0;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #64748b;
    --brand-primary: #0284c7;
    --success-bg: #ecfdf5;
    --success-border: #a7f3d0;
    --success-text: #065f46;
    --success-badge: #10b981;
    --error-bg: #fef2f2;
    --error-border: #fecaca;
    --error-text: #991b1b;
    --error-badge: #ef4444;
    --warning-bg: #fffbeb;
    --warning-text: #92400e;
    --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: var(--bg-page);
    color: var(--text-primary);
    line-height: 1.5;
    padding: 32px 24px;
    margin: 0;
  }}
  .dashboard-wrapper {{ max-width: 1280px; margin: 0 auto; }}
  .header-card {{
    background: var(--bg-surface);
    border: 1px solid var(--border-color);
    border-radius: 12px;
    padding: 24px 28px;
    margin-bottom: 24px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .btn-print {{
    background: var(--bg-surface); border: 1px solid var(--border-color);
    color: var(--text-secondary); padding: 7px 16px; border-radius: 8px;
    font-size: 13px; font-weight: 600; cursor: pointer;
  }}
  .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
  .kpi-card {{
    background: var(--bg-surface); border: 1px solid var(--border-color);
    border-radius: 10px; padding: 18px 20px;
  }}
  .kpi-label {{ font-size: 12px; text-transform: uppercase; color: var(--text-muted); font-weight: 600; }}
  .kpi-value {{ font-size: 28px; font-weight: 800; color: var(--text-primary); margin-top: 6px; }}
  .content-card {{
    background: var(--bg-surface); border: 1px solid var(--border-color);
    border-radius: 12px; overflow: hidden; margin-bottom: 24px;
  }}
  .card-toolbar {{
    padding: 16px 24px; border-bottom: 1px solid var(--border-color);
    display: flex; justify-content: space-between; align-items: center; background: var(--bg-subtle);
  }}
  .toolbar-title {{ font-size: 16px; font-weight: 700; color: var(--text-primary); }}
  .filter-tabs {{ display: flex; gap: 6px; background: var(--bg-surface); padding: 4px; border-radius: 8px; border: 1px solid var(--border-color); }}
  .tab-btn {{
    background: transparent; border: none; padding: 6px 14px; font-size: 13px;
    font-weight: 600; border-radius: 6px; color: var(--text-secondary); cursor: pointer;
  }}
  .tab-btn.active {{ background: var(--brand-primary); color: #fff; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13.5px; text-align: left; }}
  th {{
    background: var(--bg-surface); color: var(--text-muted); font-weight: 600;
    padding: 12px 18px; border-bottom: 1px solid var(--border-color); font-size: 11.5px; text-transform: uppercase;
  }}
  td {{ padding: 14px 18px; border-bottom: 1px solid var(--border-color); color: var(--text-secondary); }}
  tr:last-child td {{ border-bottom: none; }}
  .font-mono {{ font-family: var(--font-mono); font-size: 13px; }}
  .text-dark {{ color: var(--text-primary); }}
  .text-bold {{ font-weight: 600; }}
  .badge {{ display: inline-flex; align-items: center; gap: 5px; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700; }}
  .badge-match {{ background: var(--success-bg); border: 1px solid var(--success-border); color: var(--success-text); }}
  .badge-disc {{ background: var(--error-bg); border: 1px solid var(--error-border); color: var(--error-text); }}
  .delta-shortfall {{ color: var(--error-badge); font-weight: 700; font-family: var(--font-mono); }}
  .delta-zero {{ color: var(--text-muted); font-family: var(--font-mono); }}
  .info-banner {{
    background: #e0f2fe; border: 1px solid #bae6fd;
    border-radius: 10px; padding: 18px 22px; margin-top: 24px;
  }}
  .info-banner h4 {{ font-size: 14px; font-weight: 700; color: #0369a1; margin: 0 0 8px; }}
  .info-banner ul {{ margin: 0; padding-left: 20px; font-size: 12px; color: #075985; line-height: 1.6; font-family: var(--font-mono); }}
  .footer {{ text-align: center; color: var(--text-muted); font-size: 12px; margin-top: 36px; }}
  @media print {{
    body {{ background: #fff; padding: 0; }}
    .btn-print, .filter-tabs {{ display: none !important; }}
    .header-card, .kpi-card, .content-card {{ box-shadow: none; border-color: #cbd5e1; }}
  }}
</style>
</head>
<body>
<div class="dashboard-wrapper">
  <header class="header-card">
    <div>
      <h1 style="font-size:24px; margin:0;">Commodity Trade Reconciliation Dashboard</h1>
      <p style="margin:4px 0 0; color:var(--text-muted); font-size:14px;">Autonomous Multi-Modal Audit Engine</p>
    </div>
    <div style="display:flex; gap:12px; align-items:center;">
      <span style="font-size:13px; color:var(--text-secondary);">Audit: <strong>{date_str}</strong></span>
      <button class="btn-print" onclick="window.print()">Print / PDF</button>
    </div>
  </header>

  <section class="kpi-grid">
    <div class="kpi-card"><div class="kpi-label">Lots Audited</div><div class="kpi-value">{total_audited}</div></div>
    <div class="kpi-card"><div class="kpi-label">Clean Matches</div><div class="kpi-value" style="color:var(--success-badge);">{matches_count} <span style="font-size:14px; color:var(--text-muted);">({match_pct}%)</span></div></div>
    <div class="kpi-card"><div class="kpi-label">Discrepancies</div><div class="kpi-value" style="color:var(--error-badge);">{discrepancies_count}</div></div>
    <div class="kpi-card"><div class="kpi-label">Billed Net Volume</div><div class="kpi-value">{total_wt:,.0f}</div></div>
    <div class="kpi-card"><div class="kpi-label">Net Variance</div><div class="kpi-value" style="color:var(--warning-text);">{total_var_wt:+,.2f}</div></div>
  </section>

  <section class="content-card">
    <div class="card-toolbar">
      <div class="toolbar-title">Itemized Commodity Trade Reconciliation Ledger</div>
      <div class="filter-tabs">
        <button class="tab-btn active" onclick="filterRows('ALL', this)">All Lots ({total_audited})</button>
        <button class="tab-btn" onclick="filterRows('MATCH', this)">Clean Matches ({matches_count})</button>
        <button class="tab-btn" onclick="filterRows('DISCREPANCY', this)">Discrepancies ({discrepancies_count})</button>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>Commodity</th><th>Line</th><th>Billed Lot</th><th>CoA PO#</th>
          <th>Billed Qty</th><th>CoA Qty</th><th>Qty &Delta;</th>
          <th>Billed Volume</th><th>CoA Volume</th><th>Volume &Delta;</th>
          <th>Verdict</th><th>Audit Analysis</th>
        </tr>
      </thead>
      <tbody>
        {rendered_rows}
      </tbody>
    </table>
  </section>

  <div class="info-banner">
    <h4>Verified Audit Provenance (Deduplicated Source Documents)</h4>
    <ul>
      {proof_items}
    </ul>
  </div>

  <footer class="footer">
    Universal Commodity Trade Reconciliation Audit &bull; Generated autonomously on {date_str} &bull; Light-Mode Executive Dashboard
  </footer>
</div>

<script>
  function filterRows(type, btn) {{
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const rows = document.querySelectorAll('.row-item');
    rows.forEach(row => {{
      if (type === 'ALL') {{
        row.style.display = '';
      }} else if (type === 'MATCH') {{
        row.style.display = row.classList.contains('row-match') ? '' : 'none';
      }} else if (type === 'DISCREPANCY') {{
        row.style.display = row.classList.contains('row-disc') ? '' : 'none';
      }}
    }});
  }}
</script>
</body>
</html>"""

# --- Publish to GCS ---
def upload_dashboard_to_gcs(html_content: str, bucket_name: str, object_name: str = "reconciliation_dashboard.html") -> dict:
    project_id = get_active_project_id()
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)

    if not bucket.exists():
        bucket = client.create_bucket(bucket_name, location="us-central1")

    blob = bucket.blob(object_name)
    blob.upload_from_string(html_content, content_type="text/html; charset=utf-8")
    blob.cache_control = "no-cache, max-age=0"
    blob.patch()

    try:
        blob.make_public()
    except Exception:
        pass

    web_url = f"https://storage.googleapis.com/{bucket_name}/{object_name}"
    console_url = f"https://console.cloud.google.com/storage/browser/_details/{bucket_name}/{object_name}?project={project_id}"

    return {"web_url": web_url, "console_url": console_url, "gcs_uri": f"gs://{bucket_name}/{object_name}"}

# --- A2A Agent Card Discovery with Dynamic Capability Parameters ---
def generate_agent_card(request: Request) -> dict:
    host_url = str(request.base_url).rstrip("/")
    return {
        "protocolVersion": "0.3.0",
        "name": "Commodity Trade Reconciliation Agent",
        "description": "Autonomous 3-way reconciliation agent that audits supplier Certificates of Analysis (CoAs) in Gmail against settlement invoices in Google Drive, publishing an executive light-mode dashboard to GCS.",
        "version": "2.1.0",
        "url": host_url,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "reconcile_commodities",
                "name": "Commodity Trade Reconciliation",
                "description": "Performs 3-way trade reconciliation audit between supplier Certificates of Analysis (CoAs) in Gmail and settlement invoices in Google Drive. Supports dynamic email subject keywords, date ranges, and Google Drive folder IDs.",
                "tags": ["reconciliation", "commodities", "cme", "audit", "gmail", "drive"],
                "examples": [
                    "Reconcile my commodity trades",
                    "Audit trades with subject 'Inspection Report' from last month",
                    "Run reconciliation for emails after 2026/09/01 using folder 1tW3zXN0fTvVAioE6j6VMNAP0D3M2e7B8",
                    "Reconcile incoming CoAs and spot invoices"
                ],
                "inputModes": ["text/plain"],
                "outputModes": ["text/plain"]
            }
        ]
    }

@app.get("/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
def get_a2a_agent_card(request: Request):
    return JSONResponse(content=generate_agent_card(request))

@app.get("/health")
def health():
    return {"status": "ok", "service": "commodity-reconciliation-agent"}

# --- Core Pipeline Execution ---
def run_pipeline(token: str, query_params: ReconciliationQueryParams, ai_client: GenAIClient):
    coas, actual_query, available_in_inbox = fetch_coas_from_gmail(user_token=token, query_params=query_params)
    if not coas:
        # Return structured diagnostic info instead of crashing
        return {
            "zero_matches": True,
            "query_used": actual_query,
            "available_coas": available_in_inbox,
            "params": query_params
        }

    invoices = fetch_invoices_from_drive(user_token=token, folder_id=query_params.folder_id)
    if not invoices:
        raise ValueError(f"Zero invoice PDFs downloaded from Drive folder {query_params.folder_id}.")

    recon_data = execute_universal_audit(coas, invoices, ai_client)
    project_id = get_active_project_id()
    bucket_name = f"cme-recon-dashboards-{project_id}"
    html_content = build_light_mode_html(recon_data, coas, invoices)
    gcs_info = upload_dashboard_to_gcs(html_content, bucket_name)

    return {
        "zero_matches": False,
        "coas": coas,
        "invoices": invoices,
        "recon_data": recon_data,
        "gcs_info": gcs_info
    }

# --- Protocol 1: A2A v0.3 Protocol Spec Handler ---
@app.post("/")
@app.post("/tasks/send")
@app.post("/message/send")
async def handle_a2a_rpc(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_forwarded_authorization: Optional[str] = Header(None),
    x_user_authorization: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
):
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    rpc_id = body.get("id", str(uuid.uuid4()))
    params = body.get("params", {})
    task_id = params.get("id", str(uuid.uuid4()))
    context_id = params.get("contextId") or params.get("context_id") or str(uuid.uuid4())

    # Multi-header token extraction
    candidate_tokens = []
    if x_user_token:
        candidate_tokens.append(("x_user_token", x_user_token))
    if x_user_authorization:
        candidate_tokens.append(("x_user_authorization", x_user_authorization.replace("Bearer ", "").strip()))
    if x_forwarded_authorization:
        candidate_tokens.append(("x_forwarded_authorization", x_forwarded_authorization.replace("Bearer ", "").strip()))
    if authorization:
        candidate_tokens.append(("authorization", authorization.replace("Bearer ", "").strip()))

    ctx_creds = params.get("context", {}).get("user", {}).get("credentials", {})
    if isinstance(ctx_creds, dict) and "token" in ctx_creds:
        candidate_tokens.append(("body.context.token", ctx_creds["token"]))

    token = None
    for src, t in candidate_tokens:
        if t and t.startswith("ya29."):
            token = t
            break
    if not token and candidate_tokens:
        token = candidate_tokens[0][1]

    # Extract user message text
    user_text = ""
    parts = params.get("message", {}).get("parts", [])
    for p in parts:
        if isinstance(p, dict):
            user_text += p.get("text", "")
        elif isinstance(p, str):
            user_text += p

    user_text_clean = user_text.strip().lower()

    # Spec-compliant A2A v0.3 Task builder
    def build_a2a_task_response(state: str, text_content: str, artifacts: list = None) -> JSONResponse:
        msg_id = str(uuid.uuid4())
        msg_obj = {
            "kind": "message",
            "messageId": msg_id,
            "message_id": msg_id,
            "role": "agent",
            "parts": [
                {
                    "kind": "text",
                    "text": text_content
                }
            ]
        }

        formatted_artifacts = []
        if artifacts:
            for art in artifacts:
                art_id = str(uuid.uuid4())
                formatted_artifacts.append({
                    "artifactId": art_id,
                    "artifact_id": art_id,
                    "name": art.get("name", "reconciliation_dashboard"),
                    "description": "Executive Commodity Trade Reconciliation Dashboard",
                    "parts": [{"kind": "text", "text": art.get("url", "")}]
                })

        return JSONResponse(content={
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "kind": "task",
                "id": task_id,
                "contextId": context_id,
                "context_id": context_id,
                "status": {
                    "state": state,
                    "message": msg_obj
                },
                "history": [msg_obj],
                "artifacts": formatted_artifacts
            }
        })

    # Conversational routing
    if not user_text_clean or any(g in user_text_clean for g in ["hi", "hello", "hey", "how are you", "help", "who are you"]):
        greeting = (
            "Hello! I am your autonomous **Commodity Trade Reconciliation Agent**.\n\n"
            "I perform a 3-way audit between supplier **Certificates of Analysis (CoAs)** in Gmail and **Invoices** in Google Drive.\n\n"
            "**Supported Queries & Custom Parameters**:\n"
            "- *\"Reconcile my commodity trades\"* (audits all available CoAs and invoices)\n"
            "- *\"Reconcile shipments with subject 'Inspection Report'\"*\n"
            "- *\"Audit trades for October 11th, 2026\"*\n"
            "- *\"Reconcile invoices in Drive folder `<folder_id>`\"*\n\n"
            "How can I help you reconcile today?"
        )
        return build_a2a_task_response(state="completed", text_content=greeting)

    if not token:
        return build_a2a_task_response(
            state="failed",
            text_content="Error: Missing Google Workspace OAuth authorization token to access your Gmail and Google Drive."
        )

    try:
        project_id = get_active_project_id()
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
        ai_client = GenAIClient(vertexai=True, project=project_id, location="us-central1")

        query_params = extract_query_parameters(user_text=user_text, client=ai_client)

        data = run_pipeline(token=token, query_params=query_params, ai_client=ai_client)

        # Handle zero matching emails gracefully with diagnostic feedback
        if data.get("zero_matches"):
            available_list = data.get("available_coas", [])
            avail_msg = ""
            if available_list:
                avail_msg = "\n\n**Found other recent Certificate of Analysis emails in your inbox**:\n" + "\n".join(f"- {c}" for c in available_list)
                avail_msg += "\n\nTo audit all available trades, simply prompt:\n> *\"Reconcile my commodity trades\"*"
            else:
                avail_msg = "\n\nNo Certificate of Analysis emails with attachments were found in your inbox."

            zero_msg = (
                f"### No Matching Documents Found\n\n"
                f"I searched your Gmail using query: `{data['query_used']}` but found **0 matching emails** with attachments."
                f"{avail_msg}"
            )
            return build_a2a_task_response(state="completed", text_content=zero_msg)

        recon = data["recon_data"]
        gcs = data["gcs_info"]

        chat_narrative = build_executive_chat_narrative(
            recon_data=recon,
            gcs_url=gcs["web_url"],
            params=query_params
        )

        return build_a2a_task_response(
            state="completed",
            text_content=chat_narrative,
            artifacts=[{"name": "reconciliation_dashboard", "url": gcs["web_url"]}]
        )
    except Exception as e:
        print("[A2A Exception Traceback]:")
        traceback.print_exc()
        return build_a2a_task_response(state="failed", text_content=f"Audit execution issue: {str(e)}")

# --- Protocol 2: REST POST /reconcile ---
@app.post("/reconcile")
def reconcile_rest(
    authorization: Optional[str] = Header(None),
    x_user_token: Optional[str] = Header(None),
    subject_filter: str = "Certificate of Analysis",
    date_after: Optional[str] = None,
    date_before: Optional[str] = None,
    folder_id: str = DEFAULT_FOLDER_ID,
):
    token = x_user_token
    if not token and authorization:
        token = authorization.replace("Bearer ", "").strip()

    if not token:
        raise HTTPException(status_code=401, detail="Missing required authorization token.")

    query_params = ReconciliationQueryParams(
        subject_filter=subject_filter,
        date_after=date_after,
        date_before=date_before,
        folder_id=folder_id
    )

    project_id = get_active_project_id()
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
    ai_client = GenAIClient(vertexai=True, project=project_id, location="us-central1")

    data = run_pipeline(token=token, query_params=query_params, ai_client=ai_client)

    if data.get("zero_matches"):
        return JSONResponse(content={
            "status": "warning",
            "message": f"Zero messages matched query: {data['query_used']}",
            "available_coas": data.get("available_coas", [])
        })

    recon_data = data["recon_data"]
    coas = data["coas"]
    invoices = data["invoices"]
    gcs_info = data["gcs_info"]

    return JSONResponse(content={
        "status": "success",
        "audit_evidence_provenance": {
            "unique_coas_ingested": len(coas),
            "unique_invoices_ingested": len(invoices),
            "applied_filters": query_params.model_dump(),
            "deduplicated_coas": [{"filename": c["filename"], "sha256": c["sha256"][:16], "bytes": c["size_bytes"]} for c in coas],
            "deduplicated_invoices": [{"filename": i["filename"], "sha256": i["sha256"][:16], "bytes": i["size_bytes"]} for i in invoices],
        },
        "audit_summary": {
            "lots_audited": recon_data["total_lines_audited"],
            "clean_matches": recon_data["matches_count"],
            "discrepancies": recon_data["discrepancies_count"],
            "audited_net_weight": recon_data["total_audited_wt"],
            "net_weight_variance": recon_data["total_variance_wt"],
        },
        "dashboard_links": gcs_info,
        "details": recon_data["details"],
    })
