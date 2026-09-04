def test_health_route_returns_ok(api_client) -> None:
    response = api_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_openapi_contains_current_routes(api_client) -> None:
    response = api_client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/import-jobs" in response.json()["paths"]
    assert "/api/v1/import-jobs/{job_id}" in response.json()["paths"]
    assert "/api/v1/material-sds-analysis" in response.json()["paths"]
    assert "/api/v1/search" in response.json()["paths"]
    assert "/api/v1/search/config" in response.json()["paths"]
    assert "/api/v1/similarity" in response.json()["paths"]
    assert "/api/v1/similarity/config" in response.json()["paths"]


def test_ui_console_index_route_returns_html(api_client) -> None:
    response = api_client.get("/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/ui/search" in response.text
    assert "/ui/similarity" in response.text
    assert "/ui/config" in response.text
    assert "/ui/material-sds-analysis" in response.text
    assert "/ui/material-group-recommendation" in response.text
    assert "/ui/buyer-recommendation" in response.text


def test_ui_console_search_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/search")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "POST /api/v1/search" in response.text
    assert "/api/v1/search" in response.text


def test_ui_console_similarity_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/similarity")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "POST /api/v1/similarity" in response.text
    assert "/api/v1/similarity" in response.text


def test_ui_console_config_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/config")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "/api/v1/search/config" in response.text
    assert "/api/v1/similarity/config" in response.text


def test_ui_console_material_sds_analysis_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/material-sds-analysis")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "POST /api/v1/material-sds-analysis" in response.text
    assert "/api/v1/material-sds-analysis" in response.text


def test_ui_console_material_group_recommendation_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/material-group-recommendation")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Material Group recommendation" in response.text
    assert "POST /api/v1/similarity" in response.text
    assert "Recommend internal material group" in response.text
    assert "Technical specifications" in response.text
    assert "Confidence" in response.text


def test_ui_console_buyer_recommendation_route_returns_html(api_client) -> None:
    response = api_client.get("/ui/buyer-recommendation")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Buyer recommendation" in response.text
    assert "POST /api/v1/similarity" in response.text
    assert "Recommend buyer" in response.text
    assert "Plant" in response.text
    assert "Division" in response.text
    assert "Material Group" in response.text
    assert "Confidence" in response.text
    assert "evidence" in response.text


def test_openapi_does_not_contain_removed_routes(api_client) -> None:
    response = api_client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/duplicate-check" not in response.json()["paths"]
    assert "/api/v1/anomaly-detection" not in response.json()["paths"]
    assert "/api/v1/governance-guidance" not in response.json()["paths"]
    assert "/api/v1/configuration/{service_name}" not in response.json()["paths"]
    assert "/api/v1/duplicate-check/background" not in response.json()["paths"]
    assert "/api/v1/cleansing-enrichment/background" not in response.json()["paths"]
    assert "/api/v1/predictive" not in response.json()["paths"]
    assert "/api/v1/jobs/{job_id}" not in response.json()["paths"]

def test_search_route_accepts_field_rule_list_payload(api_client) -> None:
    response = api_client.post(
        "/api/v1/search",
        json=[
            {
                "key": "Material Name",
                "value": "Finished Product: Wireless Earbuds",
                "rule": "exact",
            },
            {
                "key": "Material Description",
                "value": "Wireless Earbuds",
                "rule": "fuzzy",
            },
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tenant_id"]
    assert payload["candidates"]
    assert payload["candidates"][0]["document_id"]
    assert payload["candidates"][0]["chunk_id"]
    assert "exact_matches" in payload["candidates"][0]
    assert "fuzzy_matches" in payload["candidates"][0]


def test_search_route_rejects_invalid_rule(api_client) -> None:
    response = api_client.post(
        "/api/v1/search",
        json=[
            {
                "key": "Material Name",
                "value": "Finished Product: Wireless Earbuds",
                "rule": "semantic",
            }
        ],
    )

    assert response.status_code == 422


def test_material_sds_analysis_route_returns_results(api_client) -> None:
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={
            "materials": [
                {
                    "material_name": "Industrial Solvent",
                    "material_description": "Solvent based cleaning fluid",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["results"]) == 1
    assert payload["results"][0]["material_index"] == 0
    assert payload["results"][0]["decision"] == "required"
    assert payload["results"][0]["is_sds_required"] is True
    assert payload["results"][0]["hazardous_categories"]
    assert payload["results"][0]["web_search_used"] is False
    assert payload["results"][0]["web_search_status"] == "not_requested"


def test_material_sds_analysis_route_defaults_online_search_to_false(api_client) -> None:
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={
            "materials": [
                {
                    "material_name": "Office Chair",
                    "material_description": "Ergonomic office seating",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["web_search_used"] is False
    assert response.json()["results"][0]["is_sds_required"] is False


def test_material_sds_analysis_route_preserves_batch_order(api_client) -> None:
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={
            "materials": [
                {
                    "material_name": "Industrial Solvent",
                    "material_description": "Solvent based cleaning fluid",
                },
                {
                    "material_name": "Office Chair",
                    "material_description": "Ergonomic office seating",
                },
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert [item["material_index"] for item in payload["results"]] == [0, 1]


def test_material_sds_analysis_route_rejects_empty_materials(api_client) -> None:
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={"materials": []},
    )

    assert response.status_code == 422


def test_material_sds_analysis_route_handles_blank_material_with_needs_review(api_client) -> None:
    response = api_client.post(
        "/api/v1/material-sds-analysis",
        json={
            "materials": [
                {
                    "material_type": "raw_material",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"][0]["decision"] == "needs_review"
    assert payload["results"][0]["is_sds_required"] is False
    assert payload["results"][0]["web_search_status"] == "not_requested"

def test_similarity_route_returns_candidates(api_client) -> None:
    response = api_client.post(
        "/api/v1/similarity",
        json={
            "query_text": "Acme Industrial Bangkok",
            "mode": "ALL",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "ALL"
    assert payload["candidates"]

def test_search_config_can_be_updated(api_client) -> None:
    response = api_client.put(
        "/api/v1/search/config",
        json={
            "fuzzy_threshold": 0.75,
            "max_results": 5,
            "expand_terms_enabled": False,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "fuzzy_threshold": 0.75,
        "max_results": 5,
        "expand_terms_enabled": False,
    }

def test_similarity_config_can_be_updated(api_client) -> None:
    response = api_client.put(
        "/api/v1/similarity/config",
        json={
            "vector_top_k": 7,
            "vector_min_score": 0.5,
            "graph_seed_top_k": 4,
            "graph_neighbor_cap_per_seed": 3,
            "graph_max_relation_candidates": 12,
            "graph_max_graph_chunk_candidates": 9,
            "max_results": 6,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "vector_top_k": 7,
        "vector_min_score": 0.5,
        "graph_seed_top_k": 4,
        "graph_neighbor_cap_per_seed": 3,
        "graph_max_relation_candidates": 12,
        "graph_max_graph_chunk_candidates": 9,
        "max_results": 6,
    }

def test_duplicate_background_job_route_accepts_file_ids(api_client) -> None:
    response = api_client.post(
        "/api/v1/import-jobs",
        json={"file_ids": ["file-a", "file-b"]},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job_id"]
    assert payload["status"] == "accepted"
    assert payload["file_ids"] == ["file-a", "file-b"]
