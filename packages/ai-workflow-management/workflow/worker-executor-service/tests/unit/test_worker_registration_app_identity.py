from app.layer1_domain.entities.worker_registration import WorkerRegistration


def test_synthesized_flag_true_when_no_instance_id():
    w = WorkerRegistration(worker_type="jira_create_issue", endpoint_url="https://jira-worker.example.com")
    assert w.instance_synthesized is True
    assert w.instance_id == w.id          # the existing fallback still stands


def test_synthesized_flag_false_when_instance_supplied():
    w = WorkerRegistration(worker_type="jira_create_issue", instance_id="jira-worker_0",
                           endpoint_url="https://jira-worker.example.com")
    assert w.instance_synthesized is False
    assert w.id == "jira-worker_0:jira_create_issue"


def test_worker_app_derived_from_endpoint_when_absent():
    w = WorkerRegistration(
        worker_type="jira_create_issue",
        endpoint_url="https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com",
    )
    assert w.worker_app == "smdg-ai-tenant-1-worker-jira-worker"


def test_supplied_worker_app_wins_over_derivation():
    w = WorkerRegistration(worker_type="jira_create_issue", worker_app="jira-worker",
                           endpoint_url="https://smdg-ai-tenant-1-worker-jira-worker.cfapps.br10.hana.ondemand.com")
    assert w.worker_app == "jira-worker"


def test_blank_endpoint_leaves_worker_app_blank():
    assert WorkerRegistration(worker_type="code").worker_app == ""
