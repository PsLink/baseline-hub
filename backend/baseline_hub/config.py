from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HUB_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./hub.db"
    public_url: str = "http://localhost:8080"
    cookie_secure: bool = True
    upstream_url: str = ""
    upstream_proxy: str = ""
    catalog_repository: str = "PsLink/model-catalog"
    github_client_id: str = ""
    github_client_secret: str = ""
    # Dedicated Fernet key for AutoTune session tokens and GitHub publication credentials.
    credential_key: str = ""

    @model_validator(mode="after")
    def validate_urls(self):
        import re
        from urllib.parse import urlsplit

        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.catalog_repository):
            raise ValueError("Expected catalog repository as owner/repository")
        for value in [self.public_url, self.upstream_url]:
            if not value:
                continue
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
                raise ValueError("Expected an absolute HTTP(S) URL without credentials")
            if parsed.query or parsed.fragment:
                raise ValueError("Base URLs cannot contain a query or fragment")
        if self.upstream_proxy:
            parsed = urlsplit(self.upstream_proxy)
            if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
                raise ValueError("Expected an HTTP(S) or SOCKS5 upstream proxy URL")
        return self

    @property
    def api_cookie_path(self):
        from urllib.parse import urlsplit

        return urlsplit(self.public_url).path.rstrip("/") + "/api"

    @property
    def github_flow_path(self):
        return self.api_cookie_path + "/auth/github"

    @property
    def github_callback(self):
        return self.public_url.rstrip("/") + "/api/auth/github/callback"

    @property
    def github_enabled(self):
        return bool(self.github_client_id and self.github_client_secret)
