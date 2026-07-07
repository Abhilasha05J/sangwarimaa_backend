from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # App
    APP_NAME: str = "Sangwari Maa API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql://neondb_owner:npg_VXIhT9H1GWUS@ep-winter-night-aolw1iqd-pooler.c-2.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    SECRET_KEY: str = "change-me-in-production"
    REFRESH_SECRET_KEY: str = "change-refresh-key-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # OTP
    OTP_EXPIRE_MINUTES: int = 5
    OTP_MAX_ATTEMPTS: int = 3
    RATE_LIMIT_OTP: str = "3/300"   # 3 per 5 min

    # SMS (MSG91)
    MSG91_API_KEY: str = ""
    MSG91_SENDER_ID: str = "SNGWRI"
    MSG91_TEMPLATE_ID: str = ""

    # Firebase
    FCM_SERVER_KEY: str = ""
    FIREBASE_PROJECT_ID: str = ""

    # AI / Chatbot
    OPENAI_API_KEY: str = ""
    GEMINI_API_KEY: str = "" 

    # AWS S3
    AWS_S3_BUCKET: str = "sangwari-maa-media"
    AWS_REGION: str = "ap-south-1"

    # Business rules
    BPCR_ALERT_THRESHOLD: int = 3
    ANC_OVERDUE_DAYS: int = 14
    ALERT_SLA_HOURS: int = 2

    # CORS
    ALLOWED_ORIGINS: list[str] = ["*"]

    class Config:
        env_file = ".env"
        extra = "ignore"

    def validate_production(self):
        if not self.DEBUG:
            assert len(self.SECRET_KEY) >= 32, "SECRET_KEY must be at least 32 chars"
            assert len(self.REFRESH_SECRET_KEY) >= 32, "REFRESH_SECRET_KEY must be at least 32 chars"
            assert "change" not in self.SECRET_KEY.lower(), "Change SECRET_KEY!"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
