def test_get_material_sds_config_returns_defaults(api_client) -> None:
    response = api_client.get("/api/v1/material-sds-analysis/config")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"resource_urls", "allowed_domains"}
    assert "osha.gov" in body["allowed_domains"]
    assert all(url.startswith("https://") for url in body["resource_urls"])


def test_put_material_sds_config_round_trip(api_client) -> None:
    response = api_client.put(
        "/api/v1/material-sds-analysis/config",
        json={
            "resource_urls": ["https://pubchem.ncbi.nlm.nih.gov"],
            "allowed_domains": [
                "pubchem.ncbi.nlm.nih.gov",
                "PubChem.ncbi.nlm.nih.gov",  # duplicate (different case)
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["allowed_domains"] == ["pubchem.ncbi.nlm.nih.gov"]

    # Persisted: a follow-up GET reflects the update.
    follow_up = api_client.get("/api/v1/material-sds-analysis/config")
    assert follow_up.json()["resource_urls"] == ["https://pubchem.ncbi.nlm.nih.gov"]


def test_put_material_sds_config_rejects_invalid_url(api_client) -> None:
    response = api_client.put(
        "/api/v1/material-sds-analysis/config",
        json={"resource_urls": ["ftp://insecure.example.com"], "allowed_domains": []},
    )

    assert response.status_code == 422
