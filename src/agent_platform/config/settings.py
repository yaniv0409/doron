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
    navigation_timeout_ms: int = 15_000
    network_idle_timeout_ms: int = 15_000
    content_text_max_chars: int = 10_000
    max_links_per_page: int = 200
    extract_main_content_only: bool = True
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    viewport_width: int = 1440
    viewport_height: int = 900
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


class DocumentationSettings(BaseModel):
    kuzu_reference_path: Path = Path("docs/kuzu-notes.md")


class CompressionSettings(BaseModel):
    enabled: bool = True
    tool_enabled: bool = True
    threshold_ratio: float = 0.6
    fallback_budget_chars: int = 12_000
    min_growth_chars: int = 3_000
    max_notes: int = 12
    max_findings: int = 12
    max_tool_summaries: int = 20
    timeout_seconds: int = 60


class RuntimeSettings(BaseModel):
    agent_run_timeout_seconds: int = 180


class DebugSettings(BaseModel):
    disable_browser_tools: bool = False
    disable_model_switch_tool: bool = False
    disable_compression_tool: bool = False
    disable_db_write_tool: bool = False


class AppSettings(BaseModel):
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    traces: TraceSettings = Field(default_factory=TraceSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    docs: DocumentationSettings = Field(default_factory=DocumentationSettings)
    compression: CompressionSettings = Field(default_factory=CompressionSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    debug: DebugSettings = Field(default_factory=DebugSettings)
    models: list[ModelSettings] = Field(
        default_factory=lambda: [
            ModelSettings(name="openai/gpt-4.1-mini", rank=10, is_default=True),
            ModelSettings(name="openai/gpt-5.2", rank=100),
        ]
    )
