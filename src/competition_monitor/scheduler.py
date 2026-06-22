import logging
import threading
from datetime import datetime

import anthropic

from .agents.fetch_agent import FetchAgent
from .agents.monitor_agent import MonitorAgent
from .agents.notify_agent import NotifyAgent
from .agents.translate_agent import TranslateAgent
from .config import Config
from .feishu import push_error, push_updates
from .html_generator import HtmlGenerator
from .platforms.codabench import CodabenchClient
from .store import StateStore

logger = logging.getLogger(__name__)


class MonitorScheduler:
    def __init__(self, config: Config):
        self._config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._ai = anthropic.Anthropic(
            api_key=config.anthropic_api_key,
            base_url=config.anthropic_base_url,
        )
        self._codabench = CodabenchClient(
            base_url=config.codabench_base_url,
            token=config.codabench_token,
            rate=config.rate_limit_rps,
        )
        self._fetch_agent = FetchAgent(codabench=self._codabench, config=config)
        self._monitor_agent = MonitorAgent()
        self._notify_agent = NotifyAgent()
        self._translate_agent = TranslateAgent(client=self._ai, model=config.haiku_model)
        self._store = StateStore(config.state_file)
        self._html = HtmlGenerator(config.html_dir)

    # 新快照相比旧快照缩减超过此比例时中止更新，防止 API 故障导致数据清空
    SHRINK_ABORT_RATIO = 0.5

    def run_once(self, push_index: bool = False) -> tuple[int, str]:
        logger.info("开始监测循环 (%s)", datetime.now().strftime("%Y-%m-%d %H:%M"))

        previous = self._store.load()
        logger.info("旧快照：%d 条竞赛", len(previous))

        new_snapshot, fetch_result = self._fetch_agent.run(existing=previous)
        logger.info("%s", fetch_result)

        if previous and not new_snapshot:
            msg = f"API 返回空结果，旧快照有 {len(previous)} 条竞赛，中止本次更新以防数据丢失"
            logger.error(msg)
            self._notify_error("抓取结果为空", msg)
            self._store.update_timestamp()
            return 0, f"中止：API 返回空结果（旧快照 {len(previous)} 条）"

        if previous and len(new_snapshot) < len(previous) * self.SHRINK_ABORT_RATIO:
            msg = (
                f"新快照 {len(new_snapshot)} 条，旧快照 {len(previous)} 条，"
                f"缩减超过 {self.SHRINK_ABORT_RATIO:.0%}，中止本次更新"
            )
            logger.error(msg)
            self._notify_error("快照异常缩减", msg)
            self._store.update_timestamp()
            return 0, f"中止：快照异常缩减（{len(previous)} → {len(new_snapshot)}）"

        self._translate_agent.translate(new_snapshot)

        events = self._monitor_agent.diff(previous, new_snapshot)
        self._notify_agent.notify(events)

        expired = self._store.save(new_snapshot)
        self._store.update_timestamp()
        self._html.generate(new_snapshot)
        has_update = self._html.generate_update(events, new_snapshot, expired)

        if push_index:
            from .feishu import push as feishu_push
            comps = sorted(
                new_snapshot.values(),
                key=lambda c: c.phases[0].start if c.phases and c.phases[0].start else "",
                reverse=True,
            )
            feishu_push(self._config.feishu_webhook, comps)
        elif has_update:
            push_updates(
                self._config.feishu_webhook,
                events,
                new_snapshot,
                expired,
                [title for _, title in expired],
            )

        self._store.git_push(len(new_snapshot))

        logger.info("监测循环完成，变更事件 %d 条", len(events))
        return len(events), str(fetch_result)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="monitor-scheduler")
        self._thread.start()
        logger.info("MonitorScheduler 已启动，间隔 %d 小时", self._config.fetch_interval_hours)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        self._codabench.close()
        logger.info("MonitorScheduler 已停止")

    def _notify_error(self, title: str, detail: str) -> None:
        if self._config.feishu_webhook:
            push_error(self._config.feishu_webhook, title, detail)

    def _loop(self) -> None:
        interval = self._config.fetch_interval_hours * 3600
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.error("监测循环出错: %s", e, exc_info=True)
                self._notify_error("监测循环异常", str(e))
            self._stop.wait(interval)
