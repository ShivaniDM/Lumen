"""
app.py — Lumen Verify AML Decision Workbench
UI only. Reads from the project's data/ CSVs (src/schema.py is the source of
truth for those tables) and writes audit entries through src.audit.log_event,
so the UI and the rest of the system share one audit trail
(data/audit_log.csv).

Run from the project root , so data/ resolves
correctly:

    streamlit run app.py
"""

import sys
from pathlib import Path

import streamlit as st
import pandas as pd
from datetime import datetime, timezone

import json


# Project root is the parent of lumen_ui/. Add it to sys.path so `src` can be
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src import audit, verifier  # noqa: E402

st.set_page_config(
    page_title="Lumen Verify | AML Workbench",
    layout="wide",
    page_icon="",
)

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR        = PROJECT_ROOT / "data"
OVERRIDES_CSV   = DATA_DIR / "pending_overrides.csv"
ALERTS_CSV      = DATA_DIR / "alerts.csv"
CUSTOMERS_CSV   = DATA_DIR / "customers.csv"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
EVIDENCE_CSV    = DATA_DIR / "evidence_items.csv"
PRIOR_CASES_CSV = DATA_DIR / "prior_cases.csv"
KYC_STATUS_CSV  = DATA_DIR / "kyc_profile_status.csv"
AI_OUTPUTS_CSV  = DATA_DIR / "ai_outputs.csv"
HUMAN_REVIEWS_CSV = DATA_DIR / "human_reviews.csv"
AUDIT_LOG_CSV   = DATA_DIR / "audit_log.csv"

REQUIRED = [ALERTS_CSV, CUSTOMERS_CSV, TRANSACTIONS_CSV, EVIDENCE_CSV,
            PRIOR_CASES_CSV, KYC_STATUS_CSV, AI_OUTPUTS_CSV, HUMAN_REVIEWS_CSV]

# ── Guard: check data files exist ────────────────────────────────────────────
missing = [p for p in REQUIRED if not p.exists()]
if missing:
    st.error(
        "Data files not found: "
        + ", ".join(str(p.relative_to(PROJECT_ROOT)) for p in missing)
        + ". Run the app from the project root: `streamlit run app.py`, "
        "and make sure data/ has been generated."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# A small fixed roster of analysts/managers for the demo session switcher.
# This is UI-only convenience state, not part of the project schema — there is
# no employees table in src/schema.py.
# ─────────────────────────────────────────────────────────────────────────────
ANALYSTS = {
    "EMP-003": {"id": "EMP-003", "name": "S. Mayekar", "rank": "Analyst"},
    "EMP-001": {"id": "EMP-001", "name": "L. Pagan", "rank": "Lead Analyst"},
    "EMP-006": {"id": "EMP-006", "name": "M. Chen", "rank": "Compliance Manager"},
}

SEVERITY_LABELS = {"high": "High", "med": "Medium", "low": "Low"}
STATUS_LABELS = {"open": "Pending Review", "in_review": "In Progress", "closed": "Closed"}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def write_log(action: str, details: dict, alert_id: str | None = None) -> dict:
    """Append one row to the shared audit trail (data/audit_log.csv)."""
    emp = st.session_state.get("current_user", {})
    return audit.log_event(
        actor=f"ui:{emp.get('id', 'UNKNOWN')}",
        action=action,
        alert_id=alert_id,
        details=details,
    )


def load_overrides() -> pd.DataFrame:
    cols = [
        "change_id", "alert_id", "field_changed", "old_value", "new_value",
        "changed_by_id", "changed_by_name", "changed_at", "reason",
        "status", "reviewed_by", "reviewed_at",
    ]
    if OVERRIDES_CSV.exists():
        return pd.read_csv(OVERRIDES_CSV, dtype=str, keep_default_na=False)
    return pd.DataFrame(columns=cols)


def save_override(alert_id, field, old_val, new_val, reason) -> str:
    df  = load_overrides()
    emp = st.session_state.current_user
    ts  = datetime.now(timezone.utc).isoformat()
    cid = f"CHG-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{alert_id}"
    row = {
        "change_id":       cid,
        "alert_id":        alert_id,
        "field_changed":   field,
        "old_value":       old_val,
        "new_value":       new_val,
        "changed_by_id":   emp["id"],
        "changed_by_name": emp["name"],
        "changed_at":      ts,
        "reason":          reason,
        "status":          "pending",
        "reviewed_by":     "",
        "reviewed_at":     "",
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(OVERRIDES_CSV, index=False)
    write_log("field_override", {
        "field_changed":   field,
        "old_value":       str(old_val),
        "new_value":       str(new_val),
        "reason":          reason,
        "override_status": "pending_manager_review",
    }, alert_id=alert_id)
    return cid


def update_override_status(change_id: str, status: str, reviewer: str) -> None:
    df = load_overrides()
    mask = df["change_id"] == change_id
    alert_id = df.loc[mask, "alert_id"].iloc[0] if mask.any() else None
    df.loc[mask, "status"]      = status
    df.loc[mask, "reviewed_by"] = reviewer
    df.loc[mask, "reviewed_at"] = datetime.now(timezone.utc).isoformat()
    df.to_csv(OVERRIDES_CSV, index=False)
    write_log("override_review", {
        "change_id": change_id,
        "decision":  status,
        "reviewer":  reviewer,
    }, alert_id=alert_id)


def get_approved_overrides() -> dict:
    """Return {alert_id: {field: new_value}} for all approved overrides."""
    df = load_overrides()
    if df.empty:
        return {}
    result = {}
    for _, row in df[df["status"] == "approved"].iterrows():
        result.setdefault(row["alert_id"], {})[row["field_changed"]] = row["new_value"]
    return result


@st.cache_data
def load_source_tables(cache_key: float) -> dict:
    """Load every project table as a dict of string-typed DataFrames.

    This is exactly the `source` contract src.verifier.verify_claim expects:
    a dict of DataFrames keyed by table name, all string-typed.
    """
    def read(path):
        return pd.read_csv(path, dtype=str, keep_default_na=False)

    return {
        "customers": read(CUSTOMERS_CSV),
        "transactions": read(TRANSACTIONS_CSV),
        "alerts": read(ALERTS_CSV),
        "evidence_items": read(EVIDENCE_CSV),
        "prior_cases": read(PRIOR_CASES_CSV),
        "kyc_profile_status": read(KYC_STATUS_CSV),
        "ai_outputs": read(AI_OUTPUTS_CSV),
        "human_reviews": read(HUMAN_REVIEWS_CSV),
    }


def mtimes_key() -> float:
    """Cache-busting key: sum of mtimes of all source CSVs."""
    return sum(p.stat().st_mtime for p in REQUIRED)



def case_readiness_pct(alert_id: str, source: dict) -> int:
    """Share of this alert's expected evidence items that are on file."""
    ev = source["evidence_items"]
    rows = ev[ev["alert_id"] == alert_id]
    if len(rows) == 0:
        return 0
    available = sum(1 for v in rows["available"] if str(v).strip().lower() == "true")
    return round(100 * available / len(rows))


def build_queue_row(alert_row: pd.Series, source: dict) -> dict:
    alert_id = alert_row["alert_id"]
    customers = source["customers"]
    crow = customers[customers["customer_id"] == alert_row["customer_id"]]
    customer_name = crow.iloc[0]["name"] if len(crow) else alert_row["customer_id"]

    ai_rows = source["ai_outputs"][source["ai_outputs"]["alert_id"] == alert_id]
    has_ai = len(ai_rows) > 0

    review_rows = source["human_reviews"][source["human_reviews"]["alert_id"] == alert_id]
    analyst = review_rows.iloc[0]["reviewer"] if len(review_rows) else "Unassigned"

    return {
        "alert_id": alert_id,
        "customer_id": alert_row["customer_id"],
        "customer": customer_name,
        "rule": alert_row["rule_triggered"],
        "severity": SEVERITY_LABELS.get(alert_row["severity"], alert_row["severity"]),
        "readiness": case_readiness_pct(alert_id, source),
        "ai": has_ai,
        "status": STATUS_LABELS.get(alert_row["status"], alert_row["status"]),
        "analyst": analyst,
    }


def get_case_detail(alert_id: str, source: dict) -> dict:
    """Assemble a case file for one alert straight from the real tables."""
    alerts = source["alerts"]
    arow = alerts[alerts["alert_id"] == alert_id].iloc[0]
    customer_id = arow["customer_id"]

    customers = source["customers"]
    crow = customers[customers["customer_id"] == customer_id]
    crow = crow.iloc[0] if len(crow) else None

    prior = source["prior_cases"]
    prow = prior[prior["customer_id"] == customer_id]
    prior_sar = int(prow.iloc[0]["prior_sar_count"]) if len(prow) else 0

    kyc = source["kyc_profile_status"]
    krow = kyc[kyc["customer_id"] == customer_id]
    kyc_current = krow.iloc[0]["current_within_12mo"] if len(krow) else "unknown"

    txns = source["transactions"]
    trows = txns[txns["customer_id"] == customer_id].to_dict("records")

    ev = source["evidence_items"]
    erows = ev[ev["alert_id"] == alert_id].to_dict("records")
    missing = [e["item_type"] for e in erows if str(e["available"]).strip().lower() != "true"]

    claims = source["ai_outputs"][source["ai_outputs"]["alert_id"] == alert_id].to_dict("records")
    ai_claims = []
    for c in claims:
        c = dict(c)
        c["evidence_refs"] = json.loads(c["evidence_refs"]) if c.get("evidence_refs") else []
        result = verifier.verify_claim(c, source)
        ai_claims.append({
            "id": c["claim_id"],
            "type": c["claim_type"],
            "asserted_value": c["asserted_value"],
            "result": result.status,
            "note": result.reason,
        })

    review_rows = source["human_reviews"][source["human_reviews"]["alert_id"] == alert_id]
    review = review_rows.iloc[0].to_dict() if len(review_rows) else None

    return {
        "alert": arow.to_dict(),
        "customer": crow.to_dict() if crow is not None else {},
        "prior_sar": prior_sar,
        "kyc_current_within_12mo": kyc_current,
        "transactions": trows,
        "evidence_items": erows,
        "missing": missing,
        "ai_claims": ai_claims,
        "review": review,
        "readiness": case_readiness_pct(alert_id, source),
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
source = load_source_tables(mtimes_key())
alerts_df = source["alerts"]

queue_df = pd.DataFrame([build_queue_row(r, source) for _, r in alerts_df.iterrows()])
if queue_df.empty:
    st.warning("No alerts to display. Check that data/alerts.csv has content.")

# Apply approved overrides (severity/status only — the only queue fields that
# are real schema columns) on top of the display copy.
approved = get_approved_overrides()
display_df = queue_df.copy()
for aid, fields in approved.items():
    for field, val in fields.items():
        display_df.loc[display_df["alert_id"] == aid, field] = val

# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = f"SESSION-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

if "current_user" not in st.session_state:
    st.session_state.current_user = ANALYSTS["EMP-003"]

if "view_as"           not in st.session_state: st.session_state.view_as           = "Analyst"
if "edit_mode"         not in st.session_state: st.session_state.edit_mode         = False
if "staged_edits"      not in st.session_state: st.session_state.staged_edits      = {}
if "edit_row"          not in st.session_state: st.session_state.edit_row          = None
if "selected_alert"    not in st.session_state: st.session_state.selected_alert    = None
if "case_search"       not in st.session_state: st.session_state.case_search       = "— select —"
if "settings_cl"       not in st.session_state: st.session_state.settings_cl       = []

# A clicked alert row is an <a href="?alert=<id>"> link (no JS — Streamlit
# strips onclick from injected HTML). The URL query param is the single source
# of truth for which case is open, so a row click and the "Open case file"
# selectbox are the same action.
_qp_alert = st.query_params.get("alert")
st.session_state.selected_alert = (
    _qp_alert if _qp_alert in alerts_df["alert_id"].values else None
)

if "risk_settings" not in st.session_state:
    st.session_state.risk_settings = {
        "high_threshold":             80,
        "medium_threshold":           50,
        "kyc_staleness_months":       12,
        "txn_history_days":           90,
        "require_counterparty_id":    True,
        "require_prior_sar_check":    True,
        "ai_draft_requires_readiness":True,
        "block_rubber_stamp":         True,
    }

if "keywords" not in st.session_state:
    st.session_state.keywords = {
        "Rapid Movement":             ["same-day transfer", "pass-through", "wire in wire out"],
        "Structuring":                ["just under 10k", "smurfing", "CTR avoidance", "split deposits"],
        "Expected Activity Mismatch": ["unexpected wire", "income inconsistent", "student account"],
        "High-Risk Jurisdiction":     ["sanctioned country", "OFAC", "high-risk region"],
        "KYC Drift":                  ["stale profile", "no update", "4 year gap"],
        "Unusual Volume":             ["volume spike", "sudden increase", "above monthly avg"],
        "Prior SAR History":          ["prior SAR", "previous filing", "repeat subject"],
    }

# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;500;600;700&family=Source+Serif+4:wght@400;600&display=swap');
*,html,body,[class*="css"]{font-family:'Source Sans 3',Arial,sans-serif !important;box-sizing:border-box;}
.stApp{background:#d6d6d6;}
.stMainBlockContainer{padding:0 !important;max-width:100% !important;}

.id-bar{background:#2b2b2b;padding:6px 20px;display:flex;justify-content:space-between;align-items:center;}
.id-bar-logo{font-family:'Source Serif 4',Georgia,serif;font-size:22px;font-weight:400;color:#fff;letter-spacing:-0.01em;}
.id-bar-logo span{color:#8ab4d4;}
.id-bar-right{font-size:11px;color:#aaa;display:flex;gap:16px;align-items:center;}
.id-bar-right a{color:#c8dff0;text-decoration:none;}
.id-bar-sep{color:#555;}
.id-bar-user{background:#3a3a3a;border:1px solid #555;padding:3px 10px;border-radius:2px;color:#e0e0e0 !important;font-size:11px;}

.sub-nav{background:#3d3d3d;padding:0 20px;display:flex;align-items:center;justify-content:space-between;height:28px;}
.sub-nav-left{font-size:11px;color:#c8c8c8;}
.sub-nav-right{font-size:11px;color:#f5a623;}

.page-body{padding:16px 20px;}

.role-bar{background:#fff8e1;border-bottom:2px solid #f0c040;padding:6px 20px;display:flex;align-items:center;gap:12px;font-size:11px;color:#5d4000;}

.metric-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:2px;margin-bottom:14px;}
.metric-cell{background:#fff;padding:12px 16px;border:1px solid #b0b0b0;border-top:3px solid #1a5276;}
.metric-cell-label{font-size:10px;font-weight:700;color:#555;letter-spacing:.08em;text-transform:uppercase;margin-bottom:4px;}
.metric-cell-value{font-size:26px;font-weight:700;line-height:1;color:#1a1a1a;font-variant-numeric:tabular-nums;}
.metric-cell-value.danger{color:#8b0000;}
.metric-cell-value.warn{color:#7d4e00;}
.metric-cell-value.info{color:#1a5276;}
.metric-cell-sub{font-size:10px;color:#888;margin-top:3px;}

.panel{background:#fff;border:1px solid #b0b0b0;margin-bottom:14px;}
.panel-header{background:linear-gradient(to bottom,#e8e8e8,#d8d8d8);border-bottom:1px solid #b0b0b0;padding:7px 14px;display:flex;justify-content:space-between;align-items:center;}
.panel-title{font-size:12px;font-weight:700;color:#1a1a1a;}
.panel-subtitle{font-size:11px;color:#666;}

.data-table{width:100%;border-collapse:collapse;font-size:12px;}
.data-table thead tr{background:linear-gradient(to bottom,#e0e8f0,#c8d8e8);}
.data-table th{padding:7px 12px;text-align:left;font-size:11px;font-weight:700;color:#1a3a5c;border-right:1px solid #b8ccd8;border-bottom:2px solid #8aaabf;white-space:nowrap;}
.data-table th:last-child{border-right:none;}
.data-table td{padding:8px 12px;border-bottom:1px solid #e8e8e8;border-right:1px solid #f0f0f0;color:#1a1a1a;vertical-align:middle;}
.data-table td:last-child{border-right:none;}
.data-table tbody tr:nth-child(even) td{background:#f4f7fa;}
.data-table tbody tr:nth-child(odd) td{background:#fff;}
.data-table tbody tr:hover td{background:#ddeeff !important;cursor:pointer;}
.data-table tbody tr.selected td{background:#c8dcf0 !important;border-left:3px solid #1a5276;}
.data-table tbody tr.has-pending td{border-left:3px solid #f0c040;}

.badge{display:inline-block;padding:2px 7px;font-size:10px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;border-radius:2px;border:1px solid;}
.b-high{background:#fde8e8;color:#7b0000;border-color:#c88;}
.b-medium{background:#fef3e2;color:#6b3800;border-color:#dba;}
.b-low{background:#e8f5e8;color:#1a5c1a;border-color:#9c9;}
.b-pending{background:#e8eeff;color:#1a2e8c;border-color:#99a;}
.b-progress{background:#e8f5e8;color:#1a5c1a;border-color:#9c9;}
.b-closed{background:#f0f0f0;color:#555;border-color:#bbb;}
.b-staged{background:#fff8e1;color:#7d4e00;border-color:#f0c040;}

.rb{display:flex;align-items:center;gap:6px;}
.rb-t{width:70px;height:8px;background:#ddd;border:1px solid #bbb;border-radius:1px;overflow:hidden;}
.rb-v{font-size:11px;font-weight:700;color:#333;min-width:30px;font-variant-numeric:tabular-nums;}

.edit-form{background:#f8f9fa;border:1px solid #b8ccd8;border-left:4px solid #1a5276;padding:14px 16px;margin:6px 0 10px 0;}
.edit-form-title{font-size:11px;font-weight:700;color:#1a5276;letter-spacing:.08em;text-transform:uppercase;margin-bottom:12px;}

.case-panel{background:#fff;border:1px solid #b0b0b0;margin-top:14px;}
.case-panel-hdr{background:linear-gradient(to bottom,#1a5276,#154360);padding:8px 14px;display:flex;justify-content:space-between;align-items:center;}
.case-panel-title{font-size:13px;font-weight:700;color:#fff;}
.case-panel-id{font-size:11px;color:#a8c4e0;}
.case-grid{display:grid;grid-template-columns:1fr 1fr;}
.case-section{padding:14px 16px;border-right:1px solid #e8e8e8;border-bottom:1px solid #e8e8e8;}
.case-section:nth-child(even){border-right:none;}
.case-section-title{font-size:10px;font-weight:700;color:#1a5276;letter-spacing:.1em;text-transform:uppercase;margin-bottom:10px;padding-bottom:5px;border-bottom:1px solid #d0e0ec;}
.field-row{display:flex;justify-content:space-between;margin-bottom:6px;font-size:12px;}
.field-lbl{color:#666;font-weight:500;}
.field-val{color:#1a1a1a;font-weight:600;text-align:right;}
.verify-row{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;margin:3px 0;background:#f8f8f8;border:1px solid #e8e8e8;font-size:12px;}
.v-pass{color:#1a5c1a;font-weight:700;font-size:10px;background:#e8f5e8;padding:2px 6px;border:1px solid #9c9;border-radius:2px;}
.v-fail{color:#7b0000;font-weight:700;font-size:10px;background:#fde8e8;padding:2px 6px;border:1px solid #c88;border-radius:2px;}
.v-review{color:#6b3800;font-weight:700;font-size:10px;background:#fef3e2;padding:2px 6px;border:1px solid #dba;border-radius:2px;}
.warn-box{background:#fff8e1;border:1px solid #f0c040;border-left:4px solid #f0c040;padding:8px 12px;font-size:12px;color:#5d4000;margin:8px 0 0 0;}

/* Claim card — the contradiction is the centerpiece: AI asserted X /
   evidence shows Y / result Z, all in explicit dark text. */
.claim-card{background:#fff;border:1px solid #d8d8d8;border-left:5px solid #999;margin:0 0 12px 0;}
.claim-card.fail{border-left-color:#b03a2e;}
.claim-card.pass{border-left-color:#1e8449;}
.claim-card.review{border-left-color:#b9770e;}
.claim-line{display:flex;gap:12px;padding:8px 14px;font-size:13px;align-items:baseline;border-bottom:1px solid #ececec;}
.claim-line .claim-tag{font-size:9px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#777;min-width:110px;flex-shrink:0;}
.claim-line .claim-val{color:#1a1a1a !important;font-weight:600;}
.claim-result-line{display:flex;justify-content:space-between;align-items:center;padding:9px 14px;background:#f2f2f2;}
.claim-result-line .claim-tag{font-size:9px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:#555;}

.settings-section-title{font-size:11px;font-weight:700;color:#1a5276;letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;}
.field-desc-txt{font-size:11px;color:#333;margin:2px 0 8px 0;line-height:1.4;}
.kw-chip{display:inline-block;padding:2px 8px;background:#e8eeff;color:#1a2e8c;border:1px solid #99aacc;border-radius:2px;font-size:11px;font-weight:500;margin:2px;}

.log-tbl{width:100%;border-collapse:collapse;font-size:12px;}
.log-tbl th{padding:7px 12px;background:#e0e8f0;border-bottom:2px solid #8aaabf;font-size:10px;font-weight:700;color:#1a3a5c;text-transform:uppercase;letter-spacing:.08em;text-align:left;}
.log-tbl td{padding:7px 12px;border-bottom:1px solid #eee;color:#333;}
.log-tbl tbody tr:nth-child(even) td{background:#f7f9fc;}
.lt-change{background:#fef3e2;color:#6b3800;border:1px solid #dba;padding:1px 6px;border-radius:2px;font-size:10px;font-weight:700;}
.lt-add{background:#e8f5e8;color:#1a5c1a;border:1px solid #9c9;padding:1px 6px;border-radius:2px;font-size:10px;font-weight:700;}
.lt-remove{background:#fde8e8;color:#7b0000;border:1px solid #c88;padding:1px 6px;border-radius:2px;font-size:10px;font-weight:700;}

div[data-testid="stTabs"]>div:first-child{background:linear-gradient(to bottom,#e0e0e0,#cccccc) !important;border-bottom:2px solid #999 !important;padding:0 20px !important;gap:0 !important;}
button[data-baseweb="tab"]{font-size:12px !important;font-weight:600 !important;color:#333 !important;padding:9px 18px !important;border-radius:0 !important;background:transparent !important;border-bottom:3px solid transparent !important;}
button[data-baseweb="tab"][aria-selected="true"]{background:#fff !important;color:#1a3a5c !important;border-bottom:3px solid #1a5276 !important;}
div[data-testid="stTabs"]>div:nth-child(2){background:#d6d6d6 !important;}
div[data-testid="stNumberInput"] input{background:#fff !important;border:1px solid #999 !important;border-radius:2px !important;font-size:13px !important;color:#111 !important;font-weight:600 !important;}
div[data-testid="stTextInput"] input{background:#fff !important;border:1px solid #999 !important;border-radius:2px !important;font-size:12px !important;color:#111 !important;}
div[data-testid="stSelectbox"]>div>div{background:#fff !important;border:1px solid #999 !important;border-radius:2px !important;font-size:12px !important;color:#111 !important;}
.stCheckbox label{font-size:12px !important;color:#1a1a1a !important;}
.stCheckbox label p{color:#1a1a1a !important;}
/* In Streamlit 1.58 the widget-label testid sits on a <label> element (not a
   <div>), so target the label directly — this is what darkens the Risk
   Settings field labels ("High severity trigger", "KYC staleness limit"…). */
label[data-testid="stWidgetLabel"],
label[data-testid="stWidgetLabel"] *,
label[data-testid="stWidgetLabel"] p{
  color:#1a1a1a !important;
  -webkit-text-fill-color:#1a1a1a !important;
  font-weight:700 !important;
  font-size:12px !important;
  opacity:1 !important;
}
/* Keep native popovers/menus and the dataframe grid on the light theme,
   regardless of the OS/browser dark-mode preference. */
div[data-testid="stMultiSelect"] div[data-baseweb="select"]>div{background:#fff !important;color:#111 !important;border:1px solid #999 !important;}
ul[data-baseweb="menu"],ul[role="listbox"]{background:#fff !important;}
ul[data-baseweb="menu"] li,li[role="option"]{background:#fff !important;color:#111 !important;}
div[data-testid="stDataFrame"]{background:#fff !important;}
div[data-testid="stDataFrame"] [data-testid="stTable"]{background:#fff !important;}
.stButton>button{border-radius:3px !important;font-size:11px !important;font-weight:700 !important;letter-spacing:.04em !important;border:1px solid !important;}
.stButton>button[kind="primary"]{background:linear-gradient(to bottom,#2166a8,#1a5276) !important;color:#fff !important;border-color:#154360 !important;}
.stButton>button[kind="secondary"]{background:linear-gradient(to bottom,#f0f0f0,#e0e0e0) !important;color:#333 !important;border-color:#aaa !important;}
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# TOP BAR
# ─────────────────────────────────────────────────────────────────────────────
emp = st.session_state.current_user
st.markdown(f"""
<div class="id-bar">
  <div class="id-bar-logo">Lumen <span>Verify</span></div>
  <div class="id-bar-right">
    <span class="id-bar-user">👤 {emp['name']} · {emp['rank']} · {emp['id']}</span>
    <span class="id-bar-sep">|</span>
    <a href="#">Financial Crime Compliance</a>
    <span class="id-bar-sep">|</span>
    <a href="#">Help</a>
    <span class="id-bar-sep">|</span>
    <a href="#">Log Out</a>
  </div>
</div>
<div class="sub-nav">
  <span class="sub-nav-left">AML Decision Workbench &nbsp;·&nbsp; Analyst Queue</span>
  <span class="sub-nav-right">
    Session: {st.session_state.session_id}
    &nbsp;·&nbsp;
    {datetime.now(timezone.utc).strftime('%d-%b-%Y %H:%M')} UTC
  </span>
</div>
""", unsafe_allow_html=True)

# Role switcher
ov_df         = load_overrides()
pending_count = len(ov_df[ov_df["status"] == "pending"]) if not ov_df.empty else 0

rs_col1, rs_col2, rs_col3 = st.columns([4, 1, 1])
with rs_col1:
    st.markdown(
        f'<div class="role-bar">⚠ Demo mode — viewing as: '
        f'<b>{st.session_state.view_as}</b>. '
        f'Switch role to access manager functions.</div>',
        unsafe_allow_html=True,
    )
with rs_col2:
    if st.button(
        "→ Analyst" if st.session_state.view_as == "Manager" else "→ Manager",
        width="stretch",
    ):
        st.session_state.view_as = (
            "Analyst" if st.session_state.view_as == "Manager" else "Manager"
        )
        st.rerun()
with rs_col3:
    if pending_count:
        st.markdown(
            f'<div style="padding:6px 0;font-size:11px;color:#7b0000;font-weight:700">'
            f'⏳ {pending_count} pending override(s)</div>',
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "  Alert Queue  ",
    "  Manager Review  ",
    "  Risk Settings  ",
    "  Change Log  ",
    "  Audit Trail  ",
])

# ═════════════════════════════════════════════════════════════════════════════
# CASE FILE — modal popup (PeopleSoft-style window)
# ═════════════════════════════════════════════════════════════════════════════
@st.dialog("Case File", width="large")
def show_case_dialog(alert_id: str, source: dict) -> None:
    case = get_case_detail(alert_id, source)
    c = case["customer"]
    a = case["alert"]

    # Case header
    st.markdown(f"""
    <div class="case-panel-hdr" style="border:1px solid #b0b0b0;">
      <span class="case-panel-title">{c.get('name', a['customer_id'])} — {a['rule_triggered']}</span>
      <span class="case-panel-id">{a['alert_id']} &nbsp;·&nbsp; {a['customer_id']}</span>
    </div>
    """, unsafe_allow_html=True)

    # HERO: AI Claims & Verification — the contradiction is the centerpiece,
    # read as "AI asserted X / evidence shows Y / result Z" in dark text.
    def _claim_card(cl):
        cls = "pass" if cl["result"] == "PASS" else "fail" if cl["result"] == "FAIL" else "review"
        badge = f'v-{cls}'
        return (
            f'<div class="claim-card {cls}">'
            f'<div class="claim-line"><span class="claim-tag">AI Asserted</span>'
            f'<span class="claim-val">{cl["type"]} = {cl["asserted_value"]}</span></div>'
            f'<div class="claim-line"><span class="claim-tag">Evidence Shows</span>'
            f'<span class="claim-val">{cl["note"]}</span></div>'
            f'<div class="claim-result-line"><span class="claim-tag">Verification Result</span>'
            f'<span class="{badge}" style="font-size:12px;padding:3px 9px;">{cl["result"]}</span></div>'
            f'</div>'
        )

    claim_rows = "".join(_claim_card(cl) for cl in case["ai_claims"]) \
        or '<div class="field-desc-txt">No AI claims drafted for this alert.</div>'

    st.markdown(f"""
    <div class="case-panel" style="margin-top:12px;">
      <div class="case-panel-hdr"><span class="case-panel-title">⚖ AI Claim Verification</span></div>
      <div style="padding:14px 16px;background:#f5f5f5;">{claim_rows}</div>
    </div>
    """, unsafe_allow_html=True)

    if case["missing"]:
        st.markdown(
            f'<div class="warn-box">Missing evidence: {", ".join(case["missing"])}</div>',
            unsafe_allow_html=True,
        )

    # Supporting detail: customer profile + transactions
    st.markdown(f"""
    <div class="case-panel" style="margin-top:12px;">
      <div class="case-grid">
        <div class="case-section">
          <div class="case-section-title">Customer Profile</div>
          <div class="field-row"><span class="field-lbl">Country</span><span class="field-val">{c.get('country','—')}</span></div>
          <div class="field-row"><span class="field-lbl">Occupation</span><span class="field-val">{c.get('occupation','—')}</span></div>
          <div class="field-row"><span class="field-lbl">KYC Status</span><span class="field-val">{c.get('kyc_status','—')}</span></div>
          <div class="field-row"><span class="field-lbl">KYC Current (12mo)</span><span class="field-val">{case['kyc_current_within_12mo']}</span></div>
          <div class="field-row"><span class="field-lbl">Prior SAR Count</span><span class="field-val" style="color:{'#8b0000' if case['prior_sar'] > 0 else '#1a5c1a'};font-weight:700;">{case['prior_sar']}</span></div>
          <div class="field-row"><span class="field-lbl">Case Readiness</span><span class="field-val">{case['readiness']}%</span></div>
        </div>
        <div class="case-section">
          <div class="case-section-title">Transactions</div>
          {''.join(
              f'<div class="field-row"><span class="field-lbl">{t["txn_id"]} · {t["timestamp"]}</span>'
              f'<span class="field-val">{t["direction"]} {t["amount"]} ({t["counterparty_country"]})</span></div>'
              for t in case["transactions"]
          ) or '<div class="field-desc-txt">No transactions on file.</div>'}
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    if case["review"]:
        rv = case["review"]
        st.markdown(f"""
        <div class="case-panel" style="margin-top:12px;">
          <div class="case-panel-hdr"><span class="case-panel-title">Human Review</span></div>
          <div style="padding:14px 16px;font-size:12px">
            <b>{rv.get('reviewer','—')}</b> — {rv.get('draft_disposition','—')}
            &nbsp;·&nbsp; evidence reviewed: {rv.get('evidence_reviewed','—')}<br/>
            <b>Reason:</b> {rv.get('decision_reason') or '<i>(none recorded)</i>'}<br/>
            <b>Final note:</b> {rv.get('final_note') or '<i>(none recorded)</i>'}
          </div>
        </div>
        """, unsafe_allow_html=True)

    if st.button("Close", key="close_case_dialog"):
        st.session_state.selected_alert = None
        st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — ALERT QUEUE
# ═════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)

    total     = len(display_df)
    high_c    = len(display_df[display_df["severity"] == "High"])
    pending_c = len(display_df[display_df["status"]   == "Pending Review"])
    avg_ready = int(display_df["readiness"].mean()) if total else 0

    st.markdown(f"""
    <div class="metric-strip">
      <div class="metric-cell">
        <div class="metric-cell-label">High Severity</div>
        <div class="metric-cell-value danger">{high_c}</div>
        <div class="metric-cell-sub">Require immediate review</div>
      </div>
      <div class="metric-cell">
        <div class="metric-cell-label">Pending Review</div>
        <div class="metric-cell-value warn">{pending_c}</div>
        <div class="metric-cell-sub">Awaiting analyst action</div>
      </div>
      <div class="metric-cell">
        <div class="metric-cell-label">Total Alerts</div>
        <div class="metric-cell-value info">{total}</div>
        <div class="metric-cell-sub">Active in queue</div>
      </div>
      <div class="metric-cell">
        <div class="metric-cell-label">Avg Case Readiness</div>
        <div class="metric-cell-value">{avg_ready}%</div>
        <div class="metric-cell-sub">Evidence completeness</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(f"""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Alert Queue — {emp['name']}</span>
        <span class="panel-subtitle">Select an alert below to open its case file &nbsp;·&nbsp; {total} alerts &nbsp;·&nbsp; {datetime.now(timezone.utc).strftime('%d %b %Y')}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    def badge_html(text, cls):
        return (
            f'<span style="display:inline-block;padding:2px 7px;font-size:10px;'
            f'font-weight:700;letter-spacing:.05em;text-transform:uppercase;'
            f'border-radius:2px;border:1px solid;{cls}">{text}</span>'
        )

    SEV_STYLE = {
        "High":   "background:#fde8e8;color:#7b0000;border-color:#c88;",
        "Medium": "background:#fef3e2;color:#6b3800;border-color:#dba;",
        "Low":    "background:#e8f5e8;color:#1a5c1a;border-color:#9c9;",
    }
    STA_STYLE = {
        "Pending Review": "background:#e8eeff;color:#1a2e8c;border-color:#99a;",
        "In Progress":    "background:#e8f5e8;color:#1a5c1a;border-color:#9c9;",
        "Closed":         "background:#f0f0f0;color:#555;border-color:#bbb;",
    }
    STAGED_BADGE = (
        '<span style="display:inline-block;margin-left:4px;padding:1px 5px;'
        'font-size:9px;font-weight:700;background:#fff8e1;color:#7d4e00;'
        'border:1px solid #f0c040;border-radius:2px;">STAGED</span>'
    )

    pending_alert_ids = set()
    if not ov_df.empty:
        pending_alert_ids = set(ov_df[ov_df["status"] == "pending"]["alert_id"])

    sel = st.session_state.selected_alert

    # Case search + severity filter/sort, all at the TOP so you don't scroll
    # past the whole queue to look up a case. The case search doubles as the
    # "Open case file" control and shares one mechanism with row clicks: both
    # write the ?alert= query param.
    all_alert_ids = display_df["alert_id"].tolist()
    fc0, fc1, fc2 = st.columns([3, 2, 2])
    with fc0:
        options = ["— select —"] + all_alert_ids
        want = st.session_state.selected_alert if st.session_state.selected_alert in all_alert_ids else "— select —"
        if st.session_state.get("case_search", "— select —") != want:
            st.session_state.case_search = want

        def _pick_case():
            v = st.session_state.case_search
            if v == "— select —":
                st.query_params.pop("alert", None)
            else:
                st.query_params["alert"] = v

        st.selectbox("Open case file", options, key="case_search", on_change=_pick_case)
    with fc1:
        severity_filter = st.multiselect(
            "Filter by severity",
            ["High", "Medium", "Low"],
            default=[],
            key="severity_filter",
        )
    with fc2:
        sort_order = st.selectbox(
            "Sort by severity",
            ["Queue order", "High to Low", "Low to High"],
            key="severity_sort",
        )

    if severity_filter:
        display_df = display_df[display_df["severity"].isin(severity_filter)]

    SEV_RANK = {"High": 0, "Medium": 1, "Low": 2}
    if sort_order == "High to Low":
        display_df = display_df.sort_values(
            by="severity", key=lambda s: s.map(SEV_RANK)
        )
    elif sort_order == "Low to High":
        display_df = display_df.sort_values(
            by="severity", key=lambda s: s.map(SEV_RANK), ascending=False
        )

    rows_html = ""
    for i, (_, r) in enumerate(display_df.iterrows()):
        is_sel   = sel == r["alert_id"]
        has_pend = r["alert_id"] in pending_alert_ids
        row_style = (
            "background:#c8dcf0;border-left:3px solid #1a5276;" if is_sel else
            "border-left:3px solid #f0c040;" if has_pend else ""
        )

        v    = int(r["readiness"])
        rcol = "#8b0000" if v < 50 else "#7d4e00" if v < 75 else "#1a5c1a"
        rb_html = (
            f'<div style="display:flex;align-items:center;gap:6px;">'
            f'<div style="width:70px;height:8px;background:#ddd;border:1px solid #bbb;'
            f'border-radius:1px;overflow:hidden;">'
            f'<div style="width:{v}%;height:100%;background:{rcol};"></div></div>'
            f'<span style="font-size:11px;font-weight:700;color:#333;'
            f'min-width:30px;">{v}%</span></div>'
        )

        ai_html = (
            '<span style="color:#1a5c1a;font-size:11px;font-weight:700;">&#9679; AI</span>'
            if r["ai"]
            else '<span style="color:#ccc;font-size:11px;">&#8212;</span>'
        )

        sev_html = badge_html(r["severity"], SEV_STYLE.get(r["severity"], ""))
        sta_html = badge_html(r["status"], STA_STYLE.get(r["status"], ""))
        ana_html = f'<span style="color:#555;font-size:11px;">{r["analyst"]}</span>'
        if has_pend:
            ana_html += '&nbsp;<span style="font-size:11px;">&#8987;</span>'

        # Each cell is an <a href="?alert=<id>"> link (no JS — Streamlit strips
        # onclick). Padding lives on the <a> so clicking anywhere in the cell
        # navigates and opens that case file.
        def _cell(content, extra=""):
            return (
                f'<td><a href="?alert={r["alert_id"]}" target="_self" '
                f'style="display:block;padding:8px 12px;text-decoration:none;'
                f'color:inherit;{extra}">{content}</a></td>'
            )

        rows_html += (
            f'<tr style="{row_style}">'
            + _cell(r["alert_id"], "font-weight:700;color:#1a5276;font-size:11px;")
            + _cell(r["customer"], "font-weight:600;")
            + _cell(r["rule"])
            + _cell(sev_html)
            + _cell(rb_html)
            + _cell(ai_html)
            + _cell(sta_html)
            + _cell(ana_html)
            + '</tr>'
        )

    table_html = f"""
<style>
  .lv-table {{ width:100%; border-collapse:collapse; font-size:12px; font-family:'Source Sans 3',Arial,sans-serif; }}
  .lv-table thead tr {{ background: linear-gradient(to bottom, #e0e8f0, #c8d8e8); }}
  .lv-table th {{ padding:7px 12px; text-align:left; font-size:11px; font-weight:700;
       color:#1a3a5c; border-right:1px solid #b8ccd8;
       border-bottom:2px solid #8aaabf; white-space:nowrap; }}
  .lv-table th:last-child {{ border-right:none; }}
  .lv-table td {{ padding:0; border-bottom:1px solid #e8e8e8;
       border-right:1px solid #f0f0f0; color:#1a1a1a; vertical-align:middle; background:#fff; }}
  .lv-table td:last-child {{ border-right:none; }}
  .lv-table td a {{ color:inherit; }}
  .lv-table tbody tr:nth-child(even) td {{ background:#f4f7fa; }}
  .lv-table tbody tr:nth-child(odd) td {{ background:#fff; }}
  .lv-table tbody tr:hover td {{ background:#ddeeff !important; cursor:pointer; }}
</style>
<table class="lv-table">
  <thead><tr>
    <th>Alert ID</th><th>Customer</th><th>Rule Triggered</th>
    <th>Severity</th><th>Case Readiness</th><th>AI</th>
    <th>Status</th><th>Analyst</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>"""

    # Rendered via st.markdown (not st.html) so the <a> row links navigate the
    # page — st.html sandboxes the table in an iframe and links wouldn't work.
    st.markdown(table_html, unsafe_allow_html=True)

    # Case search + row clicks both set ?alert=<id>, read into selected_alert
    # at the top of the script; open the popup case file for it.
    if st.session_state.selected_alert:
        show_case_dialog(st.session_state.selected_alert, source)

    st.markdown('</div>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — MANAGER REVIEW
# ═════════════════════════════════════════════════════════════════════════════
with tab2:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)

    if st.session_state.view_as != "Manager":
        st.markdown("""
        <div class="warn-box" style="margin:0 0 14px 0">
          🔒 Manager Review is only accessible in Manager view.
          Use the <b>→ Manager</b> button at the top of the page.
        </div>""", unsafe_allow_html=True)
    else:
        st.markdown("""
        <div class="panel">
          <div class="panel-header">
            <span class="panel-title">Pending Override Requests</span>
            <span class="panel-subtitle">
              Review and approve or reject analyst-submitted changes
            </span>
          </div>
        </div>""", unsafe_allow_html=True)

        ov_fresh   = load_overrides()
        pending_ov = (
            ov_fresh[ov_fresh["status"] == "pending"]
            if not ov_fresh.empty else pd.DataFrame()
        )

        if pending_ov.empty:
            st.markdown(
                '<div style="background:#fff;border:1px solid #b0b0b0;'
                'padding:20px;font-size:12px;color:#888">'
                'No pending override requests.</div>',
                unsafe_allow_html=True,
            )
        else:
            for _, row in pending_ov.iterrows():
                st.markdown(f"""
                <div style="background:#fff;border:1px solid #b0b0b0;
                            border-left:4px solid #f0c040;padding:14px 16px;margin-bottom:10px">
                  <div style="display:flex;justify-content:space-between;margin-bottom:8px">
                    <span style="font-size:12px;font-weight:700">{row['change_id']}</span>
                    <span style="font-size:11px;color:#888">{row['changed_at']}</span>
                  </div>
                  <div style="font-size:12px;color:#333;margin-bottom:6px">
                    <b>Alert:</b> {row['alert_id']} &nbsp;·&nbsp;
                    <b>Field:</b> {row['field_changed']} &nbsp;·&nbsp;
                    <b>From:</b>
                    <span style="color:#8b0000">{row['old_value']}</span>
                    &nbsp;→&nbsp;
                    <b>To:</b>
                    <span style="color:#1a5c1a">{row['new_value']}</span>
                  </div>
                  <div style="font-size:11px;color:#555;margin-bottom:4px">
                    <b>Submitted by:</b> {row['changed_by_name']} ({row['changed_by_id']})
                  </div>
                  <div style="font-size:11px;color:#333">
                    <b>Reason:</b> {row['reason']}
                  </div>
                </div>""", unsafe_allow_html=True)

                mc1, mc2, _ = st.columns([1, 1, 5])
                with mc1:
                    if st.button("✓ Approve", key=f"apr_{row['change_id']}", type="primary"):
                        update_override_status(
                            row["change_id"], "approved",
                            st.session_state.current_user["name"],
                        )
                        st.success("Approved — change is now live.")
                        st.rerun()
                with mc2:
                    if st.button("✕ Reject", key=f"rej_{row['change_id']}", type="secondary"):
                        update_override_status(
                            row["change_id"], "rejected",
                            st.session_state.current_user["name"],
                        )
                        st.warning("Rejected.")
                        st.rerun()

        if not ov_fresh.empty:
            st.markdown("""
            <div class="panel" style="margin-top:14px">
              <div class="panel-header">
                <span class="panel-title">Override History</span>
                <span class="panel-subtitle">All submitted overrides</span>
              </div>
            </div>""", unsafe_allow_html=True)
            st.dataframe(ov_fresh, width="stretch", hide_index=True)

    st.markdown('</div>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — RISK SETTINGS
# ═════════════════════════════════════════════════════════════════════════════
with tab3:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Risk Configuration</span>
        <span class="panel-subtitle">Changes are logged to the shared audit trail (data/audit_log.csv)</span>
      </div>
    </div>""", unsafe_allow_html=True)

    rs   = st.session_state.risk_settings
    rc1, rc2 = st.columns(2, gap="large")

    with rc1:
        # st.container(border=True) draws the bordered box AND holds the widgets
        # inside it — unlike raw <div> wrappers, which Streamlit renders as an
        # empty box while the widget lands outside (the "empty white box" bug).
        with st.container(border=True):
            st.markdown('<div class="settings-section-title">Severity Thresholds</div>', unsafe_allow_html=True)
            st.markdown('<p class="field-desc-txt"><b>High threshold</b> — alerts at or above this require Senior Analyst / Manager review (Ryan severity matrix §10).</p>', unsafe_allow_html=True)
            new_high   = st.number_input("High severity trigger (≥)",   50, 100,         rs["high_threshold"],       5,  key="ni_high")
            st.markdown('<p class="field-desc-txt"><b>Medium threshold</b> — alerts between this and High get standard analyst review.</p>', unsafe_allow_html=True)
            new_medium = st.number_input("Medium severity trigger (≥)", 20, int(new_high)-5, min(rs["medium_threshold"], new_high-5), 5, key="ni_med")

        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown('<div class="settings-section-title">Case Readiness Gates</div>', unsafe_allow_html=True)
            st.markdown('<p class="field-desc-txt"><b>KYC staleness limit</b> — profiles older than this (months) fail the readiness check. Default: 12.</p>', unsafe_allow_html=True)
            new_kyc    = st.number_input("KYC staleness limit (months)",         3,  36,  rs["kyc_staleness_months"],  3,  key="ni_kyc")
            st.markdown('<p class="field-desc-txt"><b>Transaction history</b> — minimum days required before AI can draft. Default: 90.</p>', unsafe_allow_html=True)
            new_txn    = st.number_input("Transaction history required (days)",  30, 180, rs["txn_history_days"],      30, key="ni_txn")
            st.markdown('<br>', unsafe_allow_html=True)
            new_cpty   = st.checkbox("Require counterparty identification", value=rs["require_counterparty_id"])
            new_sar    = st.checkbox("Require prior SAR history check",     value=rs["require_prior_sar_check"])

    with rc2:
        with st.container(border=True):
            st.markdown('<div class="settings-section-title">AI & Governance Controls</div>', unsafe_allow_html=True)
            st.markdown('<p class="field-desc-txt"><b>Block AI draft until readiness passes</b> — core build principle. Disabling violates the governance posture.</p>', unsafe_allow_html=True)
            new_ai_gate = st.checkbox("Block AI draft until readiness check passes", value=rs["ai_draft_requires_readiness"])
            st.markdown('<p class="field-desc-txt"><b>Anti-rubber-stamp gate</b> — Hero Moment 2 (§13). All decision fields required before saving.</p>', unsafe_allow_html=True)
            new_rubber  = st.checkbox("Enforce anti-rubber-stamp gate", value=rs["block_rubber_stamp"])

        st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown('<div class="settings-section-title">Typology Keywords</div>', unsafe_allow_html=True)
            st.markdown('<p class="field-desc-txt">Keywords per AML typology used by the AI drafter for claim matching.</p>', unsafe_allow_html=True)
            rule_choice = st.selectbox(
                "Typology", list(st.session_state.keywords.keys()),
                label_visibility="collapsed",
            )
            current_kws = st.session_state.keywords[rule_choice]
            chips = "".join(f'<span class="kw-chip">{k}</span>' for k in current_kws)
            st.markdown(
                chips or '<span style="color:#999;font-size:11px">None defined.</span>',
                unsafe_allow_html=True,
            )
            st.markdown('<br>', unsafe_allow_html=True)
            new_kw = st.text_input(
                "Add keyword",
                placeholder="e.g. layering, shell company",
                key="kw_input",
                label_visibility="collapsed",
            )
            kc1, kc2 = st.columns(2)
            with kc1:
                if st.button("＋ Add Keyword", use_container_width=True):
                    if new_kw.strip() and new_kw.strip() not in current_kws:
                        st.session_state.keywords[rule_choice].append(new_kw.strip())
                        st.session_state.settings_cl.append({
                            "time":   datetime.now(timezone.utc).strftime("%H:%M:%S"),
                            "type":   "Keyword Added",
                            "detail": f'"{new_kw.strip()}" → {rule_choice}',
                        })
                        write_log("keyword_added", {"typology": rule_choice, "keyword": new_kw.strip()})
                        st.rerun()
            with kc2:
                kw_del = st.selectbox(
                    "Remove", ["— remove —"] + current_kws,
                    label_visibility="collapsed", key="kw_remove",
                )
                if kw_del != "— remove —":
                    if st.button("✕ Remove", use_container_width=True):
                        st.session_state.keywords[rule_choice].remove(kw_del)
                        st.session_state.settings_cl.append({
                            "time":   datetime.now(timezone.utc).strftime("%H:%M:%S"),
                            "type":   "Keyword Removed",
                            "detail": f'"{kw_del}" ← {rule_choice}',
                        })
                        write_log("keyword_removed", {"typology": rule_choice, "keyword": kw_del})
                        st.rerun()

    st.markdown('<br>', unsafe_allow_html=True)
    if st.button("Save Risk Settings", type="primary"):
        mapping = [
            ("high_threshold",             new_high,    "High threshold"),
            ("medium_threshold",           new_medium,  "Medium threshold"),
            ("kyc_staleness_months",       new_kyc,     "KYC staleness (months)"),
            ("txn_history_days",           new_txn,     "Txn history (days)"),
            ("require_counterparty_id",    new_cpty,    "Require counterparty ID"),
            ("require_prior_sar_check",    new_sar,     "Require SAR check"),
            ("ai_draft_requires_readiness",new_ai_gate, "Block AI on incomplete cases"),
            ("block_rubber_stamp",         new_rubber,  "Anti-rubber-stamp gate"),
        ]
        changes = []
        for key, nv, lbl in mapping:
            if nv != rs[key]:
                changes.append(f"{lbl}: {rs[key]} → {nv}")
                st.session_state.risk_settings[key] = nv
        if changes:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            for c in changes:
                st.session_state.settings_cl.append({"time": ts, "type": "Setting Changed", "detail": c})
            write_log("risk_settings_saved", {"changes": changes})
            st.success(f"{len(changes)} setting(s) saved and logged to the audit trail.")
        else:
            st.info("No changes to save.")
    st.markdown('</div>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — CHANGE LOG
# ═════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Configuration Change Log</span>
        <span class="panel-subtitle">
          In-session setting and keyword changes · Persistent record in the Audit Trail tab
        </span>
      </div>
    </div>""", unsafe_allow_html=True)

    scl = st.session_state.settings_cl
    if not scl:
        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;'
            'padding:20px;font-size:12px;color:#888">No changes this session.</div>',
            unsafe_allow_html=True,
        )
    else:
        tt   = {"Setting Changed":"lt-change","Keyword Added":"lt-add","Keyword Removed":"lt-remove"}
        rows = "".join(
            f'<tr>'
            f'<td style="color:#888;font-variant-numeric:tabular-nums">{e["time"]}</td>'
            f'<td><span class="{tt.get(e["type"],"")}">{e["type"]}</span></td>'
            f'<td>{e["detail"]}</td>'
            f'</tr>'
            for e in reversed(scl)
        )
        st.markdown(
            f'<table class="log-tbl">'
            f'<thead><tr><th>Time</th><th>Type</th><th>Detail</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>',
            unsafe_allow_html=True,
        )
        st.markdown('<br>', unsafe_allow_html=True)
        if st.button("Clear Session Log", type="secondary"):
            st.session_state.settings_cl = []
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — AUDIT TRAIL  (data/audit_log.csv via src.audit)
# ═════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Audit Trail</span>
        <span class="panel-subtitle">
          The one append-only log for the whole project — data/audit_log.csv, written by src.audit.log_event
        </span>
      </div>
    </div>""", unsafe_allow_html=True)

    if not AUDIT_LOG_CSV.exists():
        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;'
            'padding:20px;font-size:12px;color:#888">'
            'No audit entries yet. Actions taken in this workbench '
            '(overrides, settings changes, claim verification) write rows here automatically.</div>',
            unsafe_allow_html=True,
        )
    else:
        audit_df = pd.read_csv(AUDIT_LOG_CSV, dtype=str, keep_default_na=False)
        st.markdown(
            f'<div style="font-size:11px;color:#555;margin-bottom:10px">'
            f'{len(audit_df)} entries in data/audit_log.csv</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(
            audit_df.sort_values("timestamp", ascending=False),
            width="stretch",

            hide_index=True,
        )
    st.markdown('</div>', unsafe_allow_html=True)
