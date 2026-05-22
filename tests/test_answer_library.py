"""Tests for the inbound security questionnaire answer library (feature #21).

Phase 1: the Answer asset type, the posture/organizational taxonomy split
(answers excluded from the general /assets surfaces), the substantiated_by
relationship, and the /answers workspace.
"""
import uuid

import pytest

from grcen.models.asset import (
    ORGANIZATIONAL_TYPES,
    POSTURE_TYPES,
    AssetType,
)
from grcen.permissions import UserRole
from grcen.services import answer_service
from grcen.services import asset as asset_svc
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


async def _mk_answer(pool, uid, question, answer, **meta):
    return await asset_svc.create_asset(
        pool,
        type=AssetType.ANSWER,
        name=question,
        description=answer,
        status="active",
        updated_by=uid,
        metadata_=meta or None,
    )


# ── taxonomy ──────────────────────────────────────────────────────────────


def test_answer_is_posture_not_organizational():
    assert AssetType.ANSWER in POSTURE_TYPES
    assert AssetType.ANSWER not in ORGANIZATIONAL_TYPES
    # All other types remain organizational
    assert AssetType.CONTROL in ORGANIZATIONAL_TYPES
    assert len(ORGANIZATIONAL_TYPES) == len(list(AssetType)) - len(POSTURE_TYPES)


# ── exclusion from the general asset list ───────────────────────────────────


@pytest.mark.asyncio
async def test_answers_excluded_from_general_list(pool, admin_user):
    await asset_svc.create_asset(
        pool, type=AssetType.SYSTEM, name="Prod DB", updated_by=admin_user.id
    )
    await _mk_answer(pool, admin_user.id, "Do you encrypt at rest?", "Yes, AES-256.")

    items, total = await asset_svc.list_assets(pool)
    types = {a.type for a in items}
    assert AssetType.ANSWER not in types
    assert AssetType.SYSTEM in types
    # The answer is not counted in the general total either
    assert total == 1


@pytest.mark.asyncio
async def test_type_filter_reaches_answers(pool, admin_user):
    await _mk_answer(pool, admin_user.id, "Do you do MFA?", "Yes, TOTP.")
    items, total = await asset_svc.list_assets(pool, asset_type=AssetType.ANSWER)
    assert total == 1
    assert items[0].name == "Do you do MFA?"


# ── answer_service ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_answers_with_substantiators(pool, admin_user):
    from grcen.services import relationship as rel_svc

    control = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="MFA Enforcement", updated_by=admin_user.id
    )
    answer = await _mk_answer(
        pool, admin_user.id, "Do you enforce MFA?", "Yes.", short_answer="yes"
    )
    await rel_svc.create_relationship(
        pool,
        source_asset_id=answer.id,
        target_asset_id=control.id,
        relationship_type=answer_service.SUBSTANTIATES_REL,
    )

    answers = await answer_service.list_answers(pool)
    assert len(answers) == 1
    entry = answers[0]
    assert entry["question"] == "Do you enforce MFA?"
    assert entry["short_answer"] == "yes"
    assert len(entry["substantiators"]) == 1
    assert entry["substantiators"][0]["name"] == "MFA Enforcement"
    assert entry["substantiators"][0]["type"] == "control"


@pytest.mark.asyncio
async def test_unsubstantiated_answer_has_empty_list(pool, admin_user):
    await _mk_answer(pool, admin_user.id, "Lonely question?", "Lonely answer.")
    answers = await answer_service.list_answers(pool)
    assert len(answers) == 1
    assert answers[0]["substantiators"] == []


@pytest.mark.asyncio
async def test_count_answers(pool, admin_user):
    await _mk_answer(pool, admin_user.id, "Q1", "A1")
    await _mk_answer(pool, admin_user.id, "Q2", "A2")
    assert await answer_service.count_answers(pool) == 2


# ── freshness engine (Phase 2) ──────────────────────────────────────────────


async def _link(pool, answer, target):
    from grcen.services import relationship as rel_svc

    await rel_svc.create_relationship(
        pool,
        source_asset_id=answer.id,
        target_asset_id=target.id,
        relationship_type=answer_service.SUBSTANTIATES_REL,
    )


@pytest.mark.asyncio
async def test_unsubstantiated_answer_needs_review(pool, admin_user):
    await _mk_answer(pool, admin_user.id, "Q?", "A.")
    answers = await answer_service.list_answers(pool)
    assert answers[0]["needs_review"] is True
    assert answers[0]["review_reasons"] == ["no substantiating assets"]


@pytest.mark.asyncio
async def test_answer_backed_by_effective_control_is_current(pool, admin_user):
    control = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="Encryption",
        updated_by=admin_user.id, metadata_={"effectiveness": "effective"},
    )
    answer = await _mk_answer(pool, admin_user.id, "Encrypt?", "Yes.")
    await _link(pool, answer, control)
    answers = await answer_service.list_answers(pool)
    assert answers[0]["needs_review"] is False
    assert answers[0]["review_reasons"] == []


@pytest.mark.asyncio
async def test_ineffective_control_flags_answer(pool, admin_user):
    control = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="Backups",
        updated_by=admin_user.id, metadata_={"effectiveness": "ineffective"},
    )
    answer = await _mk_answer(pool, admin_user.id, "Backups tested?", "Yes.")
    await _link(pool, answer, control)
    answers = await answer_service.list_answers(pool)
    assert answers[0]["needs_review"] is True
    assert any("Backups" in r for r in answers[0]["review_reasons"])


@pytest.mark.asyncio
async def test_archived_substantiator_flags_answer(pool, admin_user):
    policy = await asset_svc.create_asset(
        pool, type=AssetType.POLICY, name="Access Policy",
        status="archived", updated_by=admin_user.id,
    )
    answer = await _mk_answer(pool, admin_user.id, "Have a policy?", "Yes.")
    await _link(pool, answer, policy)
    answers = await answer_service.list_answers(pool)
    assert answers[0]["needs_review"] is True
    assert any("archived" in r for r in answers[0]["review_reasons"])


@pytest.mark.asyncio
async def test_not_started_framework_flags_answer(pool, admin_user):
    fw = await asset_svc.create_asset(
        pool, type=AssetType.FRAMEWORK, name="ISO 27001",
        updated_by=admin_user.id, metadata_={"certification_status": "not_started"},
    )
    answer = await _mk_answer(pool, admin_user.id, "ISO certified?", "Yes.")
    await _link(pool, answer, fw)
    answers = await answer_service.list_answers(pool)
    assert answers[0]["needs_review"] is True


@pytest.mark.asyncio
async def test_count_needs_review(pool, admin_user):
    # one current, one stale
    control = await asset_svc.create_asset(
        pool, type=AssetType.CONTROL, name="Good Control",
        updated_by=admin_user.id, metadata_={"effectiveness": "effective"},
    )
    a1 = await _mk_answer(pool, admin_user.id, "Good?", "Yes.")
    await _link(pool, a1, control)
    await _mk_answer(pool, admin_user.id, "Orphan?", "Yes.")  # unsubstantiated
    assert await answer_service.count_needs_review(pool) == 1


# ── workspace + create flow (HTTP) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_answers_workspace_renders(auth_client, pool):
    resp = await auth_client.get("/answers")
    assert resp.status_code == 200
    assert "Answer Library" in resp.text


@pytest.mark.asyncio
async def test_create_form_pins_answer_type(auth_client):
    resp = await auth_client.get("/assets/new?type=answer")
    assert resp.status_code == 200
    # Pinned type is a hidden input, not a select; labels are relabeled.
    assert 'type="hidden" name="type" value="answer"' in resp.text
    assert "Canonical answer" in resp.text


@pytest.mark.asyncio
async def test_create_answer_via_form(auth_client, pool):
    from tests.conftest import get_csrf_token

    csrf = await get_csrf_token(auth_client, "/assets/new?type=answer")
    resp = await auth_client.post(
        "/assets/new",
        data={
            "type": "answer",
            "name": "Do you log access?",
            "description": "Yes, to data_access_log.",
            "status": "active",
            "metadata.short_answer": "yes",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303)
    answers = await answer_service.list_answers(pool)
    assert any(a["question"] == "Do you log access?" for a in answers)
