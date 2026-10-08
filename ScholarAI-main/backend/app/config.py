"""应用配置：所有 env 变量集中管理。"""
from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: int = 60

    # Embedding
    embedding_provider: str = "bge"
    # openai/qwen/remote 均使用 OpenAI-compatible /v1/embeddings 接口。
    # 保留 OPENAI_* 别名，兼容已有配置文件。
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "EMBEDDING_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"
        ),
    )
    embedding_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_BASE_URL", "OPENAI_BASE_URL"),
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        validation_alias=AliasChoices("EMBEDDING_MODEL", "OPENAI_EMBEDDING_MODEL"),
    )
    bge_model: str = "BAAI/bge-small-zh-v1.5"
    bge_device: str = "cpu"

    # Storage
    database_url: str = "sqlite:///./data/scholarai.db"
    paper_storage_dir: str = "./data/papers"
    reports_dir: str = "./data/reports"
    chroma_persist_dir: str = "./data/chroma"
    upload_dir: str = "./data/uploads"
    upload_max_size_mb: int = 50
    upload_max_pages: int = 500

    # Observability
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "scholarai"
    langchain_sample_rate: float = 1.0
    log_level: str = "INFO"

    # App
    cors_origins: list[str] = ["http://localhost:5173"]
    api_port: int = 8000
    research_timeout_seconds: int = 300


# ponytail: 单例足够，hot reload 也不会变
settings = Settings()
