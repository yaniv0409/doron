from enum import Enum


class MissionStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class MaintenanceJobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ResultFormat(str, Enum):
    JSON_SCHEMA = "json_schema"
    TEXT = "text"


class LogCategory(str, Enum):
    API = "api"
    DB_AUDIT = "db.audit"
    DOCS_AUDIT = "docs.audit"
    MISSION = "mission"
    REASONING = "agent.reasoning"
    WEB_AUDIT = "web.audit"
