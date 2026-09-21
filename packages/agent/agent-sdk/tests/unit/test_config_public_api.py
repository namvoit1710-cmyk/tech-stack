from dataclasses import is_dataclass


def test_get_hana_credentials_importable_from_agent_sdk():
    from agent_sdk import get_hana_credentials

    assert callable(get_hana_credentials)


def test_hana_credentials_importable_from_agent_sdk():
    from agent_sdk import HanaCredentials

    assert is_dataclass(HanaCredentials)


def test_vcap_helpers_in_agent_sdk_all():
    import agent_sdk

    assert "get_hana_credentials" in agent_sdk.__all__
    assert "HanaCredentials" in agent_sdk.__all__


def test_config_package_re_exports_vcap_helpers():
    from agent_sdk.layer4_frameworks.config import HanaCredentials, get_hana_credentials

    assert callable(get_hana_credentials)
    assert is_dataclass(HanaCredentials)
