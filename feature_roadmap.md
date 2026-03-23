# GRCen Feature Roadmap

## High Impact - Key Features

### 1. Risk Management area
* Dedicated page for viewing and managing Risk objects
* Full risk register table with sortable columns: name, category, likelihood, impact, computed score, treatment, owner, and review status
* Interactive 5×5 heatmap (like the dashboard widget) but clickable — selecting a cell filters the register to risks in that likelihood/impact bucket
* Filter bar: filter by risk category, treatment type, control effectiveness, severity, owner, and overdue-review status
* Risk detail panel or inline expansion showing treatment plan, linked controls (via relationships), inherent vs. residual scores, and exception/acceptance status
* Bulk actions: bulk-update treatment, reassign owner, or set review dates across selected risks
* Summary statistics at the top: total active risks, count by severity band (critical/high/medium/low), overdue reviews, and risks with no assigned treatment
* Trend indicators: show whether risk counts per severity band have increased or decreased since last review cycle (based on review date history)


## Medium Impact — Usability

### 1. Drag-and-Drop Graph Relationships
The graph UI supports click-to-link relationship creation and bulk CSV/JSON import exists. Add drag-and-drop relationship creation in the graph view for a more intuitive experience.

### 2. Saved / Bookmarked Searches
Advanced search and filtering is implemented. Add the ability to save and recall frequent searches.

## Lower Priority — Polish & Scale

### 3. PDF/Report Generation
Export a compliance report for a specific scope (e.g., all assets related to a given Audit or Requirement) as a formatted PDF.

### 4. API Keys / REST API Documentation
For integration with external tools (ticketing, SIEM, CI/CD). Add OpenAPI docs and token-based API auth alongside session auth.

### 5. Tagging / Labeling System
Cross-cutting labels (e.g., "SOC2", "GDPR", "Q1-2026") that span asset types, enabling filtered views across the graph. Currently custom enum fields exist per asset type, but a universal tagging system would allow cross-type filtering.

### 6. Notification Channels
Currently notifications are in-app only. Add email and/or webhook delivery for alerts.
