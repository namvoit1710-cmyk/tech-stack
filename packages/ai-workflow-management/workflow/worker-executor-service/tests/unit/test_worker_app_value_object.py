import pytest
from app.layer1_domain.value_objects.worker_app import worker_app_from_endpoint


@pytest.mark.parametrize("url,expected", [
    ("https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com",
     "smdg-ai-tenant-1-worker-jira-worker"),
    ("http://localhost:8010", "localhost"),
    ("smdg-ai-tenant-1-worker-email-worker.cfapps.br10.hana.ondemand.com",
     "smdg-ai-tenant-1-worker-email-worker"),
    ("https://jira-worker.local:9000/execute", "jira-worker"),
    ("", ""),
    ("   ", ""),
    ("://", ""),
    ("https://", ""),
])
def test_derives_first_dns_label(url, expected):
    assert worker_app_from_endpoint(url) == expected
