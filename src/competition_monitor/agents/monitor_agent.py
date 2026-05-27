"""MonitorAgent — 对比前后两次竞赛快照，检测变更事件。

变更类型：
  - NEW_COMPETITION   : 新出现的竞赛
  - DEADLINE_CHANGE   : 截止日期变更
  - PARTICIPANT_SURGE : 参与人数显著增加
  - COMPETITION_ENDED : 竞赛已结束（截止日期已过）

TODO: 实现通知分发（邮件/Slack/飞书等）。
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from ..platforms.codabench import Competition

logger = logging.getLogger(__name__)


class ChangeType(str, Enum):
    NEW_COMPETITION = "new_competition"
    DEADLINE_CHANGE = "deadline_change"
    PARTICIPANT_SURGE = "participant_surge"
    COMPETITION_ENDED = "competition_ended"


@dataclass
class ChangeEvent:
    change_type: ChangeType
    competition_id: int
    title: str
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.change_type.value}] [{self.competition_id}] {self.title}"


class MonitorAgent:
    """对比两次快照，产生 ChangeEvent 列表。"""

    PARTICIPANT_SURGE_THRESHOLD = 0.2  # 参与人数增幅超过 20% 视为显著增加

    def diff(
        self,
        previous: dict[int, Competition],
        current: dict[int, Competition],
    ) -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        today = date.today().isoformat()

        for cid, comp in current.items():
            if cid not in previous:
                events.append(ChangeEvent(
                    change_type=ChangeType.NEW_COMPETITION,
                    competition_id=cid,
                    title=comp.title,
                ))
                continue

            prev = previous[cid]

            # 截止日期变更
            prev_end = _last_end(prev)
            curr_end = _last_end(comp)
            if curr_end and prev_end and curr_end != prev_end:
                events.append(ChangeEvent(
                    change_type=ChangeType.DEADLINE_CHANGE,
                    competition_id=cid,
                    title=comp.title,
                    detail={"old_end": prev_end, "new_end": curr_end},
                ))

            # 参与人数显著增加
            if prev.participant_count > 0:
                ratio = (comp.participant_count - prev.participant_count) / prev.participant_count
                if ratio >= self.PARTICIPANT_SURGE_THRESHOLD:
                    events.append(ChangeEvent(
                        change_type=ChangeType.PARTICIPANT_SURGE,
                        competition_id=cid,
                        title=comp.title,
                        detail={
                            "old_count": prev.participant_count,
                            "new_count": comp.participant_count,
                            "ratio": round(ratio, 3),
                        },
                    ))

        # 竞赛已结束（本次快照中已消失，或截止日期早于今天）
        for cid, prev_comp in previous.items():
            end = _last_end(prev_comp)
            if end and end < today and cid not in current:
                events.append(ChangeEvent(
                    change_type=ChangeType.COMPETITION_ENDED,
                    competition_id=cid,
                    title=prev_comp.title,
                    detail={"end": end},
                ))

        return events


def _last_end(comp: Competition) -> str | None:
    """取最后一个 phase 的结束时间，或回退到 description 中的日期。"""
    if comp.phases:
        ends = [p.end for p in comp.phases if p.end]
        if ends:
            return max(ends)
    return None
