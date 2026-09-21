from uuid6 import uuid7
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder


class UuidBuilder(IUuidBuilder):
    def create_uuid7(self) -> str:
        return str(uuid7())
