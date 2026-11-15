"""Configuração via variáveis de ambiente. Nenhum segredo fica no código."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return val or ""


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


@dataclass
class Settings:
    env: str = field(default_factory=lambda: _env("RSSP_ENV", "development"))
    database_url: str = field(default_factory=lambda: _env("RSSP_DATABASE_URL", "sqlite:///./data/rssp.db"))
    # Chave para assinar JWT e URLs temporárias. Obrigatória fora de dev.
    secret_key: str = field(default_factory=lambda: _env("RSSP_SECRET_KEY", ""))
    # Chave Fernet (base64 32 bytes) para cifrar credenciais de streaming em repouso.
    encryption_key: str = field(default_factory=lambda: _env("RSSP_ENCRYPTION_KEY", ""))
    storage_backend: str = field(default_factory=lambda: _env("RSSP_STORAGE_BACKEND", "local"))  # local | s3
    storage_path: str = field(default_factory=lambda: _env("RSSP_STORAGE_PATH", "./data/storage"))
    s3_bucket: str = field(default_factory=lambda: _env("RSSP_S3_BUCKET", ""))
    s3_endpoint: str = field(default_factory=lambda: _env("RSSP_S3_ENDPOINT", ""))
    s3_region: str = field(default_factory=lambda: _env("RSSP_S3_REGION", "us-east-1"))
    max_upload_mb: int = field(default_factory=lambda: _int("RSSP_MAX_UPLOAD_MB", 300))
    access_token_minutes: int = field(default_factory=lambda: _int("RSSP_ACCESS_TOKEN_MINUTES", 15))
    refresh_token_days: int = field(default_factory=lambda: _int("RSSP_REFRESH_TOKEN_DAYS", 14))
    public_base_url: str = field(default_factory=lambda: _env("RSSP_PUBLIC_BASE_URL", "http://localhost:8080"))
    cookie_secure: bool = field(default_factory=lambda: _env("RSSP_COOKIE_SECURE", "0") == "1")
    ffmpeg_bin: str = field(default_factory=lambda: _env("RSSP_FFMPEG", "ffmpeg"))
    ffprobe_bin: str = field(default_factory=lambda: _env("RSSP_FFPROBE", "ffprobe"))
    web_dir: str = field(default_factory=lambda: _env("RSSP_WEB_DIR", str(Path(__file__).resolve().parents[2] / "web")))
    login_rate_per_minute: int = field(default_factory=lambda: _int("RSSP_LOGIN_RATE_PER_MINUTE", 10))
    trust_proxy: bool = field(default_factory=lambda: _env("RSSP_TRUST_PROXY", "0") == "1")

    def validate(self) -> None:
        if self.env != "development":
            if len(self.secret_key) < 32:
                raise RuntimeError("RSSP_SECRET_KEY deve ter ao menos 32 caracteres em produção.")
            if not self.encryption_key:
                raise RuntimeError("RSSP_ENCRYPTION_KEY é obrigatória em produção.")
        # Apenas desenvolvimento: chaves geradas e guardadas fora do código, em data/.
        if not self.secret_key:
            import secrets
            self.secret_key = self._dev_key(".dev_secret_key", lambda: secrets.token_urlsafe(48))
        if not self.encryption_key:
            from cryptography.fernet import Fernet
            self.encryption_key = self._dev_key(".dev_encryption_key", lambda: Fernet.generate_key().decode())

    def _dev_key(self, name: str, gen) -> str:
        key_file = Path(self.storage_path).parent / name
        key_file.parent.mkdir(parents=True, exist_ok=True)
        if key_file.exists():
            return key_file.read_text().strip()
        val = gen()
        key_file.write_text(val)
        os.chmod(key_file, 0o600)
        return val


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.validate()
    return _settings


def reset_settings() -> None:
    """Usado pelos testes."""
    global _settings
    _settings = None
