"""FetchAgent — 协调 FetchScript，返回当次快照与统计。

调用方（MonitorScheduler）负责：
  - 传入 existing snapshot（来自 StateStore）
  - 用返回的 new_snapshot 做 diff，然后写回 StateStore
"""
import logging

import anthropic

from ..config import Config
from ..platforms.codabench import CodabenchClient, Competition
from .base import BaseAgent
from .fetch_script import BasicCompetition, FetchResult, FetchScript

logger = logging.getLogger(__name__)

# 重新导出，让外部只需 import fetch_agent 即可
__all__ = ["FetchAgent", "FetchResult"]


class FetchAgent(BaseAgent):
    def __init__(
        self,
        client: anthropic.Anthropic,
        codabench: CodabenchClient,
        config: Config,
    ):
        self._ai = client
        self._codabench = codabench
        self._config = config

    def run(  # type: ignore[override]
        self,
        existing: dict[int, Competition] | None = None,
    ) -> tuple[dict[int, Competition], FetchResult]:
        """
        拉取并过滤竞赛。

        Args:
            existing: 上次快照（{id: Competition}），用于跳过已知竞赛的详情 API 调用。
                      传 None 或空字典时视为全新运行。

        Returns:
            (new_snapshot, result)
              new_snapshot: 本次过滤后的完整竞赛快照 {id: Competition}
              result:       本次运行统计
        """
        script = FetchScript(self._codabench, self._config)
        basics, result = script.run(existing=existing or {})

        # 构建新快照：优先使用 detail（有 phases），无则用 Competition stub
        new_snapshot: dict[int, Competition] = {}
        for b in basics:
            if b.detail is not None:
                new_snapshot[b.id] = b.detail
            else:
                # detail API 失败时，用 list API 返回的基础 Competition 暂存
                new_snapshot[b.id] = Competition(
                    id=b.id,
                    title=b.title,
                    description=b.description,
                    participants_count=b.participant_count,
                )

        return new_snapshot, result
