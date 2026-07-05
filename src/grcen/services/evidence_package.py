"""Auditor evidence package (roadmap E2).

Bundles everything an auditor needs for one compliance framework into a single
ZIP — the assurance-side counterpart to the ingress connectors. GRCen already
exposes each piece piecemeal (a SoA CSV here, a control-library CSV there, a
sparkline of test results, one-file-at-a-time evidence downloads); this stitches
them into a self-describing package scoped to a framework and an optional audit
period:

    manifest.json                    — framework, period, generated_at, counts
    README.txt                       — human-readable contents guide
    statement_of_applicability.csv   — per-requirement scope + justification
    control_library.csv              — controls satisfying this framework
    control_test_ledger.csv          — every recorded test run (not just the
                                       latest) for those controls — the
                                       per-run ledger GRCen couldn't export before
    audit_trail.csv                  — change history for the framework's assets
    evidence/                        — the actual attached evidence files
    evidence/_index.csv              — index of every evidence item (files + links)

Everything is org-scoped through ``framework_service``; encrypted attachment
files are decrypted into the package (the download already does this, and the
package is itself an access-logged export). Built in-memory in one pass.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import asyncpg

from grcen.models.attachment import AttachmentKind
from grcen.services import attachment as attachment_service
from grcen.services import framework_service
from grcen.services.encryption import decrypt_bytes


@dataclass
class EvidencePackageResult:
    filename: str
    content: bytes
    framework_name: str
    counts: dict[str, int] = field(default_factory=dict)


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip()).strip("-")
    return s or "untitled"


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def _soa_csv(detail) -> str:
    return _csv(
        ["requirement", "applicable", "implementation_status", "satisfied",
         "coverage", "applicability_justification"],
        [[r.name, "yes" if r.applicable else "no", r.implementation_status or "",
          "yes" if r.satisfied else "no", r.coverage, r.applicability_justification or ""]
         for r in detail.requirements],
    )


def _control_csv(controls: list[dict]) -> str:
    rows = []
    for c in controls:
        meta = c.get("metadata") or {}
        reqs = c.get("requirements") or []
        rows.append([
            c["id"], c["name"], meta.get("effectiveness", ""),
            meta.get("last_tested", ""), meta.get("next_test_due", ""),
            len(reqs),
            "; ".join(
                f"{r['framework_name']}: {r['name']}" if r.get("framework_name") else r["name"]
                for r in reqs
            ),
        ])
    return _csv(
        ["control_id", "control_name", "effectiveness", "last_tested",
         "next_test_due", "requirement_count", "requirements"],
        rows,
    )


async def _framework_controls(pool, organization_id, control_ids) -> list[dict]:
    if not control_ids:
        return []
    idset = set(control_ids)
    allc = await framework_service.list_controls_with_coverage(
        pool, organization_id=organization_id
    )
    return [c for c in allc if c["id"] in idset]


async def _test_runs(pool, organization_id, control_ids, since, until) -> list[dict]:
    if not control_ids:
        return []
    clauses = ["t.organization_id = $1", "t.control_id = ANY($2::uuid[])"]
    vals: list = [organization_id, control_ids]
    idx = 3
    if since:
        clauses.append(f"t.run_at::date >= ${idx}")
        vals.append(since)
        idx += 1
    if until:
        clauses.append(f"t.run_at::date <= ${idx}")
        vals.append(until)
        idx += 1
    rows = await pool.fetch(
        f"""SELECT t.run_at, c.name AS control_name, t.result, t.method,
                   u.username AS tester, t.period_start, t.period_end,
                   t.evidence_url, t.notes
            FROM control_test_runs t
            JOIN assets c ON c.id = t.control_id
            LEFT JOIN users u ON u.id = t.tested_by
            WHERE {' AND '.join(clauses)}
            ORDER BY t.run_at DESC""",
        *vals,
    )
    return [dict(r) for r in rows]


def _test_ledger_csv(runs: list[dict]) -> str:
    return _csv(
        ["run_at", "control", "result", "method", "tester",
         "period_start", "period_end", "evidence_url", "notes"],
        [[r["run_at"], r["control_name"], r["result"], r["method"], r["tester"] or "",
          r["period_start"] or "", r["period_end"] or "", r["evidence_url"] or "",
          r["notes"] or ""] for r in runs],
    )


async def _audit_rows(pool, asset_ids, since, until) -> list[dict]:
    # Scoped by entity_id (the framework's assets — all in this org), so we don't
    # depend on audit_log carrying an organization_id column.
    clauses = ["entity_id = ANY($1::uuid[])"]
    vals: list = [asset_ids]
    idx = 2
    if since:
        clauses.append(f"created_at::date >= ${idx}")
        vals.append(since)
        idx += 1
    if until:
        clauses.append(f"created_at::date <= ${idx}")
        vals.append(until)
        idx += 1
    rows = await pool.fetch(
        f"""SELECT created_at, username, action, entity_type, entity_name, changes
            FROM audit_log
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC""",
        *vals,
    )
    return [dict(r) for r in rows]


def _audit_csv(rows: list[dict]) -> str:
    out = []
    for r in rows:
        changes = r["changes"]
        if changes is not None and not isinstance(changes, str):
            changes = json.dumps(changes, default=str)
        out.append([r["created_at"], r["username"], r["action"],
                    r["entity_type"], r["entity_name"] or "", changes or ""])
    return _csv(["timestamp", "user", "action", "entity_type", "entity_name", "changes"], out)


async def _collect_evidence(pool, zf, organization_id, owner_asset_ids) -> tuple[int, int]:
    """Bundle attachment files into evidence/ and write evidence/_index.csv.

    Returns (file_count, link_count). Encrypted files are decrypted in; URL /
    document attachments are recorded in the index without a file.
    """
    index_rows: list[list] = []
    file_count = 0
    used_arcnames: set[str] = set()

    for asset_id in owner_asset_ids:
        attachments = await attachment_service.list_attachments(
            pool, asset_id, organization_id=organization_id
        )
        if not attachments:
            continue
        owner = await pool.fetchrow("SELECT name, type FROM assets WHERE id = $1", asset_id)
        owner_label = _slug(f"{owner['type']}-{owner['name']}") if owner else str(asset_id)

        for att in attachments:
            is_file = att.kind == AttachmentKind.FILE and att.url_or_path
            if is_file and os.path.isfile(att.url_or_path):
                try:
                    with open(att.url_or_path, "rb") as f:
                        data = f.read()
                    if att.encrypted:
                        data = decrypt_bytes(data, "file_contents")
                except Exception as exc:  # noqa: BLE001 — record, don't abort the package
                    index_rows.append([owner_label, att.name, att.kind.value,
                                       f"ERROR: {type(exc).__name__}", att.collected_at or "",
                                       att.valid_until or ""])
                    continue
                arcname = f"evidence/{owner_label}/{_slug(att.name)}"
                if arcname in used_arcnames:
                    stem, _, ext = arcname.rpartition(".")
                    n = 2
                    base = stem if stem else arcname
                    while arcname in used_arcnames:
                        arcname = f"{base}-{n}.{ext}" if ext and stem else f"{base}-{n}"
                        n += 1
                used_arcnames.add(arcname)
                zf.writestr(arcname, data)
                file_count += 1
                index_rows.append([owner_label, att.name, att.kind.value, arcname,
                                   att.collected_at or "", att.valid_until or ""])
            else:
                # URL / document link, or a file missing on disk.
                index_rows.append([owner_label, att.name, att.kind.value,
                                   att.url_or_path or "", att.collected_at or "",
                                   att.valid_until or ""])

    zf.writestr(
        "evidence/_index.csv",
        _csv(["owner", "name", "kind", "path_or_url", "collected_at", "valid_until"], index_rows),
    )
    return file_count, len(index_rows) - file_count


def _readme(framework_name: str, generated_at: datetime, since, until, counts) -> str:
    period = "all history"
    if since or until:
        period = f"{since or 'start'} → {until or 'now'}"
    return (
        f"GRCen Evidence Package\n"
        f"======================\n\n"
        f"Framework: {framework_name}\n"
        f"Period:    {period}\n"
        f"Generated: {generated_at.isoformat()}\n\n"
        f"Contents\n"
        f"--------\n"
        f"  statement_of_applicability.csv  {counts['requirements']} requirement(s)\n"
        f"  control_library.csv             {counts['controls']} control(s) satisfying this framework\n"
        f"  control_test_ledger.csv         {counts['test_runs']} recorded test run(s)\n"
        f"  audit_trail.csv                 {counts['audit_events']} change-log event(s)\n"
        f"  evidence/                       {counts['evidence_files']} file(s), "
        f"{counts['evidence_links']} link(s) (see evidence/_index.csv)\n\n"
        f"The control test ledger lists every recorded test run (not just the\n"
        f"latest), so an auditor can confirm a control operated throughout the\n"
        f"period. The audit trail is the change history for this framework's\n"
        f"assets. Encrypted evidence files are decrypted into this package.\n"
    )


async def build_package(
    pool: asyncpg.Pool,
    *,
    organization_id: UUID,
    framework_id: UUID,
    since: date | None = None,
    until: date | None = None,
    generated_at: datetime | None = None,
) -> EvidencePackageResult | None:
    """Assemble the evidence ZIP for one framework, or None if it doesn't exist."""
    detail = await framework_service.get_framework_detail(
        pool, framework_id, organization_id=organization_id
    )
    if detail is None:
        return None

    generated_at = generated_at or datetime.now(UTC)
    framework_name = detail.framework["name"]
    req_ids = [r.id for r in detail.requirements]

    # Controls satisfying this framework's requirements (unique, order-preserving).
    control_ids: list[UUID] = []
    seen: set[UUID] = set()
    for r in detail.requirements:
        for s in r.satisfiers:
            if s.get("type") == "control" and s["id"] not in seen:
                seen.add(s["id"])
                control_ids.append(s["id"])

    asset_ids = [framework_id, *req_ids, *control_ids]
    counts: dict[str, int] = {}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("statement_of_applicability.csv", _soa_csv(detail))
        counts["requirements"] = len(detail.requirements)

        controls = await _framework_controls(pool, organization_id, control_ids)
        zf.writestr("control_library.csv", _control_csv(controls))
        counts["controls"] = len(controls)

        runs = await _test_runs(pool, organization_id, control_ids, since, until)
        zf.writestr("control_test_ledger.csv", _test_ledger_csv(runs))
        counts["test_runs"] = len(runs)

        audit = await _audit_rows(pool, asset_ids, since, until)
        zf.writestr("audit_trail.csv", _audit_csv(audit))
        counts["audit_events"] = len(audit)

        files, links = await _collect_evidence(pool, zf, organization_id, req_ids + control_ids)
        counts["evidence_files"] = files
        counts["evidence_links"] = links

        manifest = {
            "framework": {"id": str(framework_id), "name": framework_name},
            "organization_id": str(organization_id),
            "generated_at": generated_at.isoformat(),
            "period": {
                "since": since.isoformat() if since else None,
                "until": until.isoformat() if until else None,
            },
            "contents": [
                "statement_of_applicability.csv", "control_library.csv",
                "control_test_ledger.csv", "audit_trail.csv", "evidence/",
            ],
            "counts": counts,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        zf.writestr("README.txt", _readme(framework_name, generated_at, since, until, counts))

    filename = f"evidence-package-{_slug(framework_name)}-{generated_at.strftime('%Y%m%d')}.zip"
    return EvidencePackageResult(
        filename=filename, content=buf.getvalue(),
        framework_name=framework_name, counts=counts,
    )
