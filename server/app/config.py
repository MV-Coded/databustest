from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Конфигурация сервера. Все секреты — из переменных окружения."""
    jwt_secret: str = "change-me-in-production-super-secret-key-2024"
    jwt_algorithm: str = "HS256"
    jwt_expire_seconds: int = 86400  # 24 hours

    class Config:
        env_file = ".env"