from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OMOP_DB_PORT: int
    OMOP_POSTGRES_USER: str
    OMOP_POSTGRES_PASSWORD: SecretStr
    OMOP_POSTGRES_DB: str

    @property
    def OMOP_DATABASE_URL(self) -> SecretStr:
        return SecretStr(
            f"postgresql://{self.OMOP_POSTGRES_USER}:{self.OMOP_POSTGRES_PASSWORD.get_secret_value()}@localhost:{self.OMOP_DB_PORT}/{self.OMOP_POSTGRES_DB}"
        )


# Eager load once (for app use)
_settings = Settings()  # type: ignore


# Accessor to allow override in tests
def get_settings() -> Settings:
    """
    Get the application settings.

    Returns:
        Settings: An instance of the Settings class containing configuration values.
    """
    return _settings  # type: ignore
