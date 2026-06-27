"""Admin content-packs page: access control and the install/preview/uninstall flow."""
import pytest

from grcen.services import content_packs, framework_service, organization_service


@pytest.mark.asyncio
async def test_admin_sees_content_packs_page(auth_client):
    resp = await auth_client.get("/admin/content-packs")
    assert resp.status_code == 200
    body = resp.text
    assert "Content Packs" in body
    # Every registered pack is listed.
    assert "Common Compliance Baseline" in body
    assert "NIST CSF 2.0" in body
    assert "ISO/IEC 27001:2022" in body


@pytest.mark.asyncio
async def test_viewer_cannot_access_content_packs(viewer_client):
    resp = await viewer_client.get("/admin/content-packs", follow_redirects=False)
    assert resp.status_code in (302, 303, 403)


@pytest.mark.asyncio
async def test_install_post_redirects(auth_client):
    # Preview (dry run) a pack; whether or not its content is authored yet, the
    # handler must redirect back to the page with a flash (never 500).
    resp = await auth_client.post(
        "/admin/content-packs/install",
        data={"pack_id": "common-baseline", "action": "preview"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/content-packs" in resp.headers["location"]


@pytest.mark.asyncio
async def test_unknown_pack_redirects_with_error(auth_client):
    resp = await auth_client.post(
        "/admin/content-packs/install",
        data={"pack_id": "does-not-exist", "action": "install"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "Unknown" in resp.headers["location"]


@pytest.mark.asyncio
async def test_install_baseline_via_ui_then_render_crosswalks(auth_client, pool):
    """Full HTTP flow: admin installs the baseline, framework pages render crosswalks."""
    pack = content_packs.get_pack("common-baseline")
    if not content_packs.fragments_present(pack):
        pytest.skip("common-baseline content not authored yet")

    resp = await auth_client.post(
        "/admin/content-packs/install",
        data={"pack_id": "common-baseline", "action": "install"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "Installed" in resp.headers["location"]

    index = await auth_client.get("/frameworks")
    assert index.status_code == 200
    for name in ("NIST", "ISO/IEC 27001", "SOC 2", "CIS"):
        assert name in index.text

    # Pick the framework with the most cross-framework maps and render it.
    org = await organization_service.get_default_org_id(pool)
    summaries = await framework_service.list_frameworks(pool, organization_id=org)
    best_id, best_count = None, -1
    for s in summaries:
        detail = await framework_service.get_framework_detail(
            pool, s.id, organization_id=org
        )
        if detail.crosswalk_count > best_count:
            best_id, best_count = s.id, detail.crosswalk_count
    assert best_count > 0

    page = await auth_client.get(f"/frameworks/{best_id}")
    assert page.status_code == 200
    assert "Cross-framework" in page.text
    assert "cross-framework map" in page.text
