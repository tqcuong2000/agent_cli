import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """
    Application Settings managed by Pydantic.
    Loads from environment variables or a .env file.
    """
    # Example configuration fields
    app_name: str = Field(default="agent_cli", description="The name of the application")
    debug_mode: bool = Field(default=False, description="Enable debug mode")
    
    # Model Config
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        # Prefix for environment variables, e.g., AGENT_CLI_DEBUG_MODE=True
        env_prefix="AGENT_CLI_" 
    )

# A global instance of the settings to be imported and used across the app
config = Settings()
