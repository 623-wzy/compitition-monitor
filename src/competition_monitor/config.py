import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"


def _claude_env() -> dict[str, str]:
    try:
        data = json.loads(_CLAUDE_SETTINGS.read_text(encoding="utf-8"))
        return data.get("env", {})
    except Exception:
        return {}


def _get(key: str, default: str = "") -> str:
    """env var → ~/.claude/settings.json → default."""
    return os.environ.get(key) or _claude_env().get(key, default)


@dataclass
class Config:
    anthropic_api_key: str = field(default_factory=lambda: _get("ANTHROPIC_API_KEY"))
    anthropic_base_url: str | None = field(
        default_factory=lambda: _get("ANTHROPIC_BASE_URL") or None
    )
    sonnet_model: str = field(
        default_factory=lambda: _get("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
    )
    haiku_model: str = field(
        default_factory=lambda: _get("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5-20251001")
    )

    codabench_base_url: str = "https://www.codabench.org"
    codabench_token: str | None = field(
        default_factory=lambda: os.environ.get("CODABENCH_TOKEN")
    )

    # 监控间隔（小时）
    fetch_interval_hours: int = field(
        default_factory=lambda: int(_get("MONITOR_INTERVAL_HOURS", "6"))
    )
    # API 限速（每秒请求数）
    rate_limit_rps: float = 2.0
    # 最多拉取竞赛数量
    max_competitions: int = 500
    # 截止日期超过此天数的视为长期开放赛事，过滤掉
    max_days_ahead: int = 730

    data_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("MONITOR_DATA_DIR", "./data"))
    )
    workspace_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("MONITOR_WORKSPACE_DIR", "./workspace"))
    )

    @property
    def competitions_md(self) -> Path:
        return self.data_dir / "competitions.md"

    @property
    def competitions_index(self) -> Path:
        return self.data_dir / "competitions_index.json"

    @property
    def state_file(self) -> Path:
        """记录上次已知竞赛集合，用于变更检测。"""
        return self.data_dir / "monitor_state.json"


def load_config() -> Config:
    cfg = Config()
    if not cfg.anthropic_api_key:
        raise RuntimeError(
            "未找到 ANTHROPIC_API_KEY。\n"
            "可通过以下任一方式提供：\n"
            "  1. export ANTHROPIC_API_KEY=sk-ant-...\n"
            f"  2. 写入 {_CLAUDE_SETTINGS} 的 env.ANTHROPIC_API_KEY 字段"
        )
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.workspace_dir.mkdir(parents=True, exist_ok=True)
    return cfg
