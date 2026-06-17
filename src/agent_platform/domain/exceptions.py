class AgentPlatformError(Exception):
    """Base application error."""


class ConfigurationError(AgentPlatformError):
    """Invalid or missing configuration."""


class BrowserError(AgentPlatformError):
    """Browser interaction failed."""


class DatabaseError(AgentPlatformError):
    """Database interaction failed."""


class DocumentationError(AgentPlatformError):
    """Documentation lookup failed."""


class ModelError(AgentPlatformError):
    """Model interaction failed."""


class ModelSwitchRequested(AgentPlatformError):
    """Signal used to restart execution on a different model."""

    def __init__(self, target_model: str) -> None:
        super().__init__(f"model switch requested: {target_model}")
        self.target_model = target_model


class ContextRefreshRequested(AgentPlatformError):
    """Signal used to restart execution with compressed context."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"context refresh requested: {reason}")
        self.reason = reason


class OutputValidationError(AgentPlatformError):
    """Structured output failed validation."""


class RequestValidationError(AgentPlatformError):
    """Mission request validation failed."""
