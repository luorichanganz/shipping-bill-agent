from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is declared for normal installs
    load_dotenv = None


class Settings(BaseModel):
    ocr_endpoint: str = "http://36.103.199.11:5002/process"
    ocr_task: str = "orderChange"
    ocr_connect_timeout: float = 10
    ocr_read_timeout: float = 300
    ocr_max_retries: int = Field(default=2, ge=0, le=5)
    ocr_public_dir: Path = Path("./tmp/ocr-public")
    ocr_public_base_url: str = "http://127.0.0.1:8000"
    charge_alias_file: Path = Path("./data/charge_aliases.json")

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    llm_model: str = "gpt-4.1-mini"
    llm_temperature: float = 0
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_model: str | None = None
    vision_temperature: float = 0
    vision_max_image_dimension: int = Field(default=2000, ge=512)

    confidence_threshold: float = Field(default=0.8, ge=0, le=1)
    max_attachment_depth: int = Field(default=4, ge=1)
    max_attachment_count: int = Field(default=30, ge=1)
    max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1)

    @classmethod
    def from_env(cls) -> Settings:
        if load_dotenv is not None:
            load_dotenv()

        public_dir = os.getenv("OCR_PUBLIC_DIR", "./tmp/ocr-public")
        return cls(
            ocr_endpoint=os.getenv("OCR_ENDPOINT", cls.model_fields["ocr_endpoint"].default),
            ocr_task=os.getenv("OCR_TASK", "orderChange"),
            ocr_connect_timeout=float(os.getenv("OCR_CONNECT_TIMEOUT", "10")),
            ocr_read_timeout=float(os.getenv("OCR_READ_TIMEOUT", "300")),
            ocr_max_retries=int(os.getenv("OCR_MAX_RETRIES", "2")),
            ocr_public_dir=Path(public_dir),
            ocr_public_base_url=os.getenv("OCR_PUBLIC_BASE_URL", "http://127.0.0.1:8000"),
            charge_alias_file=Path(os.getenv("CHARGE_ALIAS_FILE", "./data/charge_aliases.json")),
            openai_api_key=os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or None,
            llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0")),
            vision_api_key=(
                os.getenv("VISION_API_KEY")
                or os.getenv("LLM_API_KEY")
                or os.getenv("OPENAI_API_KEY")
                or None
            ),
            vision_base_url=(
                os.getenv("VISION_BASE_URL")
                or os.getenv("LLM_BASE_URL")
                or os.getenv("OPENAI_BASE_URL")
                or None
            ),
            vision_model=os.getenv("VISION_MODEL") or None,
            vision_temperature=float(os.getenv("VISION_TEMPERATURE", "0")),
            vision_max_image_dimension=int(os.getenv("VISION_MAX_IMAGE_DIMENSION", "2000")),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.8")),
            max_attachment_depth=int(os.getenv("MAX_ATTACHMENT_DEPTH", "4")),
            max_attachment_count=int(os.getenv("MAX_ATTACHMENT_COUNT", "30")),
            max_attachment_bytes=int(os.getenv("MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024))),
        )
