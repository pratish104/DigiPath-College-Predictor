"""DigiPath Report Generation Service.

Provides in-memory generation of PDF, CSV, Excel (XLSX), and JSON audit reports
for college admission predictions with resilient fallbacks.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

log = logging.getLogger("digipath.report_generator")

# Optional dependencies detection
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.pdfgen import canvas
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    log.warning("ReportLab is not installed; PDF export will dynamically fallback to structured CSV stream.")

# Check openpyxl availability for Excel
try:
    import openpyxl
    OPENPYXL_AVAILABLE = True
except ImportError:
    OPENPYXL_AVAILABLE = False
    log.warning("openpyxl is not installed; Excel export will dynamically fallback to CSV stream.")


# ── Data Normalization Helpers ───────────────────────────────────────────────

def canonical_export_results(results: Any) -> List[Dict[str, Any]]:
    """Extract the complete unique-college result set for exports.

    The predictor response is a payload containing ``recommendations`` (one
    card per college) and ``results`` (flat historical CAP facts).  Exports
    must use the former when available so historical rows do not become
    duplicate college rows.  The fallback groups a legacy flat list by DTE
    code and retains every row as that college's historical evidence.
    """
    if isinstance(results, dict):
        recommendations = results.get("recommendations")
        if isinstance(recommendations, list) and recommendations:
            return [row for row in recommendations if isinstance(row, dict)]
        results = results.get("results", [])
    if not isinstance(results, list):
        return []
    if results and all(isinstance(row, dict) for row in results) and any(
        "historical_records" in row for row in results
    ):
        return [row for row in results if isinstance(row, dict)]

    grouped: dict[str, Dict[str, Any]] = {}
    order: list[str] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        code = str(row.get("college_code") or row.get("dte_code") or row.get("code") or "").strip().zfill(5)
        if code not in grouped:
            grouped[code] = dict(row)
            grouped[code]["historical_records"] = []
            order.append(code)
        grouped[code]["historical_records"].append(dict(row))
    canonical: list[Dict[str, Any]] = []
    for code in order:
        row = grouped[code]
        records = row.get("historical_records") or []
        row["historical_record_count"] = len(records)
        row["historical_status_counts"] = {
            status: sum(record.get("status") == status for record in records)
            for status in ("SAFE", "MODERATE", "DREAM")
        }
        canonical.append(row)
    return canonical


def normalize_prediction_results(results: Any) -> List[Dict[str, Any]]:
    """Normalize the current canonical predictor cards for report formats."""
    results = canonical_export_results(results)
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        rank = item.get("rank") or idx
        dte_code = str(item.get("college_code") or item.get("dte_code") or item.get("code") or "00000").zfill(5)
        college_name = str(item.get("college_name") or item.get("name") or "N/A").strip()
        branch = str(item.get("branch") or "N/A").strip()
        city = str(item.get("city") or item.get("location") or "N/A").strip()
        college_type = str(item.get("status_name") or item.get("college_type") or item.get("type") or "N/A").strip()
        
        cutoff_24 = item.get("cutoff_2024") or item.get("actual_2024")
        cutoff_24_str = f"{cutoff_24}%" if cutoff_24 is not None else "N/A"
        
        cutoff_25 = item.get("cutoff_2025") or item.get("actual_2025") or item.get("cutoff") or item.get("previous_cutoff")
        cutoff_25_str = f"{cutoff_25}%" if cutoff_25 is not None else "N/A"
        
        pred_26 = item.get("predicted_2026") or item.get("predicted_cutoff") or item.get("forecast_2026")
        pred_26_str = f"{pred_26}%" if pred_26 is not None else "N/A"
        
        prob = item.get("probability_percent") if item.get("probability_percent") is not None else item.get("probability")
        prob_str = f"{prob}%" if prob is not None else "N/A"
        
        status = str(item.get("status") or item.get("classification") or "TARGET").upper()
        fees = str(item.get("open_fees") or item.get("fees") or "N/A")
        status_counts = item.get("historical_status_counts") or {}
        historical_count = item.get("historical_record_count")
        if historical_count is None:
            historical_count = len(item.get("historical_records") or [])
        historical_summary = "; ".join(
            f"{key}: {status_counts.get(key, 0)}" for key in ("SAFE", "MODERATE", "DREAM")
        ) or "N/A"
        
        normalized.append({
            "rank": rank,
            "dte_code": dte_code,
            "college_name": college_name,
            "branch": branch,
            "city": city,
            "college_type": college_type,
            "cutoff_2024": cutoff_24_str,
            "cutoff_2025": cutoff_25_str,
            "predicted_2026": pred_26_str,
            "probability_percent": prob_str,
            "classification": status,
            "open_fees": fees,
            "historical_record_count": historical_count,
            "historical_status_counts": historical_summary,
        })
    return normalized


def results_to_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """Convert normalized predictions list to a pandas DataFrame."""
    norm = normalize_prediction_results(results)
    if not norm:
        return pd.DataFrame(columns=[
            "Rank", "DTE Code", "College Name", "Branch", "City",
            "College Type", "2024 Cutoff", "2025 Cutoff", "2026 AI Forecast",
            "Probability", "Classification", "Historical CAP Records", "Historical Status Counts", "Open Fees"
        ])
    df = pd.DataFrame(norm)
    rename_map = {
        "rank": "Rank",
        "dte_code": "DTE Code",
        "college_name": "College Name",
        "branch": "Branch",
        "city": "City",
        "college_type": "College Type",
        "cutoff_2024": "2024 Cutoff",
        "cutoff_2025": "2025 Cutoff",
        "predicted_2026": "2026 AI Forecast",
        "probability_percent": "Probability",
        "classification": "Classification",
        "historical_record_count": "Historical CAP Records",
        "historical_status_counts": "Historical Status Counts",
        "open_fees": "Open Fees",
    }
    df.rename(columns=rename_map, inplace=True)
    return df


# ── CSV Report Generator ─────────────────────────────────────────────────────

def generate_csv_report(
    results: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bytes, str, str]:
    """Generate a structured CSV report byte stream with audit metadata."""
    meta = metadata or {}
    canonical = canonical_export_results(results)
    timestamp_slug = int(time.time())
    filename = f"DigiPath_Admission_Report_{timestamp_slug}.csv"
    
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Audit Header Comments
    writer.writerow(["# DIGIPATH ADMISSIONS INTELLIGENCE MATRIX — AUDIT REPORT"])
    writer.writerow([f"# Timestamp: {meta.get('timestamp') or datetime.now(timezone.utc).isoformat()}"])
    writer.writerow([f"# Pathway: {str(meta.get('pathway') or meta.get('exam_type') or 'MHT-CET').upper()}"])
    writer.writerow([f"# Candidate Score: {meta.get('score') or meta.get('percentile') or meta.get('percentage') or 'N/A'}"])
    writer.writerow([f"# Category: {meta.get('category') or 'OPEN'}"])
    writer.writerow([f"# Branch Preference: {meta.get('branch_preference') or meta.get('branch') or 'All'}"])
    writer.writerow([f"# Total Colleges: {len(canonical)}"])
    writer.writerow([])  # blank separator
    
    df = results_to_dataframe(results)
    df.to_csv(output, index=False)
    
    content_bytes = output.getvalue().encode("utf-8")
    return content_bytes, "text/csv; charset=utf-8", filename


# ── Excel (XLSX) Report Generator ────────────────────────────────────────────

def generate_excel_report(
    results: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bytes, str, str]:
    """Generate an XLSX report, or dynamically fallback to CSV if openpyxl is absent."""
    if not OPENPYXL_AVAILABLE:
        log.warning("openpyxl is unavailable. Gracefully falling back to structured CSV format.")
        return generate_csv_report(results, metadata)
    
    meta = metadata or {}
    canonical = canonical_export_results(results)
    timestamp_slug = int(time.time())
    filename = f"DigiPath_Admission_Report_{timestamp_slug}.xlsx"
    
    try:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            # Sheet 1: Prediction Results
            df = results_to_dataframe(results)
            df.to_excel(writer, sheet_name="Recommendations", index=False)
            
            # Sheet 2: Audit Metadata
            meta_rows = [
                {"Parameter": "Report Type", "Value": "DigiPath Admissions Forecast"},
                {"Parameter": "Pathway", "Value": str(meta.get("pathway") or meta.get("exam_type") or "MHT-CET").upper()},
                {"Parameter": "Candidate Score", "Value": str(meta.get("score") or meta.get("percentile") or meta.get("percentage") or "N/A")},
                {"Parameter": "Category", "Value": str(meta.get("category") or "OPEN")},
                {"Parameter": "Branch Preference", "Value": str(meta.get("branch_preference") or meta.get("branch") or "All")},
                {"Parameter": "Generated At", "Value": str(meta.get("timestamp") or datetime.now(timezone.utc).isoformat())},
                {"Parameter": "Total Colleges", "Value": str(len(canonical))},
                {"Parameter": "Historical CAP Records", "Value": str(sum(row.get("historical_record_count", 0) for row in canonical))},
            ]
            meta_df = pd.DataFrame(meta_rows)
            meta_df.to_excel(writer, sheet_name="Audit_Metadata", index=False)
            
        buffer.seek(0)
        content_bytes = buffer.getvalue()
        return (
            content_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename,
        )
    except Exception as exc:
        log.exception("Excel generation error: %s. Falling back to CSV.", exc)
        return generate_csv_report(results, metadata)


# ── PDF Report Generator ─────────────────────────────────────────────────────

def generate_pdf_report(
    results: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bytes, str, str]:
    """Generate a styled PDF report using ReportLab, or fallback to CSV if unavailable."""
    if not REPORTLAB_AVAILABLE:
        log.warning("ReportLab is unavailable. Gracefully falling back to structured CSV format.")
        return generate_csv_report(results, metadata)
    
    meta = metadata or {}
    canonical = canonical_export_results(results)
    timestamp_slug = int(time.time())
    filename = f"DigiPath_Admission_Report_{timestamp_slug}.pdf"
    
    try:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=24,
            leftMargin=24,
            topMargin=28,
            bottomMargin=28,
        )
        
        styles = getSampleStyleSheet()
        
        # Cyberpunk / DigiPath palette styles
        title_style = ParagraphStyle(
            "DocTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=16,
            leading=20,
            textColor=colors.HexColor("#008833"),
            alignment=0,
            spaceAfter=4,
        )
        
        meta_style = ParagraphStyle(
            "MetaText",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#333333"),
        )
        
        cell_header_style = ParagraphStyle(
            "CellHeader",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            textColor=colors.black,
            alignment=1,
        )
        
        cell_body_style = ParagraphStyle(
            "CellBody",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7.5,
            leading=9.5,
            textColor=colors.HexColor("#111111"),
        )
        
        cell_center_style = ParagraphStyle(
            "CellCenter",
            parent=cell_body_style,
            alignment=1,
        )

        elements = []
        
        # Header banner
        exam_name = str(meta.get("pathway") or meta.get("exam_type") or "MHT-CET").upper()
        score_val = meta.get("score") or meta.get("percentile") or meta.get("percentage") or "N/A"
        cat_val = meta.get("category") or "OPEN"
        branch_val = meta.get("branch_preference") or meta.get("branch") or "All"
        gen_time = meta.get("timestamp") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        
        elements.append(Paragraph("DIGIPATH // ADMISSION INTELLIGENCE AUDIT REPORT", title_style))
        elements.append(Paragraph(
            f"<b>Pathway:</b> {exam_name} &nbsp;|&nbsp; "
            f"<b>Candidate Score:</b> {score_val} &nbsp;|&nbsp; "
            f"<b>Category:</b> {cat_val} &nbsp;|&nbsp; "
            f"<b>Branch:</b> {branch_val}",
            meta_style,
        ))
        elements.append(Paragraph(
            f"<b>Generated At:</b> {gen_time} &nbsp;|&nbsp; "
            f"<b>Total Colleges:</b> {len(canonical)} &nbsp;|&nbsp; "
            f"<b>Historical CAP Records:</b> {sum(row.get('historical_record_count', 0) for row in canonical)}",
            meta_style,
        ))
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#00ff66"), spaceAfter=12))
        
        # Table columns & headers
        table_data = [
            [
                Paragraph("<b>Rank</b>", cell_header_style),
                Paragraph("<b>DTE</b>", cell_header_style),
                Paragraph("<b>College Name</b>", cell_header_style),
                Paragraph("<b>Branch</b>", cell_header_style),
                Paragraph("<b>Location</b>", cell_header_style),
                Paragraph("<b>Type</b>", cell_header_style),
                Paragraph("<b>CAP Records</b>", cell_header_style),
                Paragraph("<b>Class</b>", cell_header_style),
            ]
        ]
        
        norm_results = normalize_prediction_results(canonical)
        for r in norm_results:
            table_data.append([
                Paragraph(str(r["rank"]), cell_center_style),
                Paragraph(str(r["dte_code"]), cell_center_style),
                Paragraph(str(r["college_name"])[:48], cell_body_style),
                Paragraph(str(r["branch"])[:30], cell_body_style),
                Paragraph(str(r["city"])[:22], cell_body_style),
                Paragraph(str(r["college_type"])[:18], cell_body_style),
                Paragraph(str(r["historical_record_count"]), cell_center_style),
                Paragraph(str(r["classification"]), cell_center_style),
            ])
            
        col_widths = [28, 36, 150, 100, 70, 62, 50, 52]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#00ff66")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4fbf4")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ]))
        elements.append(t)
        
        # Build document
        doc.build(elements)
        buffer.seek(0)
        content_bytes = buffer.getvalue()
        return content_bytes, "application/pdf", filename
        
    except Exception as exc:
        log.exception("PDF generation error: %s. Falling back to CSV.", exc)
        return generate_csv_report(results, metadata)


# ── JSON Report Generator ────────────────────────────────────────────────────

def generate_json_report(
    results: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bytes, str, str]:
    """Generate a structured JSON export."""
    meta = metadata or {}
    timestamp_slug = int(time.time())
    filename = f"DigiPath_Admission_Report_{timestamp_slug}.json"
    
    payload = {
        "metadata": {
            "title": "DigiPath Admission Intelligence Report",
            "timestamp": meta.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "pathway": str(meta.get("pathway") or meta.get("exam_type") or "MHT-CET").upper(),
            "score": meta.get("score") or meta.get("percentile") or meta.get("percentage"),
            "category": meta.get("category") or "OPEN",
            "branch_preference": meta.get("branch_preference") or meta.get("branch") or "All",
            "total_matches": len(canonical_export_results(results)),
        },
        "results": normalize_prediction_results(results),
    }
    content_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    return content_bytes, "application/json; charset=utf-8", filename


# ── Unified Generator Entry Point ────────────────────────────────────────────

def generate_report(
    results: List[Dict[str, Any]],
    format_type: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bytes, str, str]:
    """Dispatch report generation to the appropriate handler based on format_type.
    
    Returns:
        (content_bytes, media_type, filename)
    """
    clean_format = (format_type or "csv").strip().lower().lstrip(".")
    
    if clean_format == "pdf":
        return generate_pdf_report(results, metadata)
    elif clean_format in ("xlsx", "excel"):
        return generate_excel_report(results, metadata)
    elif clean_format == "json":
        return generate_json_report(results, metadata)
    else:  # default to CSV
        return generate_csv_report(results, metadata)
