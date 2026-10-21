from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

MAX_JPEG_QUALITY = 90  # JPEG quality (saving, resampling, etc.)
MAX_JPEG_COUNT = 100
MAX_JPEG_SIZE = 100_000_000  # 100 MB
TILE_SIZE = 256
OVERLAP = 1
SLIDE_ROOT = Path("/tmp/mrxs-slides")
SLIDE_ROOT.mkdir(parents=True, exist_ok=True)
SUPPORTED_FILETYPES = (".mrxs", ".zip", ".jpg", ".jpeg")


class Config(BaseSettings):
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_secure: bool
    minio_bucket: str

    openrouter_api_key: str
    openrouter_base_url: str
    openrouter_site_url: str
    openrouter_app_name: str

    analysis_models: str

    max_crop_pixels: int
    cors_origins: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def models(self) -> list[str]:
        models = [x.strip() for x in self.analysis_models.split(",") if x.strip()]
        return list(dict.fromkeys(models))

    @property
    def cors_origin_list(self) -> list[str]:
        return [x.strip() for x in self.cors_origins.split(",") if x.strip()]


config = Config()
