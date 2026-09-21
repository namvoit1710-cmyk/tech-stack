from agent_sdk import Settings

class AppSettingsAdapter(Settings):
    """
    Extend SDK Settings.
    Inherits all SDK fields and allows adding new properties.
    """

    # Add new Layer 2 specific fields
    FILE_URL: str = "default_file_url"  # will be picked from .env if present
    DATA_FACTORY_URL: str = "default_file_url"
    OPENAI_API_KEY : str = ""
    GOVERNAMENT_API_BASE_URL: str = ""

settings = Settings()