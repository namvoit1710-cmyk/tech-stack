from app.layer1_domain.value_objects.http_method import HttpMethod
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.body_type import BodyType


class TestHttpMethod:
    def test_values(self):
        assert HttpMethod.GET == "GET"
        assert HttpMethod.POST == "POST"
        assert HttpMethod.PUT == "PUT"
        assert HttpMethod.PATCH == "PATCH"
        assert HttpMethod.DELETE == "DELETE"
        assert HttpMethod.HEAD == "HEAD"
        assert HttpMethod.OPTIONS == "OPTIONS"

    def test_is_string(self):
        assert isinstance(HttpMethod.GET, str)


class TestAuthType:
    def test_values(self):
        assert AuthType.NONE == "none"
        assert AuthType.BEARER == "bearer"
        assert AuthType.BASIC == "basic"
        assert AuthType.API_KEY == "api_key"

    def test_is_string(self):
        assert isinstance(AuthType.NONE, str)


class TestBodyType:
    def test_values(self):
        assert BodyType.NONE == "none"
        assert BodyType.RAW_JSON == "raw_json"
        assert BodyType.RAW_TEXT == "raw_text"
        assert BodyType.STRUCTURED == "structured"
        assert BodyType.FORM_URLENCODED == "form_urlencoded"
        assert BodyType.FILE == "file"

    def test_is_string(self):
        assert isinstance(BodyType.NONE, str)
