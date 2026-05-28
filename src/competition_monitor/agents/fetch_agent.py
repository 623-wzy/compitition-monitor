import logging

from ..config import Config
from ..platforms.codabench import CodabenchClient, Competition
from .fetch_script import FetchResult, FetchScript

logger = logging.getLogger(__name__)

__all__ = ["FetchAgent", "FetchResult"]


class FetchAgent:
    def __init__(self, codabench: CodabenchClient, config: Config):
        self._codabench = codabench
        self._config = config

    def run(
        self,
        existing: dict[int, Competition] | None = None,
    ) -> tuple[dict[int, Competition], FetchResult]:
        script = FetchScript(self._codabench, self._config)
        comps, result = script.run(existing=existing or {})
        return {c.id: c for c in comps}, result
