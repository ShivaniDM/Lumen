"""
generate_data.py
Run once to create the synthetic data files for Lumen Verify.

Usage:
    python generate_data.py

Creates:
    data/alerts.csv
    data/employees.csv

Does NOT touch:
    data/pending_overrides.csv  (created by app.py at runtime)
    logs/                       (created by app.py at runtime)
"""

import csv
import os
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# ── alerts.csv ────────────────────────────────────────────────────────────────
ALERTS = [
    {
        "id":        "ALT-001",
        "customer":  "Jane Doe",
        "rule":      "Rapid Movement",
        "severity":  "High",
        "readiness": 62,
        "ai":        True,
        "status":    "Pending Review",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-002",
        "customer":  "John Smith",
        "rule":      "Large Cash Deposit",
        "severity":  "Medium",
        "readiness": 80,
        "ai":        True,
        "status":    "In Progress",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-003",
        "customer":  "Maria Garcia",
        "rule":      "Structuring",
        "severity":  "High",
        "readiness": 45,
        "ai":        False,
        "status":    "Pending Review",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-004",
        "customer":  "David Kim",
        "rule":      "Wire Transfer Pattern",
        "severity":  "Low",
        "readiness": 91,
        "ai":        True,
        "status":    "Closed",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-005",
        "customer":  "Priya Patel",
        "rule":      "Unusual Volume",
        "severity":  "High",
        "readiness": 33,
        "ai":        True,
        "status":    "Pending Review",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-006",
        "customer":  "Carlos Ruiz",
        "rule":      "Rapid Movement",
        "severity":  "Medium",
        "readiness": 71,
        "ai":        True,
        "status":    "In Progress",
        "analyst":   "S. Mayekar",
    },
    {
        "id":        "ALT-007",
        "customer":  "Aisha Okonkwo",
        "rule":      "High-Risk Jurisdiction Transfer",
        "severity":  "High",
        "readiness": 55,
        "ai":        False,
        "status":    "Pending Review",
        "analyst":   "S. Mayekar",
    },
]

# ── employees.csv ─────────────────────────────────────────────────────────────
EMPLOYEES = [
    {
        "id":      "EMP-001",
        "name":    "L. Pagan",
        "display": "L. Pagan",
        "rank":    "Lead Analyst",
        "team":    "AML Core",
        "active":  True,
    },
    {
        "id":      "EMP-002",
        "name":    "R. Rydalch",
        "display": "R. Rydalch",
        "rank":    "Analyst",
        "team":    "AML Core",
        "active":  True,
    },
    {
        "id":      "EMP-003",
        "name":    "S. Mayekar",
        "display": "S. Mayekar",
        "rank":    "Analyst",
        "team":    "AML Core",
        "active":  True,
    },
    {
        "id":      "EMP-004",
        "name":    "S. Azeez",
        "display": "S. Azeez",
        "rank":    "Analyst",
        "team":    "AML Core",
        "active":  True,
    },
    {
        "id":      "EMP-005",
        "name":    "J. Torres",
        "display": "J. Torres",
        "rank":    "Senior Analyst",
        "team":    "AML Review",
        "active":  True,
    },
    {
        "id":      "EMP-006",
        "name":    "M. Chen",
        "display": "M. Chen",
        "rank":    "Compliance Manager",
        "team":    "Compliance",
        "active":  True,
    },
    {
        "id":      "EMP-007",
        "name":    "A. Okafor",
        "display": "A. Okafor",
        "rank":    "Compliance Officer",
        "team":    "Compliance",
        "active":  True,
    },
]

# ── Write files ───────────────────────────────────────────────────────────────
def write_csv(path, rows, fieldnames):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Created: {path}  ({len(rows)} rows)")

alerts_path    = DATA_DIR / "alerts.csv"
employees_path = DATA_DIR / "employees.csv"

print("\nLumen Verify — Data Generator")
print("=" * 40)

write_csv(
    alerts_path,
    ALERTS,
    ["id","customer","rule","severity","readiness","ai","status","analyst"],
)
write_csv(
    employees_path,
    EMPLOYEES,
    ["id","name","display","rank","team","active"],
)

print("\nDone. Run 'streamlit run app.py' to start the app.")
print("=" * 40)