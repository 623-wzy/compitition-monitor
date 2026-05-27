"""MonitorScheduler — 后台线程，按固定间隔执行完整监测循环。

一次循环：
  1. StateStore.load()     → 读取上次快照
  2. FetchAgent.run()      → 拉取 + 两阶段过滤 → 新快照 + 统计
  3. MonitorAgent.diff()   → 对比新旧快照，产生变更事件
  4. NotifyAgent.notify()  → 分发通知
  5. StateStore.save()     → 持久化新快照
"""
import logging
import threading
from datetime import datetime

import anthropic

from .agents.fetch_agent import FetchAgent
from .agents.monitor_agent import MonitorAgent
from .agents.notify_agent import NotifyAgent
from .agents.translate_agent import TranslateAgent
from .config import Config
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
        self._fetch_agent = FetchAgent(
            client=self._ai,
            codabench=self._codabench,
            config=config,
        )
        self._monitor_agent = MonitorAgent()
        self._notify_agent = NotifyAgent()
        self._translate_agent = TranslateAgent(
            client=self._ai,
            model=config.haiku_model,
        )
        self._store = StateStore(config.state_file)
        self._html = HtmlGenerator(config.html_dir)

    # ------------------------------------------------------------------

    def run_once(self) -> tuple[int, str]:
        """
        执行一次完整监测循环。

        Returns:
            (n_events, fetch_summary)
        """
        logger.info("开始监测循环 (%s)", datetime.now().strftime("%Y-%m-%d %H:%M"))

        # 1. 读取旧快照
        previous = self._store.load()
        logger.info("旧快照：%d 条竞赛", len(previous))

        # 2. 拉取 + 过滤
        new_snapshot, fetch_result = self._fetch_agent.run(existing=previous)
        logger.info("%s", fetch_result)

        # 3. 翻译描述和页面内容（只翻译尚无中文的字段）
        self._translate_agent.translate(new_snapshot)

        # 4. Diff
        events = self._monitor_agent.diff(previous, new_snapshot)

        # 5. 通知
        self._notify_agent.notify(events)

        # 6. 保存快照（含每个竞赛的 JSON）
        self._store.save(new_snapshot)
        self._store.update_timestamp()

        # 7. 生成 HTML 页面
        self._html.generate(new_snapshot)

        # 8. git push data/
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

    def _loop(self) -> None:
        interval = self._config.fetch_interval_hours * 3600
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as e:
                logger.error("监测循环出错: %s", e, exc_info=True)
            self._stop.wait(interval)
