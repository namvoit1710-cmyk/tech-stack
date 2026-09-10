from uuid6 import uuid7

from app.layer2_application.interfaces.uuid_generator_port import IUUIDGeneratorPort

class UUIDv7Generator(IUUIDGeneratorPort):
    """UUIDv7 Generator implementation using the uuid6       library.
    
    This class provides a concrete implementation of the IUUIDGeneratorPort interface,
    generating UUID version 7 identifiers for use in the domain layer.
    """
    @staticmethod
    def generate_uuid() -> str:
        """Generate a new UUIDv7 string.
        
        Returns:
            A new UUIDv7 as a string.
        """
        return str(uuid7())