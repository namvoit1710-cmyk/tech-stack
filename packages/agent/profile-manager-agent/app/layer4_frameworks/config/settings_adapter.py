from agent_sdk import Settings
from pydantic import Field

class AppSettingsAdapter(Settings):
    """
    Extend SDK Settings.
    Inherits all SDK fields and allows adding new properties.
    """

    OPENAI_API_KEY : str = Field(default="")
    PROFILE_MANAGEMENT_BASE_URL: str = Field(default="")
    TOKEN_URL: str = Field(default="")
    CLIENT_ID: str = Field(default="")
    CLIENT_SECRET: str = Field(default="")

settings = AppSettingsAdapter()