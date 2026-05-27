"""StateStore — 持久化竞赛快照到 JSON，支持变更检测。"""
import json
import logging
from datetime import datetime
from pathlib import Path

from .platforms.codabench import Competition

logger = logging.getLogger(__name__)


class StateStore:
    """读写 monitor_state.json，保存上一次抓取的竞赛快照。"""

    def __init__(self, state_file: Path):
        self._path = state_file

    def load(self) -> dict[int, Competition]:
        """返回 {competition_id: Competition}，文件不存在时返回空字典。"""
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            return {int(k): Competition.model_validate(v) for k, v in raw.items()}
        except Exception as e:
            logger.warning("读取 state 文件失败: %s", e)
            return {}

    def save(self, snapshot: dict[int, Competition]) -> None:
        """覆盖写入当前快照。"""
        data = {str(cid): comp.model_dump(by_alias=True) for cid, comp in snapshot.items()}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("state 已保存，共 %d 条竞赛", len(snapshot))

    def update_timestamp(self) -> None:
        """在 state 文件旁写一个 last_run.txt，记录上次运行时间。"""
        ts_path = self._path.parent / "last_run.txt"
        ts_path.write_text(datetime.now().isoformat(), encoding="utf-8")
