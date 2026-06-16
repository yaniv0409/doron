from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ModelSettings(BaseModel):
    name: str
    rank: int
    context_window: int | None = None
    cost_class: str = "standard"
    supports_tools: bool = True
    supports_structured_output: bool = True
    is_default: bool = False


class OpenRouterSettings(BaseModel):
    api_key: str | None = None
    base_url: str = "https://openrouter.ai/api/v1"
    app_url: str | None = None
    app_title: str = "agent-platform"
    embedding_model: str = "openai/text-embedding-3-small"


class LoggingSettings(BaseModel):
    directory: Path = Path("logs")
    max_bytes: int = 2_000_000
    backup_count: int = 5


class TraceSettings(BaseModel):
    directory: Path = Path("traces")
    checkpoint_directory: Path = Path("traces/checkpoints")


class BrowserSettings(BaseModel):
    headless: bool = True
    default_timeout_ms: int = 20_000


class DocumentationSettings(BaseModel):
    kuzu_reference_path: Path = Path("docs/kuzu-notes.md")


class AppSettings(BaseModel):
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    traces: TraceSettings = Field(default_factory=TraceSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    docs: DocumentationSettings = Field(default_factory=DocumentationSettings)
    models: list[ModelSettings] = Field(
        default_factory=lambda: [
            ModelSettings(name="openai/gpt-4.1-mini", rank=10, is_default=True),
            ModelSettings(name="openai/gpt-5.2", rank=100),
        ]
    )
