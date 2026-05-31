"""Application configuration.

All runtime configuration is declared here as a single typed `Settings`
object and loaded from environment variables / the local `.env` file.
Import the shared instance with:

    from config import settings

Never read `os.environ` directly elsewhere in the codebase — funnel
everything through this module so configuration has one source of truth.
"""

from functools import lru_cache

from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application metadata ----
    app_name: str = "Salary Slip Automation API"
    app_version: str = "1.0.0"
    debug: bool = False

    # ---- Business / storage ----
    company_name: str = "Nippon Toyota"
    database_path: str = "payroll.db"

    # ---- CORS ----
    # Comma-separated list in .env, e.g.
    # CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app
    cors_origins: str = "http://localhost:3000"

    # ---- SMTP (consumed later by the email dispatcher) ----
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: EmailStr | None = None
    smtp_from_name: str = "Payroll Department"

    # ---- Mailtrap HTTP API (optional alternative to SMTP) ----
    # When both are set, the app sends over HTTPS via Mailtrap's API instead
    # of SMTP — useful when local networks/antivirus break SMTP/STARTTLS.
    mailtrap_api_token: str = ""
    mailtrap_inbox_id: str = ""

    # ---- Brevo HTTP API (real delivery over HTTPS) ----
    # Set this to send real email via Brevo's API. Works on hosts that block
    # outbound SMTP (e.g. Render free tier). Takes priority over SMTP/Mailtrap.
    brevo_api_key: str = ""

    # ---- Uploads / limits ----
    max_upload_bytes: int = Field(default=10 * 1024 * 1024)  # 10 MB safety cap

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse the comma-separated CORS string into a clean list."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the .env file is read exactly once per process."""
    return Settings()


settings = get_settings()