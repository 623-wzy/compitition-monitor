"""Agent 基类与进程内会话状态存储。"""
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Any


class AgentResult:
    def __init__(self, data: Any):
        self.token: str = str(uuid.uuid4())
        self.data: Any = data

    def to_tool_summary(self, preview_n: int = 5) -> dict:
        if isinstance(self.data, list):
            previews = [
                f"[{item.id}] {item.title}" if hasattr(item, "title") else str(item)
                for item in self.data[:preview_n]
            ]
            return {"result_token": self.token, "count": len(self.data), "preview": previews}
        return {"result_token": self.token, "data": self.data}


_MAX_STATE = 20
_STATE: OrderedDict[str, Any] = OrderedDict()


def store_result(token: str, data: Any) -> None:
    _STATE[token] = data
    _STATE.move_to_end(token)
    while len(_STATE) > _MAX_STATE:
        _STATE.popitem(last=False)


def retrieve_result(token: str) -> Any:
    if token not in _STATE:
        raise KeyError(f"SessionState 中找不到 token: {token!r}")
    return _STATE[token]


class BaseAgent(ABC):
    @abstractmethod
    def run(self, **kwargs) -> Any: ...
