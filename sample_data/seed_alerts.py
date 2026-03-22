#!/usr/bin/env python3
"""Seed alerts and notifications into GRCen after importing assets.

Usage:
    python sample_data/seed_alerts.py [DATABASE_URL]

Default DATABASE_URL: postgresql://grcen:grcen@localhost:5432/grcen
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg

DATABASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
    "DATABASE_URL", "postgresql://grcen:grcen@localhost:5432/grcen"
)

# Alerts to seed: (asset_name, asset_type, title, message, schedule_type, cron_expression, next_fire_at_offset_days, enabled)
# next_fire_at_offset_days is relative to "now" — positive = future, negative = past (overdue)
ALERTS = [
    # Policy reviews
    ("Acceptable Use Policy", "policy", "Annual AUP Review Due", "Acceptable Use Policy is due for annual review.", "recurring", "0 9 15 1 *", 300, True),
    ("Data Retention Policy", "policy", "Data Retention Policy Review", "Annual review of data retention policy is due.", "recurring", "0 9 1 3 *", -10, True),
    ("Incident Response Policy", "policy", "IR Policy Annual Review", "Incident Response Policy annual review.", "recurring", "0 9 1 6 *", 70, True),
    ("Access Control Policy", "policy", "Access Control Policy Review", "Annual review of access control policy.", "recurring", "0 9 1 4 *", 10, True),
    ("Information Security Policy", "policy", "InfoSec Policy Review Due", "Information Security Policy annual review.", "recurring", "0 9 1 7 *", 100, True),
    ("Vendor Management Policy", "policy", "Vendor Policy Review Due", "Vendor Management Policy annual review.", "recurring", "0 9 1 9 *", 160, True),
    ("Business Continuity Policy", "policy", "BCP Annual Review", "Business Continuity Policy annual review and tabletop exercise.", "recurring", "0 9 1 5 *", 40, True),
    ("Privacy Policy", "policy", "Privacy Policy Review", "Annual privacy policy review and GDPR alignment check.", "recurring", "0 9 1 11 *", 220, True),

    # Person reviews
    ("Alice Chen", "person", "CTO Performance Review", "Annual performance review for Alice Chen.", "recurring", "0 9 1 3 *", -15, True),
    ("Bob Martinez", "person", "VP Eng Performance Review", "Annual performance review for Bob Martinez.", "recurring", "0 9 15 4 *", 24, True),
    ("Carol Davies", "person", "SecOps Director Review", "Annual performance review for Carol Davies.", "recurring", "0 9 1 6 *", 70, True),
    ("Frank Osei", "person", "SRE Director Review", "Annual performance review for Frank Osei.", "recurring", "0 9 1 2 *", -40, True),
    ("Grace Liu", "person", "Security Analyst Review", "Annual review for Grace Liu.", "recurring", "0 9 15 5 *", 54, True),

    # Vendor assessments
    ("Okta", "vendor", "Okta Vendor Assessment Due", "Annual security assessment for Okta is due.", "recurring", "0 9 15 9 *", 175, True),
    ("Snowflake", "vendor", "Snowflake Assessment Due", "Annual vendor security assessment for Snowflake.", "recurring", "0 9 1 5 *", 40, True),
    ("AWS", "vendor", "AWS Vendor Assessment Due", "Annual security assessment for AWS.", "recurring", "0 9 1 6 *", 70, True),
    ("Stripe", "vendor", "Stripe PCI Assessment", "Annual PCI compliance verification for Stripe.", "recurring", "0 9 1 8 *", 130, True),
    ("CrowdStrike", "vendor", "CrowdStrike Assessment Due", "Annual vendor assessment for CrowdStrike — overdue.", "recurring", "0 9 1 3 *", -20, True),

    # Framework certifications
    ("SOC 2", "framework", "SOC 2 Certification Renewal", "SOC 2 Type II certification expires soon — begin renewal audit.", "once", None, 145, True),
    ("PCI DSS v4.0", "framework", "PCI DSS Recertification", "PCI DSS v4.0 certification renewal due.", "once", None, 190, True),
    ("GDPR", "framework", "GDPR Compliance Review", "Annual GDPR compliance posture review.", "recurring", "0 9 25 5 *", 64, True),
    ("ISO 27001:2022", "framework", "ISO 27001 Certification Target", "Target date for completing ISO 27001 certification.", "once", None, 370, True),

    # Control testing
    ("MFA Enforcement", "control", "MFA Control Test Due", "Quarterly MFA enforcement control testing.", "recurring", "0 9 15 */3 *", 54, True),
    ("Encryption at Rest", "control", "Encryption Control Test", "Quarterly encryption at rest verification.", "recurring", "0 9 10 */3 *", 19, True),
    ("Network Segmentation", "control", "Segmentation Test Due", "Semi-annual network segmentation penetration test.", "recurring", "0 9 1 */6 *", 70, True),
    ("WAF — Customer Portal", "control", "WAF Rule Review", "Quarterly WAF rule review and tuning.", "recurring", "0 9 1 */3 *", 10, True),
    ("Backup Verification", "control", "Backup Restore Test", "Monthly backup restoration verification.", "recurring", "0 9 1 * *", 9, True),

    # Process scheduling
    ("Quarterly Access Review", "process", "Q2 Access Review Start", "Begin Q2 quarterly access review.", "once", None, 24, True),
    ("Vulnerability Scanning", "process", "Weekly Vuln Scan Check", "Verify weekly vulnerability scan completed successfully.", "recurring", "0 10 * * 1", 3, True),
    ("Security Awareness Training", "process", "Annual Training Launch", "Launch annual security awareness training campaign.", "recurring", "0 9 15 11 *", 238, True),
    ("Penetration Testing", "process", "Annual Pentest Kickoff", "Schedule and kick off annual external penetration test.", "once", None, 60, True),
    ("Employee Onboarding", "process", "Onboarding Process Review", "Review and update onboarding checklist and automation.", "recurring", "0 9 1 */6 *", 70, True),
    ("Vendor Onboarding", "process", "Vendor Process Review", "Review vendor onboarding and assessment procedures.", "recurring", "0 9 1 */6 *", 100, True),
    ("Budget Planning Cycle", "process", "Q3 Budget Reforecast", "Begin Q3 budget reforecast cycle.", "once", None, 100, True),

    # Audit milestones
    ("SOC 2 Type II Audit 2025", "audit", "SOC 2 Audit Report Due", "SOC 2 Type II audit report expected from assessor.", "once", None, -5, True),
    ("ISO 27001 Gap Assessment 2026", "audit", "ISO Gap Assessment Kickoff", "Begin ISO 27001 gap assessment with BSI Group.", "once", None, 30, True),
    ("Annual Access Review 2026", "audit", "Access Review Deadline", "All access review findings must be remediated.", "once", None, 45, True),

    # System maintenance
    ("Production Kubernetes Cluster", "system", "K8s Version Upgrade Window", "Scheduled K8s version upgrade maintenance window.", "once", None, 14, True),
    ("CI/CD Pipeline", "system", "Jenkins Maintenance Restart", "Weekly Jenkins controller restart for memory management.", "recurring", "0 3 * * 0", 5, True),
    ("Staging Environment", "system", "Staging Refresh", "Monthly staging environment data refresh.", "recurring", "0 2 1 * *", 9, True),
    ("Secrets Manager", "system", "Vault Certificate Rotation", "Quarterly Vault TLS certificate rotation.", "recurring", "0 9 1 */3 *", 10, True),

    # Device lifecycle
    ("Developer Laptops", "device", "Laptop Refresh Cycle", "Annual hardware refresh — evaluate and replace aging laptops.", "recurring", "0 9 1 9 *", 160, True),

    # Data category reviews
    ("Customer PII", "data_category", "PII Data Mapping Review", "Annual review of customer PII data flows and storage locations.", "recurring", "0 9 1 6 *", 70, True),
    ("Payment Card Data", "data_category", "Cardholder Data Review", "Annual PCI scoping and cardholder data flow review.", "recurring", "0 9 1 10 *", 190, True),
]

# Notifications to seed (for alerts that have already fired): (alert_index, title, message, is_read, days_ago)
NOTIFICATIONS = [
    (8, "CTO Performance Review — Overdue", "Alice Chen's annual performance review was due 2026-03-07. Please schedule.", False, 15),
    (11, "SRE Director Review — Overdue", "Frank Osei's annual performance review was due 2026-02-01.", True, 40),
    (4, "Data Retention Policy Review — Overdue", "Data Retention Policy review was due 2026-03-01.", False, 10),
    (18, "CrowdStrike Assessment — Overdue", "CrowdStrike vendor assessment was due 2026-03-01.", False, 20),
    (35, "SOC 2 Audit Report — Overdue", "SOC 2 Type II audit report was expected from Deloitte.", False, 5),
    (29, "Weekly Vuln Scan OK", "Weekly vulnerability scan completed — 0 critical, 3 high, 12 medium findings.", True, 3),
    (29, "Weekly Vuln Scan OK", "Weekly vulnerability scan completed — 0 critical, 2 high, 14 medium findings.", True, 10),
]


async def main():
    pool = await asyncpg.create_pool(DATABASE_URL)

    # Build name+type -> id lookup
    rows = await pool.fetch("SELECT id, name, type FROM assets")
    asset_lookup: dict[tuple[str, str], uuid.UUID] = {}
    for r in rows:
        asset_lookup[(r["name"], r["type"])] = r["id"]

    if not asset_lookup:
        print("No assets found in database. Import assets first.")
        await pool.close()
        return

    created_alerts = 0
    created_notifs = 0
    alert_ids: list[uuid.UUID] = []
    now = datetime.now(timezone.utc)

    for asset_name, asset_type, title, message, sched_type, cron_expr, offset_days, enabled in ALERTS:
        key = (asset_name, asset_type)
        asset_id = asset_lookup.get(key)
        if not asset_id:
            print(f"  SKIP alert (asset not found): {asset_name} ({asset_type})")
            alert_ids.append(None)
            continue

        next_fire = now + timedelta(days=offset_days)
        aid = uuid.uuid4()
        await pool.execute(
            """INSERT INTO alerts (id, asset_id, title, message, schedule_type, cron_expression, next_fire_at, enabled)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT DO NOTHING""",
            aid, asset_id, title, message, sched_type, cron_expr, next_fire, enabled,
        )
        alert_ids.append(aid)
        created_alerts += 1

    for alert_idx, title, message, is_read, days_ago in NOTIFICATIONS:
        aid = alert_ids[alert_idx] if alert_idx < len(alert_ids) else None
        if not aid:
            continue
        await pool.execute(
            """INSERT INTO notifications (id, alert_id, title, message, read)
               VALUES ($1, $2, $3, $4, $5)""",
            uuid.uuid4(), aid, title, message, is_read,
        )
        created_notifs += 1

    print(f"Created {created_alerts} alerts and {created_notifs} notifications.")
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
