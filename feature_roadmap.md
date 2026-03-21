# GRCen Feature Roadmap

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
