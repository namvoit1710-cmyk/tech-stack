def test_get_field_config_defaults_to_empty(api_client) -> None:
    response = api_client.get("/api/v1/ingest-fields/config")

    assert response.status_code == 200
    assert response.json() == {"embedding_fields": [], "graph_entity_fields": []}


def test_put_field_config_round_trips(api_client) -> None:
    response = api_client.put(
        "/api/v1/ingest-fields/config",
        json={
            "embedding_fields": ["Material Name", "Manufacturer"],
            "graph_entity_fields": ["Manufacturer"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "embedding_fields": ["Material Name", "Manufacturer"],
        "graph_entity_fields": ["Manufacturer"],
    }

    reread = api_client.get("/api/v1/ingest-fields/config")
    assert reread.json()["embedding_fields"] == ["Material Name", "Manufacturer"]


def test_put_field_config_rejects_unknown_available_field(api_client) -> None:
    response = api_client.put(
        "/api/v1/ingest-fields/config",
        json={
            "embedding_fields": ["Ghost Column"],
            "graph_entity_fields": [],
            "available_fields": ["Material Name", "Manufacturer"],
        },
    )

    assert response.status_code == 422
    assert "Ghost Column" in response.text


def test_reindex_endpoint_reindexes_requested_files(api_client) -> None:
    response = api_client.post(
        "/api/v1/ingest-fields/reindex",
        json={"file_ids": ["file-1", "file-2"], "tenant_id": "tenant-x"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == "tenant-x"
    assert body["reindexed_file_ids"] == ["file-1", "file-2"]
    assert body["failed_file_ids"] == []


def test_openapi_and_ui_console_expose_field_config(api_client) -> None:
    paths = api_client.get("/openapi.json").json()["paths"]
    assert "/api/v1/ingest-fields/config" in paths
    assert "/api/v1/ingest-fields/reindex" in paths

    # The UI console router is mounted under the API prefix in this build.
    ui = api_client.get("/api/v1/ui/config")
    assert ui.status_code == 200
    assert "/api/v1/ingest-fields/config" in ui.text
    assert "Ingest field selection" in ui.text
