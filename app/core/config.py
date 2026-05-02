from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Smart Notifier"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/smart_notifier"

    telegram_bot_token: str = ""
    telegram_poll_interval: float = 1.0

    scheduler_timezone: str = "Asia/Shanghai"

    web_username: str = "admin"
    web_password: str = "change_me"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()
