"""Tests for inbound questionnaire import & fill (feature #21 Phase 3)."""
import uuid

import pytest

from grcen.models.asset import AssetType
from grcen.permissions import UserRole
from grcen.services import answer_service
from grcen.services import asset as asset_svc
from grcen.services import questionnaire_service as qs
from grcen.services.auth import create_user


@pytest.fixture
async def admin_user(pool):
    return await create_user(
        pool, f"admin_{uuid.uuid4().hex[:8]}", "pw", role=UserRole.ADMIN
    )


@pytest.fixture
async def org_id(admin_user):
    return admin_user.organization_id


# ── CSV parsing ─────────────────────────────────────────────────────────────


def test_parse_questions_first_column():
    csv_bytes = b"Do you encrypt?,notes\nDo you do MFA?,n/a\n"
    qns = qs.parse_questions(csv_bytes, column=0)
    assert qns == ["Do you encrypt?", "Do you do MFA?"]


def test_parse_questions_skips_header_and_blanks():
    csv_bytes = b"Question,Answer\nQ1,\n\nQ2,\n"
    qns = qs.parse_questions(csv_bytes, column=0, has_header=True)
    assert qns == ["Q1", "Q2"]


def test_parse_questions_other_column():
    csv_bytes = b"id,question\n1,First?\n2,Second?\n"
    qns = qs.parse_questions(csv_bytes, column=1, has_header=True)
    assert qns == ["First?", "Second?"]


# ── lifecycle ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_and_import(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(
        pool, organization_id=org_id, name="Acme SIG", source="Acme", created_by=admin_user.id
    )
    n = await qs.import_questions(
        pool, qid, ["Q1?", "Q2?", "Q3?"], organization_id=org_id
    )
    assert n == 3
    responses = await qs.list_responses(pool, qid, organization_id=org_id)
    assert [r["question_text"] for r in responses] == ["Q1?", "Q2?", "Q3?"]
    assert [r["position"] for r in responses] == [0, 1, 2]
    assert all(r["status"] == "unanswered" for r in responses)


@pytest.mark.asyncio
async def test_import_appends_positions(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["A?"], organization_id=org_id)
    await qs.import_questions(pool, qid, ["B?"], organization_id=org_id)
    responses = await qs.list_responses(pool, qid, organization_id=org_id)
    assert [r["position"] for r in responses] == [0, 1]


@pytest.mark.asyncio
async def test_map_response_autofills_from_library(pool, admin_user, org_id):
    answer = await asset_svc.create_asset(
        pool, organization_id=org_id, type=AssetType.ANSWER,
        name="Do you encrypt at rest?", description="Yes, AES-256-GCM.",
        updated_by=admin_user.id,
    )
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["Encryption at rest?"], organization_id=org_id)
    resp = (await qs.list_responses(pool, qid, organization_id=org_id))[0]

    await qs.set_response(pool, resp["id"], organization_id=org_id, answer_asset_id=answer.id)

    updated = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    assert updated["answer_asset_id"] == answer.id
    assert updated["filled_answer"] == "Yes, AES-256-GCM."
    assert updated["status"] == "filled"


@pytest.mark.asyncio
async def test_manual_answer_and_review(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["Anything?"], organization_id=org_id)
    resp = (await qs.list_responses(pool, qid, organization_id=org_id))[0]

    await qs.set_response(pool, resp["id"], organization_id=org_id, filled_answer="Custom answer.")
    updated = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    assert updated["filled_answer"] == "Custom answer."
    assert updated["status"] == "filled"

    await qs.set_response(
        pool, resp["id"], organization_id=org_id,
        filled_answer="Custom answer.", mark_reviewed=True,
    )
    updated = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    assert updated["status"] == "reviewed"


@pytest.mark.asyncio
async def test_list_questionnaires_progress(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["Q1?", "Q2?"], organization_id=org_id)
    resp = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    await qs.set_response(pool, resp["id"], organization_id=org_id, filled_answer="A.")

    items = await qs.list_questionnaires(pool, organization_id=org_id)
    row = next(q for q in items if q["id"] == qid)
    assert row["total"] == 2
    assert row["answered"] == 1


@pytest.mark.asyncio
async def test_set_status_validates(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.set_status(pool, qid, "submitted", organization_id=org_id)
    q = await qs.get_questionnaire(pool, qid, organization_id=org_id)
    assert q["status"] == "submitted"
    with pytest.raises(ValueError):
        await qs.set_status(pool, qid, "bogus", organization_id=org_id)


@pytest.mark.asyncio
async def test_delete_cascades_responses(pool, admin_user, org_id):
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["Q1?"], organization_id=org_id)
    assert await qs.delete_questionnaire(pool, qid, organization_id=org_id) is True
    assert await qs.get_questionnaire(pool, qid, organization_id=org_id) is None
    # responses gone via ON DELETE CASCADE
    assert await qs.list_responses(pool, qid, organization_id=org_id) == []


# ── HTTP smoke ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_questionnaire_http_flow(auth_client, pool):
    from tests.conftest import get_csrf_token

    csrf = await get_csrf_token(auth_client, "/questionnaires")
    resp = await auth_client.post(
        "/questionnaires",
        data={"name": "Vendor Review", "source": "BigCo", "csrf_token": csrf},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    qid = resp.headers["location"].rsplit("/", 1)[-1]

    detail = await auth_client.get(f"/questionnaires/{qid}")
    assert detail.status_code == 200
    assert "Vendor Review" in detail.text

    listing = await auth_client.get("/questionnaires")
    assert "Vendor Review" in listing.text


@pytest.mark.asyncio
async def test_unsubstantiated_library_still_usable_for_fill(pool, admin_user, org_id):
    # An answer with no substantiator still maps/fills fine (freshness is advisory).
    answer = await asset_svc.create_asset(
        pool, organization_id=org_id, type=AssetType.ANSWER,
        name="Q?", description="A.", updated_by=admin_user.id,
    )
    answers = await answer_service.list_answers(pool, organization_id=org_id)
    assert answers[0]["needs_review"] is True  # advisory only
    qid = await qs.create_questionnaire(pool, organization_id=org_id, name="Q", created_by=admin_user.id)
    await qs.import_questions(pool, qid, ["map me"], organization_id=org_id)
    resp = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    await qs.set_response(pool, resp["id"], organization_id=org_id, answer_asset_id=answer.id)
    updated = (await qs.list_responses(pool, qid, organization_id=org_id))[0]
    assert updated["filled_answer"] == "A."
