"""UUID Generator Port - Defines the interface for generating UUIDs in the domain layer."""
from typing import Protocol

class IUUIDGeneratorPort(Protocol):
    """Interface for UUID generator service.
    
    This port defines the contract for generating unique identifiers (UUIDs) for domain entities.
    Infrastructure layer provides the implementation (e.g., using Python's uuid library).
    """
    @staticmethod
    def generate_uuid() -> str:
        """Generate a new UUID string.
        
        Returns:
            A new UUID as a string.
        """
        ...