"""HTTP-layer tests for the material SDS analysis endpoint.

Uses the `api_client` fixture (full app with fake DB/spaCy). The rules pre-screen
path is exercised so the assertions do not depend on the stub LLM output.
"""


def test_sds_endpoint_accepts_new_fields_and_returns_rules_decision(api_client):
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={
            "materials": [
                {
                    "material_name": "Industrial Cleaner",
                    "material_description": "Solvent-based degreaser",
                    "unspsc_code": "47131800",
                    "classification": "HAZ-CHEM",
                    "manufacturer_name": "Acme Chemicals",
                    "manufacturer_description": "Aliphatic solvent blend",
                    "options": {"enable_online_search": False},
                }
            ]
        },
    )

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["method"] == "rules"
    assert item["decision"] == "required"
    assert item["is_sds_required"] is True
    assert "solvents" in item["hazardous_categories"]
    assert any("Deterministic rule match" in e for e in item["evidence"])


def test_sds_endpoint_returns_needs_review_for_insufficient_input(api_client):
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={"materials": [{"material_type": "raw_material"}]},
    )

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert item["method"] == "insufficient_input"
    assert item["decision"] == "needs_review"
    assert item["is_sds_required"] is False


def test_sds_endpoint_rejects_empty_materials(api_client):
    response = api_client.post(
        "/api/v1/material-sds-analysis", json={"materials": []}
    )
    assert response.status_code == 422
