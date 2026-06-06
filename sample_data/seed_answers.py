#!/usr/bin/env python3
"""Seed the answer library + one inbound questionnaire (feature #21).

The bundled sample CSVs predate the answer-library feature, so `/answers` and
`/questionnaires` start empty. This adds a realistic set of canonical Q&A
entries (`AssetType.ANSWER`) wired to existing seeded Control/Policy/Framework/
Audit assets via `substantiated_by`, plus one inbound questionnaire with some
questions pre-mapped to library answers and some left blank — so a usability-test
participant can exercise the reuse / auto-fill / freshness flow (plan Task 8).

It deliberately seeds three freshness states:
  • healthy   — backed by active substantiators (stays fresh)
  • degraded  — backed by a decommissioned (inactive) control (flags for review)
  • unbacked  — no substantiators at all (flags for review)

Run AFTER seed_data.py (it links to assets that script creates).

Usage:
    python sample_data/seed_answers.py [DATABASE_URL]

Env:
    DATABASE_URL    Postgres DSN (default: postgresql://grcen:grcen@localhost:5432/grcen)
    GRCEN_ORG_SLUG  Target org slug (default: the instance's default org)
"""
import asyncio
import json
import os
import sys
import uuid

import asyncpg

DATABASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv(
    "DATABASE_URL", "postgresql://grcen:grcen@localhost:5432/grcen"
)
DATABASE_URL = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")

SUBSTANTIATES_REL = "substantiated_by"

# (question, answer, short_answer, last_reviewed, [(substantiator_name, type), ...])
ANSWERS = [
    ("Do you enforce multi-factor authentication for all employees and administrators?",
     "Yes. MFA is enforced organization-wide through our identity provider for all "
     "employee and privileged/administrative access. Enrollment is mandatory and "
     "verified during quarterly access reviews.",
     "Yes", "2026-04-15",
     [("MFA Enforcement", "control"), ("Access Control Policy", "policy")]),

    ("Is customer data encrypted at rest?",
     "Yes. All customer data is encrypted at rest using AES-256. Key management is "
     "handled by our cloud provider's managed KMS with periodic rotation.",
     "Yes", "2026-04-15",
     [("Encryption at Rest", "control"), ("Data Classification Policy", "policy")]),

    ("Do you maintain a documented and tested incident response plan?",
     "Yes. We maintain a documented Incident Response Policy that is reviewed at "
     "least annually and exercised via tabletop simulations.",
     "Yes", "2026-03-20",
     [("Incident Response Policy", "policy"), ("Internal Security Audit Q1 2026", "audit")]),

    ("Are you SOC 2 compliant?",
     "Yes. We complete an annual SOC 2 Type II examination. The most recent report "
     "is available under NDA upon request.",
     "Yes — SOC 2 Type II", "2026-02-10",
     [("SOC 2", "framework"), ("SOC 2 Type II Audit 2025", "audit")]),

    ("Do you perform application security testing (SAST/DAST) and vulnerability scanning?",
     "Yes. Static and dynamic application security testing run in our CI pipeline, "
     "and we perform periodic vulnerability scans of production infrastructure.",
     "Yes", "2026-04-01",
     [("SAST/DAST Scanning", "control")]),

    ("Do you conduct periodic user access reviews?",
     "Yes. Access to systems and data is reviewed at least quarterly, with results "
     "documented and remediated.",
     "Yes — quarterly", "2026-04-15",
     [("Quarterly Access Review Control", "control"), ("Annual Access Review 2026", "audit")]),

    # Degraded: backed only by a decommissioned control -> should flag for review.
    ("Do you operate a Data Loss Prevention (DLP) solution?",
     "Yes. A DLP solution monitors egress of sensitive data across email and "
     "endpoints.",
     "Yes", "2025-09-01",
     [("Legacy DLP (decommissioned)", "control")]),

    # Unbacked: no substantiators -> should flag for review.
    ("Do you perform annual third-party penetration testing?",
     "Yes. An independent firm performs a network and application penetration test "
     "at least annually; findings are tracked to remediation.",
     "Yes — annually", "2025-08-15",
     []),
]

# One inbound questionnaire. Each question: (text, mapped_answer_question_or_None).
QUESTIONNAIRE_NAME = "Acme Corp — Vendor Security Assessment 2026"
QUESTIONNAIRE_SOURCE = "Acme Corp Procurement"
QUESTIONS = [
    # Pre-mapped (demonstrates auto-fill); maps by the answer's question text.
    ("Does your organization require multi-factor authentication?",
     "Do you enforce multi-factor authentication for all employees and administrators?"),
    ("Is data encrypted while stored (at rest)?",
     "Is customer data encrypted at rest?"),
    # Left blank for the participant to map/fill during the session.
    ("Do you have an incident response process?", None),
    ("Please describe your compliance certifications.", None),
    ("How often do you review user access?", None),
    ("Do you conduct independent penetration testing?", None),
]


async def main() -> int:
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        org_slug = os.getenv("GRCEN_ORG_SLUG", "").strip()
        if org_slug:
            org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = $1", org_slug)
            if org_id is None:
                print(f"Organization '{org_slug}' not found. Run `grcen createorg` first.")
                return 1
        else:
            org_id = await pool.fetchval("SELECT id FROM organizations WHERE slug = 'default'")
        print(f"Seeding answer library into organization_id={org_id}")

        existing = await pool.fetchval(
            "SELECT count(*) FROM assets WHERE type = 'answer' AND organization_id = $1", org_id
        )
        if existing:
            print(f"Found {existing} existing Answer assets — skipping to avoid duplicates.")
            return 0

        # name+type -> id lookup for substantiator resolution (scoped to org).
        lookup: dict[tuple[str, str], uuid.UUID] = {}
        for r in await pool.fetch(
            "SELECT id, name, type::text AS type FROM assets WHERE organization_id = $1", org_id
        ):
            lookup[(r["name"], r["type"])] = r["id"]

        # A decommissioned control so one answer demonstrates the degraded state.
        legacy_id = uuid.uuid4()
        await pool.execute(
            """INSERT INTO assets (id, type, name, description, status, metadata, organization_id)
               VALUES ($1, 'control', $2, $3, 'inactive', $4, $5)""",
            legacy_id,
            "Legacy DLP (decommissioned)",
            "Endpoint/email DLP tool retired in 2025; replacement not yet in place.",
            json.dumps({"effectiveness": "not_tested"}),
            org_id,
        )
        lookup[("Legacy DLP (decommissioned)", "control")] = legacy_id

        answers_created = subs_created = subs_missing = 0
        answer_ids: dict[str, uuid.UUID] = {}
        for question, answer, short, reviewed, subs in ANSWERS:
            aid = uuid.uuid4()
            await pool.execute(
                """INSERT INTO assets (id, type, name, description, status, metadata, organization_id)
                   VALUES ($1, 'answer', $2, $3, 'active', $4, $5)""",
                aid, question, answer,
                json.dumps({"short_answer": short, "answer_format": "boolean_plus_detail",
                            "last_reviewed": reviewed}),
                org_id,
            )
            answer_ids[question] = aid
            answers_created += 1
            for sub_name, sub_type in subs:
                target = lookup.get((sub_name, sub_type))
                if target is None:
                    print(f"  SKIP substantiator (not found): {sub_name} ({sub_type})")
                    subs_missing += 1
                    continue
                await pool.execute(
                    """INSERT INTO relationships
                         (id, source_asset_id, target_asset_id, relationship_type, description, organization_id)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    uuid.uuid4(), aid, target, SUBSTANTIATES_REL,
                    "Substantiates this answer", org_id,
                )
                subs_created += 1

        # One inbound questionnaire with mixed-state responses.
        qid = uuid.uuid4()
        await pool.execute(
            """INSERT INTO questionnaires (id, organization_id, name, source, due_date, status)
               VALUES ($1, $2, $3, $4, DATE '2026-07-15', 'in_progress')""",
            qid, org_id, QUESTIONNAIRE_NAME, QUESTIONNAIRE_SOURCE,
        )
        resp_filled = resp_blank = 0
        for pos, (qtext, mapped_q) in enumerate(QUESTIONS):
            rid = uuid.uuid4()
            mapped_id = answer_ids.get(mapped_q) if mapped_q else None
            if mapped_id is not None:
                filled = next(a for q, a, *_ in ANSWERS if q == mapped_q)
                await pool.execute(
                    """INSERT INTO questionnaire_responses
                         (id, questionnaire_id, organization_id, position, question_text,
                          answer_asset_id, filled_answer, status)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, 'filled')""",
                    rid, qid, org_id, pos, qtext, mapped_id, filled,
                )
                resp_filled += 1
            else:
                await pool.execute(
                    """INSERT INTO questionnaire_responses
                         (id, questionnaire_id, organization_id, position, question_text, status)
                       VALUES ($1, $2, $3, $4, $5, 'unanswered')""",
                    rid, qid, org_id, pos, qtext,
                )
                resp_blank += 1

        print(f"Answers: created {answers_created} "
              f"(substantiator links {subs_created}, missing {subs_missing})")
        print(f"Questionnaire '{QUESTIONNAIRE_NAME}': "
              f"{resp_filled} pre-filled + {resp_blank} blank responses")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
