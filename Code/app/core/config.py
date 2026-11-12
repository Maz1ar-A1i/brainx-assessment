from __future__ import annotations

from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CaptchaLab AI"
    app_env: str = "development"
    database_path: str = "./captchalab.db"
    artifact_dir: str = "./artifacts"
    max_upload_bytes: int = 2 * 1024 * 1024
    max_csv_bytes: int = 5 * 1024 * 1024
    provider_timeout_seconds: float = 12.0
    browser_timeout_ms: int = 20000
    provider_widget_timeout_seconds: float = 25.0
    provider_interaction_timeout_seconds: float = 90.0
    provider_verification_timeout_seconds: float = 18.0
    provider_test_headless: bool = False
    app_base_url: str = "http://127.0.0.1:8000"
    chromium_executable: str = ""
    allowed_target_hosts: str = "localhost,127.0.0.1,::1,captchalab.test"
    browser_resource_hosts: str = Field(
        default=(
            "www.google.com,google.com,www.gstatic.com,recaptcha.net,www.recaptcha.net,"
            "js.hcaptcha.com,hcaptcha.com,newassets.hcaptcha.com,"
            "challenges.cloudflare.com,cloudflare.com"
        ),
        validation_alias=AliasChoices("ALLOWED_RESOURCE_HOSTS", "BROWSER_RESOURCE_HOSTS"),
    )

    # Official provider test credentials. Real production secrets belong in .env.
    recaptcha_site_key: str = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
    recaptcha_secret_key: str = "6LeIxAcTAAAAAGG-vFI1TnRWxMZNFuojJ4WifJWe"
    hcaptcha_site_key: str = "10000000-ffff-ffff-ffff-000000000001"
    hcaptcha_secret_key: str = "0x0000000000000000000000000000000000000000"
    turnstile_site_key: str = "1x00000000000000000000AA"
    turnstile_secret_key: str = "1x0000000000000000000000000000000AA"

    # Optional Gemini fallback for candidate-controlled synthetic challenges only.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"
    gemini_timeout_seconds: float = 12.0
    gemini_fallback_threshold: float = 0.50
    gemini_billing_tier: str = "free"
    gemini_input_usd_per_million: float = 0.75
    gemini_output_usd_per_million: float = 3.75

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @staticmethod
    def _parse_hosts(value: str) -> set[str]:
        hosts = set()
        for raw in (value or "").split(","):
            host = raw.strip().lower().rstrip(".")
            # Wildcards intentionally do not grant global access. This also
            # neutralizes the unsafe ALLOWED_TARGET_HOSTS=* from older .env files.
            if host and host not in {"*", "all"}:
                hosts.add(host)
        return hosts

    @property
    def allowed_hosts(self) -> set[str]:
        return self._parse_hosts(self.allowed_target_hosts)

    @property
    def resource_hosts(self) -> set[str]:
        return self._parse_hosts(self.browser_resource_hosts)

    @property
    def unsafe_wildcard_configured(self) -> bool:
        values = {item.strip().lower() for item in self.allowed_target_hosts.split(",")}
        return bool(values.intersection({"*", "all"}))


@lru_cache

def get_settings() -> Settings:
    return Settings()
