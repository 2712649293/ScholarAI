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
    # Some OpenAI-compatible Qwen endpoints return the visible answer in the
    # reasoning field unless thinking is disabled. Leave empty for providers
    # that do not need this parameter; Qwen models are handled in llm.py.
    llm_reasoning_effort: str = ""
    llm_timeout_seconds: int = 60

    # 单篇论文的章节提取与汇总；模型留空时兼容原有主模型配置。
    small_llm_model: str = ""
    small_llm_base_url: str = ""
    small_llm_api_key: str = ""
    small_llm_reasoning_effort: str = ""

    # paper-search-mcp: the external MCP server used for paper discovery and
    # PDF retrieval.  The default command is installed with the backend.
    paper_search_mcp_command: str = "paper-search-mcp"
    paper_search_mcp_args: str = ""
    paper_search_mcp_timeout_seconds: float = Field(default=120, gt=0)

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

    # RAG: optional services are independent of the ordinary chat model.
    vision_base_url: str = ""
    vision_model: str = "gpt-6.1-sol"
    vision_api_key: str = ""
    vision_api_style: str = "responses"
    vision_timeout_seconds: float = Field(default=45, gt=0)
    vision_document_budget_seconds: float = Field(default=180, gt=0)
    vision_max_concurrency: int = Field(default=2, ge=1, le=8)
    vision_max_images_per_doc: int = Field(default=30, ge=0)
    rerank_endpoint: str = ""
    rerank_model: str = ""
    rerank_api_key: str = ""
    rerank_timeout_seconds: float = Field(default=15, gt=0)
    retrieval_branch_limit: int = Field(default=30, ge=1, le=200)
    rerank_candidate_limit: int = Field(default=40, ge=1, le=200)
    rag_context_tokens: int = Field(default=4000, ge=512)
    embedding_max_input_tokens: int = Field(default=512, ge=64)
    chunk_min_tokens: int = Field(default=120, ge=1)
    chunk_target_tokens: int = Field(default=300, ge=1)
    chunk_max_tokens: int = Field(default=450, ge=32)
    chunk_overlap_tokens: int = Field(default=60, ge=0)

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
