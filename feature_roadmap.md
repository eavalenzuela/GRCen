# GRCen Feature Roadmap

## High Impact — Core GRC Gaps

### 1. Role-Based Access Control (RBAC)
Currently only "admin" exists. A GRC tool needs roles like Auditor (read-only), Risk Owner (edit own assets), Compliance Manager, etc. This is table-stakes for multi-user adoption.

### 2. Audit Trail / Change Log
Every asset and relationship change should be logged (who, what, when, old value, new value). Essential for compliance — auditors need to prove when a policy was updated or a risk was reviewed.

### 3. Risk Scoring & Heatmap Dashboard
Risk assets already have severity/likelihood/impact fields. Auto-calculate risk scores, display a heatmap matrix, and surface top risks on the dashboard.

### 4. Review Workflows
Periodic reviews (annual policy review, quarterly risk reassessment) are core GRC. Add a "last reviewed" / "next review due" field, tie it to alerts, and show overdue items prominently.

## Medium Impact — Usability

### 5. Advanced Search & Filtering
Current search is name-only ILIKE. Add filtering by custom field values, metadata, date ranges, and multi-asset-type search. A saved/bookmarked search would help repeat users.

### 6. Bulk Relationship Import from Graph View
Allow drag-and-drop relationship creation in the graph UI, and bulk CSV relationship creation with auto-matching.

### 7. Asset Templates / Cloning
Let users clone an existing asset (e.g., duplicate a policy with a new name) to speed up data entry.

### 8. Dashboard Widgets
The dashboard currently shows counts and recent items. Add: compliance coverage %, overdue reviews, open risks by severity, alerts due this week.

## Lower Priority — Polish & Scale

### 9. PDF/Report Generation
Export a compliance report for a specific scope (e.g., all assets related to a given Audit or Requirement) as a formatted PDF.

### 10. API Keys / REST API Documentation
For integration with external tools (ticketing, SIEM, CI/CD). Add OpenAPI docs and token-based API auth alongside session auth.

### 11. Tagging / Labeling System
Cross-cutting labels (e.g., "SOC2", "GDPR", "Q1-2026") that span asset types, enabling filtered views across the graph.

### 12. Notification Channels
Currently notifications are in-app only. Add email and/or webhook delivery for alerts.
