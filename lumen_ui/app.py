"""
app.py — Lumen Verify AML Decision Workbench
UI only. Reads from data/ CSVs. Writes to data/pending_overrides.csv and logs/.

Run:
    streamlit run app.py

Requires (run once first):
    python generate_data.py
"""

import streamlit as st
import pandas as pd
import json
from datetime import datetime
from pathlib import Path

st.set_page_config(
    page_title="Lumen Verify | AML Workbench",
    layout="wide",
    page_icon="",
)

# ── Paths (app.py only owns these two) ───────────────────────────────────────
LOGS_DIR      = Path("logs")
OVERRIDES_CSV = Path("data") / "pending_overrides.csv"
ALERTS_CSV    = Path("data") / "alerts.csv"
EMPLOYEES_CSV = Path("data") / "employees.csv"

LOGS_DIR.mkdir(exist_ok=True)

# ── Guard: check data files exist ────────────────────────────────────────────
if not ALERTS_CSV.exists() or not EMPLOYEES_CSV.exists():
    st.error(
        "Data files not found. "
        "Run `python generate_data.py` in your terminal first, then refresh."
    )
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def write_log(action_type: str, details: dict) -> dict:
    """Write one JSON file to logs/ for every auditable action."""
    emp = st.session_state.get("current_user", {})
    ts  = datetime.utcnow()
    log = {
        "log_id":        f"LOG-{ts.strftime('%Y%m%d-%H%M%S')}-{emp.get('id','UNKNOWN')}",
        "timestamp":     ts.isoformat() + "Z",
        "employee_id":   emp.get("id",   "UNKNOWN"),
        "employee_name": emp.get("name", "Unknown"),
        "employee_rank": emp.get("rank", "Unknown"),
        "action_type":   action_type,
        "session_id":    st.session_state.get("session_id", "SESSION-UNKNOWN"),
    }
    log.update(details)
    fname = (
        f"{ts.strftime('%Y-%m-%d_%H-%M-%S')}"
        f"_{emp.get('id','UNK')}"
        f"_{action_type}.json"
    )
    with open(LOGS_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    return log


def load_overrides() -> pd.DataFrame:
    cols = [
        "change_id", "alert_id", "field_changed", "old_value", "new_value",
        "changed_by_id", "changed_by_name", "changed_at", "reason",
        "status", "reviewed_by", "reviewed_at",
    ]
    if OVERRIDES_CSV.exists():
        return pd.read_csv(OVERRIDES_CSV)
    return pd.DataFrame(columns=cols)


def save_override(alert_id, field, old_val, new_val, reason) -> str:
    df  = load_overrides()
    emp = st.session_state.current_user
    ts  = datetime.utcnow().isoformat() + "Z"
    cid = f"CHG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{alert_id}"
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
        "alert_id":        alert_id,
        "field_changed":   field,
        "old_value":       str(old_val),
        "new_value":       str(new_val),
        "reason":          reason,
        "override_status": "pending_manager_review",
    })
    return cid


def update_override_status(change_id: str, status: str, reviewer: str) -> None:
    df = load_overrides()
    mask = df["change_id"] == change_id
    df.loc[mask, "status"]      = status
    df.loc[mask, "reviewed_by"] = reviewer
    df.loc[mask, "reviewed_at"] = datetime.utcnow().isoformat() + "Z"
    df.to_csv(OVERRIDES_CSV, index=False)
    write_log("override_review", {
        "change_id": change_id,
        "decision":  status,
        "reviewer":  reviewer,
    })


def get_approved_overrides() -> dict:
    """Return {alert_id: {field: new_value}} for all approved overrides."""
    df = load_overrides()
    if df.empty:
        return {}
    result = {}
    for _, row in df[df["status"] == "approved"].iterrows():
        result.setdefault(row["alert_id"], {})[row["field_changed"]] = row["new_value"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────────────────────────
alerts_df    = pd.read_csv(ALERTS_CSV)
employees_df = pd.read_csv(EMPLOYEES_CSV)

emp_options = (
    employees_df[employees_df["active"] == True]
    .apply(lambda r: f"{r['id']} — {r['display']} ({r['rank']})", axis=1)
    .tolist()
)

# Build display copy with approved overrides applied on top
approved   = get_approved_overrides()
display_df = alerts_df.copy()
for aid, fields in approved.items():
    for field, val in fields.items():
        display_df.loc[display_df["id"] == aid, field] = val

# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state.session_id = f"SESSION-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

if "current_user" not in st.session_state:
    st.session_state.current_user = {
        "id": "EMP-003", "name": "S. Mayekar",
        "rank": "Analyst", "team": "AML Core",
    }

if "view_as"           not in st.session_state: st.session_state.view_as           = "Analyst"
if "edit_mode"         not in st.session_state: st.session_state.edit_mode         = False
if "staged_edits"      not in st.session_state: st.session_state.staged_edits      = {}
if "edit_row"          not in st.session_state: st.session_state.edit_row          = None
if "selected_alert"    not in st.session_state: st.session_state.selected_alert    = None
if "settings_cl"       not in st.session_state: st.session_state.settings_cl       = []

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
# CASE DETAIL DATA  (static demo data — will be replaced by synthetic CSVs)
# ─────────────────────────────────────────────────────────────────────────────
CASE_DETAILS = {
    "ALT-001": {
        "customer": "Jane Doe", "rule": "Rapid Movement", "severity": "High",
        "readiness": 62, "status": "Pending Review", "kyc_status": "Current",
        "prior_sar": 0, "prior_alerts": 2, "risk_rating": "Medium-High",
        "account_type": "Checking", "expected_activity": "$5,000/mo domestic",
        "kyc_last_updated": "Mar 2025",
        "transactions": [
            {"id":"T-014","date":"2026-05-01","amount":"$48,200","type":"Wire In", "counterparty":"Offshore LLC"},
            {"id":"T-018","date":"2026-05-01","amount":"$47,800","type":"Wire Out","counterparty":"Shell Co. BVI"},
            {"id":"T-021","date":"2026-05-01","amount":"$12,000","type":"Wire Out","counterparty":"Unknown"},
        ],
        "ai_claims": [
            {"id":"C1","type":"prior_sar_history","result":"FAIL",
             "note":"AI asserts prior SAR exists. Source: prior_sar_count = 0. HERO CASE — approval blocked."},
            {"id":"C2","type":"rapid_movement","result":"PASS",
             "note":"T-014, T-018, T-021 confirm same-day in/out pattern."},
            {"id":"C3","type":"expected_activity_mismatch","result":"PASS",
             "note":"$108k vs $5k/mo expected — confirmed mismatch."},
        ],
        "missing": ["Counterparty identification incomplete for T-021"],
        "ai_summary": (
            "Account received three large incoming wires totalling $108,200 on 2026-05-01 "
            "followed by same-day outgoing transfers of $59,800. Pattern inconsistent with "
            "expected activity. AI incorrectly asserted prior SAR history — flagged FAIL."
        ),
    },
    "ALT-003": {
        "customer": "Maria Garcia", "rule": "Structuring", "severity": "High",
        "readiness": 45, "status": "Pending Review", "kyc_status": "Stale (4 yrs)",
        "prior_sar": 1, "prior_alerts": 0, "risk_rating": "High",
        "account_type": "Business Checking", "expected_activity": "$3,000/mo cash",
        "kyc_last_updated": "Jan 2022",
        "transactions": [
            {"id":"T-031","date":"2026-04-28","amount":"$9,800","type":"Cash Deposit","counterparty":"Branch A"},
            {"id":"T-032","date":"2026-04-29","amount":"$9,500","type":"Cash Deposit","counterparty":"Branch B"},
            {"id":"T-033","date":"2026-04-30","amount":"$9,200","type":"Cash Deposit","counterparty":"Branch C"},
        ],
        "ai_claims": [
            {"id":"C1","type":"structuring","result":"PASS",
             "note":"3 deposits across 3 branches, all under $10k CTR threshold."},
            {"id":"C2","type":"stale_kyc_profile","result":"PASS",
             "note":"KYC last updated Jan 2022 — exceeds 12-month staleness threshold."},
            {"id":"C3","type":"prior_sar_history","result":"PASS",
             "note":"prior_sar_count = 1 confirmed."},
        ],
        "missing": ["Updated KYC profile required before final disposition"],
        "ai_summary": (
            "Three cash deposits totalling $28,500 at separate branches on consecutive days, "
            "each below $10,000 CTR threshold. Consistent with structuring typology. "
            "KYC stale 4 years. Prior SAR on file."
        ),
    },
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
.rb-f{height:100%;}
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

.settings-section-title{font-size:11px;font-weight:700;color:#1a5276;letter-spacing:.08em;text-transform:uppercase;margin-bottom:10px;}
.field-desc-txt{font-size:11px;color:#666;margin:2px 0 8px 0;line-height:1.4;}
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
    {datetime.utcnow().strftime('%d-%b-%Y %H:%M')} UTC
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
        use_container_width=True,
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
    "  Logs Explorer  ",
])

# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — ALERT QUEUE
# ═════════════════════════════════════════════════════════════════════════════
with tab1:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)

    total     = len(display_df)
    high_c    = len(display_df[display_df["severity"] == "High"])
    pending_c = len(display_df[display_df["status"]   == "Pending Review"])
    avg_ready = int(display_df["readiness"].mean())

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

    # Panel header
    edit_mode = False  # Edit mode disabled for submission version
    st.markdown(f"""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Alert Queue — {emp['name']}</span>
        <span class="panel-subtitle">Select an alert below to open its case file &nbsp;·&nbsp; {total} alerts &nbsp;·&nbsp; {datetime.utcnow().strftime('%d %b %Y')}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Badge helpers
    def sev_b(s):
        c = {"High":"b-high","Medium":"b-medium","Low":"b-low"}.get(s, "")
        return f'<span class="badge {c}">{s}</span>'

    def sta_b(s):
        c = {"Pending Review":"b-pending","In Progress":"b-progress","Closed":"b-closed"}.get(s, "")
        return f'<span class="badge {c}">{s}</span>'

    def rb(v):
        col = "#8b0000" if v < 50 else "#7d4e00" if v < 75 else "#1a5c1a"
        return (
            f'<div class="rb">'
            f'<div class="rb-t"><div class="rb-f" style="width:{v}%;background:{col}"></div></div>'
            f'<span class="rb-v">{v}%</span></div>'
        )

    def ai_i(v):
        return (
            '<span style="color:#1a5c1a;font-size:11px;font-weight:700">● AI</span>'
            if v else
            '<span style="color:#ccc;font-size:11px">—</span>'
        )

    # Alerts with pending overrides
    pending_alert_ids = set()
    if not ov_df.empty:
        pending_alert_ids = set(ov_df[ov_df["status"] == "pending"]["alert_id"])

    sel = st.session_state.selected_alert

    # ── Build table HTML ──────────────────────────────────────────────────────
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

    rows_html = ""
    for i, (_, r) in enumerate(display_df.iterrows()):
        staged   = st.session_state.staged_edits.get(r["id"], {})
        d_sev    = staged.get("severity", r["severity"])
        d_sta    = staged.get("status",   r["status"])
        d_ana    = staged.get("analyst",  r["analyst"])
        is_sel   = sel == r["id"]
        has_pend = r["id"] in pending_alert_ids

        if is_sel:
            row_style = "background:#c8dcf0;border-left:3px solid #1a5276;"
        elif has_pend:
            row_style = "border-left:3px solid #f0c040;"
        else:
            row_style = ""

        # readiness bar
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

        # ai indicator
        ai_html = (
            '<span style="color:#1a5c1a;font-size:11px;font-weight:700;">&#9679; AI</span>'
            if str(r["ai"]).lower() in ("true","1","yes")
            else '<span style="color:#ccc;font-size:11px;">&#8212;</span>'
        )

        sev_html = badge_html(d_sev, SEV_STYLE.get(d_sev,""))
        if "severity" in staged:
            sev_html += STAGED_BADGE

        sta_html = badge_html(d_sta, STA_STYLE.get(d_sta,""))
        if "status" in staged:
            sta_html += STAGED_BADGE

        ana_html = f'<span style="color:#555;font-size:11px;">{d_ana}</span>'
        if "analyst" in staged:
            ana_html += STAGED_BADGE
        if has_pend:
            ana_html += '&nbsp;<span style="font-size:11px;">&#8987;</span>'

        rows_html += (
            f'<tr style="{row_style}">'
            f'<td style="font-weight:700;color:#1a5276;font-size:11px;">{r["id"]}</td>'
            f'<td style="font-weight:600;">{r["customer"]}</td>'
            f'<td>{r["rule"]}</td>'
            f'<td>{sev_html}</td>'
            f'<td>{rb_html}</td>'
            f'<td>{ai_html}</td>'
            f'<td>{sta_html}</td>'
            f'<td>{ana_html}</td>'
            f'</tr>'
        )

    table_html = f"""
<style>
  .lv-table {{ width:100%; border-collapse:collapse; font-size:12px; font-family:'Source Sans 3',Arial,sans-serif; }}
  .lv-table thead tr {{ background: linear-gradient(to bottom, #e0e8f0, #c8d8e8); }}
  .lv-table th {{ padding:7px 12px; text-align:left; font-size:11px; font-weight:700;
       color:#1a3a5c; border-right:1px solid #b8ccd8;
       border-bottom:2px solid #8aaabf; white-space:nowrap; }}
  .lv-table th:last-child {{ border-right:none; }}
  .lv-table td {{ padding:8px 12px; border-bottom:1px solid #e8e8e8;
       border-right:1px solid #f0f0f0; color:#1a1a1a; vertical-align:middle; background:#fff; }}
  .lv-table td:last-child {{ border-right:none; }}
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

    st.html(table_html)

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
            st.dataframe(ov_fresh, use_container_width=True, hide_index=True)

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
        <span class="panel-subtitle">Changes are logged and written to logs/</span>
      </div>
    </div>""", unsafe_allow_html=True)

    rs   = st.session_state.risk_settings
    rc1, rc2 = st.columns(2, gap="large")

    with rc1:
        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;'
            'padding:16px 18px;margin-bottom:14px">',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="settings-section-title">Severity Thresholds</div>', unsafe_allow_html=True)
        st.markdown('<p class="field-desc-txt"><b>High threshold</b> — alerts at or above this require Senior Analyst / Manager review (Ryan severity matrix §10).</p>', unsafe_allow_html=True)
        new_high   = st.number_input("High severity trigger (≥)",   50, 100,         rs["high_threshold"],       5,  key="ni_high")
        st.markdown('<p class="field-desc-txt"><b>Medium threshold</b> — alerts between this and High get standard analyst review.</p>', unsafe_allow_html=True)
        new_medium = st.number_input("Medium severity trigger (≥)", 20, int(new_high)-5, min(rs["medium_threshold"], new_high-5), 5, key="ni_med")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;padding:16px 18px">',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="settings-section-title">Case Readiness Gates</div>', unsafe_allow_html=True)
        st.markdown('<p class="field-desc-txt"><b>KYC staleness limit</b> — profiles older than this (months) fail the readiness check. Default: 12.</p>', unsafe_allow_html=True)
        new_kyc    = st.number_input("KYC staleness limit (months)",         3,  36,  rs["kyc_staleness_months"],  3,  key="ni_kyc")
        st.markdown('<p class="field-desc-txt"><b>Transaction history</b> — minimum days required before AI can draft. Default: 90.</p>', unsafe_allow_html=True)
        new_txn    = st.number_input("Transaction history required (days)",  30, 180, rs["txn_history_days"],      30, key="ni_txn")
        st.markdown('<br>', unsafe_allow_html=True)
        new_cpty   = st.checkbox("Require counterparty identification", value=rs["require_counterparty_id"])
        new_sar    = st.checkbox("Require prior SAR history check",     value=rs["require_prior_sar_check"])
        st.markdown('</div>', unsafe_allow_html=True)

    with rc2:
        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;'
            'padding:16px 18px;margin-bottom:14px">',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="settings-section-title">AI & Governance Controls</div>', unsafe_allow_html=True)
        st.markdown('<p class="field-desc-txt"><b>Block AI draft until readiness passes</b> — core build principle. Disabling violates the governance posture.</p>', unsafe_allow_html=True)
        new_ai_gate = st.checkbox("Block AI draft until readiness check passes", value=rs["ai_draft_requires_readiness"])
        st.markdown('<p class="field-desc-txt"><b>Anti-rubber-stamp gate</b> — Hero Moment 2 (§13). All decision fields required before saving.</p>', unsafe_allow_html=True)
        new_rubber  = st.checkbox("Enforce anti-rubber-stamp gate", value=rs["block_rubber_stamp"])
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;padding:16px 18px">',
            unsafe_allow_html=True,
        )
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
                        "time":   datetime.utcnow().strftime("%H:%M:%S"),
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
                        "time":   datetime.utcnow().strftime("%H:%M:%S"),
                        "type":   "Keyword Removed",
                        "detail": f'"{kw_del}" ← {rule_choice}',
                    })
                    write_log("keyword_removed", {"typology": rule_choice, "keyword": kw_del})
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

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
            ts = datetime.utcnow().strftime("%H:%M:%S")
            for c in changes:
                st.session_state.settings_cl.append({"time": ts, "type": "Setting Changed", "detail": c})
            write_log("risk_settings_saved", {"changes": changes})
            st.success(f"{len(changes)} setting(s) saved and logged to logs/")
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
          In-session setting and keyword changes · Persistent records in logs/
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
# TAB 5 — LOGS EXPLORER
# ═════════════════════════════════════════════════════════════════════════════
with tab5:
    st.markdown('<div class="page-body">', unsafe_allow_html=True)
    st.markdown("""
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Logs Explorer</span>
        <span class="panel-subtitle">
          Persistent JSON log files in logs/ · One file per action
        </span>
      </div>
    </div>""", unsafe_allow_html=True)

    log_files = sorted(LOGS_DIR.glob("*.json"), reverse=True)
    if not log_files:
        st.markdown(
            '<div style="background:#fff;border:1px solid #b0b0b0;'
            'padding:20px;font-size:12px;color:#888">'
            'No log files yet. Actions (dispositions, overrides, settings changes) '
            'write JSON files here automatically.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="font-size:11px;color:#555;margin-bottom:10px">'
            f'{len(log_files)} log file(s) in logs/</div>',
            unsafe_allow_html=True,
        )
        for lf in log_files[:20]:
            with open(lf, encoding="utf-8") as f:
                data = json.load(f)
            with st.expander(f"📄 {lf.name}", expanded=False):
                st.json(data)
    st.markdown('</div>', unsafe_allow_html=True)