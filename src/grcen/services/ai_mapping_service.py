"""AI control-to-requirement mapping suggester.

Given a framework's open gaps and the org's control library, asks Claude to
propose which existing control best satisfies each uncovered requirement. The
model never writes to the graph: every proposal becomes a DRAFT
``relationship_create`` pending change, so a human approves each mapping in the
normal /approvals queue before any ``satisfies`` edge exists.

The ``anthropic`` import is lazy and the client is injectable, so the app starts
(and the test suite runs) without the package or an API key present.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from grcen.config import settings
from grcen.models.asset import AssetType
from grcen.models.user import User
from grcen.services import framework_service, workflow_service

_CONFIDENCE = ("high", "medium", "low")
_QUEUE_CONFIDENCE = {"high", "medium"}  # 'low' is reported but not queued

_TOOL = {
    "name": "propose_mappings",
    "description": (
        "Propose which control best satisfies each uncovered requirement. "
        "Give at most one control per requirement — the single best fit. Omit a "
        "requirement entirely if no listed control is a credible match. Only use "
        "ids from the provided lists; never invent ids."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "control_id": {"type": "string"},
                        "confidence": {"type": "string", "enum": list(_CONFIDENCE)},
                        "rationale": {"type": "string"},
                    },
                    "required": ["requirement_id", "control_id", "confidence", "rationale"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["mappings"],
        "additionalProperties": False,
    },
}

_SYSTEM = (
    "You are a GRC analyst mapping security controls to compliance requirements. "
    "A control satisfies a requirement when implementing the control would provide "
    "the evidence or capability the requirement demands. Be conservative: a weak or "
    "tangential link is worse than no suggestion, because every suggestion costs a "
    "human a review. Prefer precision over recall."
)


def is_configured() -> bool:
    return bool(settings.ANTHROPIC_API_KEY)


async def _gather(
    pool: asyncpg.Pool, framework_id: UUID, organization_id: UUID
) -> tuple[list[dict], list[dict]]:
    """(open-gap requirements, candidate controls) — both id/name/description dicts."""
    detail = await framework_service.get_framework_detail(
        pool, framework_id, organization_id=organization_id)
    gap_ids = (
        [r.id for r in detail.applicable_requirements if r.coverage == "gap"]
        if detail else []
    )
    requirements: list[dict] = []
    if gap_ids:
        rows = await pool.fetch(
            """SELECT id, name, description, metadata->>'reference_id' AS code
               FROM assets WHERE id = ANY($1::uuid[]) ORDER BY name""",
            gap_ids,
        )
        requirements = [
            {"id": str(r["id"]), "name": r["name"],
             "description": r["description"] or "", "code": r["code"] or ""}
            for r in rows
        ]
    control_rows = await pool.fetch(
        """SELECT id, name, description FROM assets
           WHERE type = 'control' AND status = 'active' AND organization_id = $1
           ORDER BY name LIMIT 250""",
        organization_id,
    )
    controls = [
        {"id": str(r["id"]), "name": r["name"], "description": r["description"] or ""}
        for r in control_rows
    ]
    return requirements, controls


def _build_prompt(requirements: list[dict], controls: list[dict]) -> str:
    def _line(d: dict) -> str:
        code = f"[{d['code']}] " if d.get("code") else ""
        desc = f" — {d['description']}" if d["description"] else ""
        return f"- id={d['id']} {code}{d['name']}{desc}"

    reqs = "\n".join(_line(r) for r in requirements)
    ctrls = "\n".join(_line(c) for c in controls)
    return (
        "UNCOVERED REQUIREMENTS (each currently has no satisfying control):\n"
        f"{reqs}\n\nAVAILABLE CONTROLS:\n{ctrls}\n\n"
        "For each requirement that a listed control credibly satisfies, propose the "
        "single best control via the propose_mappings tool."
    )


async def _propose(
    client, model: str, requirements: list[dict], controls: list[dict]
) -> list[dict]:
    resp = await client.messages.create(
        model=model,
        max_tokens=8000,
        system=_SYSTEM,
        messages=[{"role": "user", "content": _build_prompt(requirements, controls)}],
        tools=[_TOOL],
        tool_choice={"type": "tool", "name": "propose_mappings"},
    )
    if getattr(resp, "stop_reason", None) == "refusal":
        return []
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "propose_mappings":
            return list(block.input.get("mappings", []))
    return []


async def suggest_mappings(
    pool: asyncpg.Pool,
    *,
    framework_id: UUID,
    organization_id: UUID,
    user: User,
    client=None,
) -> dict[str, Any]:
    """Propose mappings and queue each as a DRAFT pending change. Returns a summary."""
    requirements, controls = await _gather(pool, framework_id, organization_id)
    if not requirements or not controls:
        return {"created": 0, "skipped": 0, "low_confidence": 0,
                "reason": "no open gaps" if not requirements else "no controls"}

    if client is None:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    mappings = await _propose(client, settings.ANTHROPIC_MODEL, requirements, controls)

    req_by_id = {r["id"]: r for r in requirements}
    ctrl_by_id = {c["id"]: c for c in controls}
    created = skipped = low = 0
    queued: list[dict] = []
    for m in mappings:
        req = req_by_id.get(m.get("requirement_id"))
        ctrl = ctrl_by_id.get(m.get("control_id"))
        if not req or not ctrl:  # hallucinated / off-list id
            skipped += 1
            continue
        if m.get("confidence") not in _QUEUE_CONFIDENCE:
            low += 1
            continue
        rationale = str(m.get("rationale", "")).strip()
        try:
            await workflow_service.submit(
                pool,
                action="relationship_create",
                asset_type=AssetType.CONTROL,
                target_asset_id=UUID(req["id"]),
                title=f"AI: {ctrl['name']} satisfies {req['name']}",
                payload={
                    "source_asset_id": ctrl["id"],
                    "target_asset_id": req["id"],
                    "relationship_type": "satisfies",
                    "description": f"AI-suggested ({m['confidence']}): {rationale}",
                },
                user=user,
            )
            created += 1
            queued.append({"control": ctrl["name"], "requirement": req["name"],
                           "confidence": m["confidence"]})
        except ValueError:
            # A pending relationship_create already exists for this requirement.
            skipped += 1
    return {"created": created, "skipped": skipped, "low_confidence": low, "queued": queued}
