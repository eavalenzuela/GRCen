import io

import pytest


@pytest.mark.asyncio
async def test_asset_import_csv(auth_client):
    csv_content = "name,type,status\nImported Person,person,active\nImported System,system,draft\n"
    files = {"file": ("assets.csv", io.BytesIO(csv_content.encode()), "text/csv")}

    # Preview
    resp = await auth_client.post("/api/imports/assets/preview", files=files)
    assert resp.status_code == 200
    assert resp.json()["valid_rows"] == 2

    # Execute (re-create files since they're consumed)
    files = {"file": ("assets.csv", io.BytesIO(csv_content.encode()), "text/csv")}
    resp = await auth_client.post("/api/imports/assets/execute", files=files)
    assert resp.status_code == 200
    assert resp.json()["created"] == 2

    # Verify
    resp = await auth_client.get("/api/assets/")
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_export_csv(auth_client):
    await auth_client.post("/api/assets/", json={"type": "person", "name": "Export Me"})

    resp = await auth_client.get("/api/exports/assets?format=csv")
    assert resp.status_code == 200
    assert "Export Me" in resp.text


@pytest.mark.asyncio
async def test_export_json(auth_client):
    await auth_client.post("/api/assets/", json={"type": "risk", "name": "Risk Export"})

    resp = await auth_client.get("/api/exports/assets?format=json")
    assert resp.status_code == 200
    data = resp.json()
    assert any(r["name"] == "Risk Export" for r in data)
