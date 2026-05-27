"""StateStore — 持久化竞赛快照到 JSON，支持变更检测。"""
import json
import logging
import subprocess
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
        """覆盖写入当前快照，同时按竞赛 ID 存单独 JSON。"""
        data = {str(cid): comp.model_dump(by_alias=True) for cid, comp in snapshot.items()}
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("state 已保存，共 %d 条竞赛", len(snapshot))
        self.save_competitions_json(snapshot)

    def save_competitions_json(self, snapshot: dict[int, Competition]) -> None:
        """将每个竞赛保存为 data/competitions/<id>.json。"""
        comp_dir = self._path.parent / "competitions"
        comp_dir.mkdir(parents=True, exist_ok=True)
        for cid, comp in snapshot.items():
            out = comp_dir / f"{cid}.json"
            out.write_text(
                json.dumps(comp.model_dump(by_alias=True), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.debug("已保存 %d 个竞赛的单独 JSON 文件到 %s", len(snapshot), comp_dir)

    def update_timestamp(self) -> None:
        """在 state 文件旁写一个 last_run.txt，记录上次运行时间。"""
        ts_path = self._path.parent / "last_run.txt"
        ts_path.write_text(datetime.now().isoformat(), encoding="utf-8")

    def git_push(self, snapshot_size: int) -> None:
        """将 data/ 目录的变更提交并 push 到远端。"""
        repo = self._path.parent.parent  # data/ 的上级即 repo 根目录
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            subprocess.run(["git", "add", "data/", "docs/"], cwd=repo, check=True, capture_output=True)
            result = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=repo, capture_output=True,
            )
            if result.returncode == 0:
                logger.info("data/ 无变更，跳过 git push")
                return
            subprocess.run(
                ["git", "commit", "-m", f"data: update {snapshot_size} competitions [{ts}]"],
                cwd=repo, check=True, capture_output=True,
            )
            subprocess.run(["git", "push"], cwd=repo, check=True, capture_output=True)
            logger.info("git push 完成（%d 条竞赛）", snapshot_size)
        except subprocess.CalledProcessError as e:
            logger.error("git push 失败: %s", e.stderr.decode(errors="replace").strip())
