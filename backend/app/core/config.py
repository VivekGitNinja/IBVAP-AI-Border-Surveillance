from typing import Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "IBVAP"
    environment: str = "development"
    secret_key: str = ""
    database_url: str = "sqlite:///./ibvap.db"
    jwt_secret: str = "ibvap-military-grade-secure-jwt-secret-key-32b-plus-entropy-2026-ops"
    jwt_expire_minutes: int = 1440
    redis_url: str = "redis://localhost:6379/0"
    evidence_dir: str = "./data/evidence"
    allowed_origins: str = "http://localhost:5173,http://localhost:8001,http://127.0.0.1:5173,http://127.0.0.1:8001"
    require_auth: bool = True
    log_level: str = "INFO"
    log_format: str = "json"
    enable_metrics: bool = True
    enable_demo: bool = False
    enable_synthetic_cameras: bool = False
    allow_demo_data: bool = False
    upload_dir: str = "./storage/uploads"
    max_upload_size_mb: int = 500
    allowed_video_extensions: str = "mp4,mov,avi,mkv,webm,jpg,jpeg,png,webp"

    # Computer Vision & Intelligence Extensions
    zone_cooldown_seconds: float = 10.0
    loitering_seconds: float = 60.0
    crowd_min_count: int = 5
    crowd_window_seconds: float = 30.0
    rapid_speed_threshold: float = 200.0
    enable_anpr: bool = True
    anpr_ocr_engine: str = "auto"
    night_luma_threshold: float = 60.0
    night_enhance: bool = True
    enable_face_recognition: bool = False
    face_match_threshold: float = 0.363
    face_blur: bool = True
    ffmpeg_h264_transcode: bool = True
    c2_webhook_url: str = ""
    c2_webhook_secret: str = "c2-tactical-secret-key"


    # Motion Fallback Noise Gating & Confidence Flags
    motion_min_area: int = 600
    motion_persistence_frames: int = 3
    motion_conf_floor: float = 0.55

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    def __repr__(self) -> str:
        fields = []
        for k, v in self.__dict__.items():
            if "secret" in k.lower() or "password" in k.lower() or "token" in k.lower():
                fields.append(f"{k}='***REDACTED***'")
            else:
                fields.append(f"{k}={repr(v)}")
        return f"Settings({', '.join(fields)})"

    def __str__(self) -> str:
        return self.__repr__()


KNOWN_INSECURE_SECRETS = {
    "ibvap-military-grade-secure-jwt-secret-key-32b-plus-entropy-2026-ops",
    "ibvap-super-secret-key-change-in-production-2026",
    "dev-super-secret-key-change-in-prod-min-32-chars-long",
    "c2-tactical-secret-key",
    "default_c2_secret_do_not_use_in_prod",
    "secret",
    "changeme",
    "password",
    "12345678",
    "admin123",
}


class SecurityConfigurationError(RuntimeError):
    """Raised when security configuration violates fail-closed production invariants."""
    pass


def validate_security_configuration(s: Optional[Settings] = None, **kwargs: Any) -> None:
    """Validate security parameters and enforce fail-closed startup behavior in production."""
    if s is None:
        if kwargs:
            clean_kwargs = dict(kwargs)
            if "jwt_secret_key" in clean_kwargs and "jwt_secret" not in clean_kwargs:
                clean_kwargs["jwt_secret"] = clean_kwargs.pop("jwt_secret_key")
            s = Settings(**clean_kwargs)
        else:
            s = settings

    if s.environment in ("production", "staging"):
        # 1. Secret Key validation
        if s.secret_key and s.secret_key.strip() in KNOWN_INSECURE_SECRETS:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: SECRET_KEY contains an insecure repository default."
            )
        if "secret_key" in kwargs and kwargs["secret_key"] in KNOWN_INSECURE_SECRETS:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: SECRET_KEY contains an insecure repository default."
            )

        # 2. JWT secret validation
        if not s.jwt_secret or not s.jwt_secret.strip():
            raise SecurityConfigurationError(
                "FATAL [P0-01]: JWT_SECRET must be configured in production environment."
            )
        if s.jwt_secret.strip() in KNOWN_INSECURE_SECRETS:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: JWT_SECRET contains an insecure repository default. Provide an authentic high-entropy secret."
            )
        if len(s.jwt_secret.strip()) < 32:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: JWT_SECRET must be at least 32 characters long in production."
            )

        # 3. C2 Webhook secret validation
        if not s.c2_webhook_secret or not s.c2_webhook_secret.strip():
            raise SecurityConfigurationError(
                "FATAL [P0-01]: C2_WEBHOOK_SECRET must be configured in production environment."
            )
        if s.c2_webhook_secret.strip() in KNOWN_INSECURE_SECRETS:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: C2_WEBHOOK_SECRET contains an insecure repository default."
            )
        if len(s.c2_webhook_secret.strip()) < 16:
            raise SecurityConfigurationError(
                "FATAL [P0-01]: C2_WEBHOOK_SECRET must be at least 16 characters long in production."
            )


settings = Settings()
