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
        existing = existing or {}
        script = FetchScript(self._codabench, self._config)
        comps, result = script.run(existing=existing)

        # 复用已有翻译，避免对未变更内容重复调用翻译 API
        for comp in comps:
            ex = existing.get(comp.id)
            if not ex:
                continue
            if not comp.description_zh and ex.description_zh:
                comp.description_zh = ex.description_zh
            for page in comp.pages:
                ex_page = next((p for p in ex.pages if p.index == page.index), None)
                if ex_page and not page.content_zh and ex_page.content_zh:
                    page.content_zh = ex_page.content_zh

        return {c.id: c for c in comps}, result
