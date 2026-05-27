"""NotifyAgent — 将变更事件分发到各通知渠道。

当前支持：
  - console（Rich 打印，始终启用）
  - TODO: 邮件 / Slack / 飞书 / Webhook
"""
import logging
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .monitor_agent import ChangeEvent, ChangeType

logger = logging.getLogger(__name__)
console = Console()

_TYPE_STYLE = {
    ChangeType.NEW_COMPETITION: "[bold green]NEW[/bold green]",
    ChangeType.DEADLINE_CHANGE: "[bold yellow]DEADLINE CHANGE[/bold yellow]",
    ChangeType.PARTICIPANT_SURGE: "[bold cyan]PARTICIPANT SURGE[/bold cyan]",
    ChangeType.COMPETITION_ENDED: "[dim]ENDED[/dim]",
}


class NotifyAgent:
    """接收 ChangeEvent 列表，输出通知。"""

    def notify(self, events: list[ChangeEvent]) -> None:
        if not events:
            logger.debug("无变更事件")
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        table = Table(title=f"竞赛变更通知  {ts}", show_lines=True, border_style="blue")
        table.add_column("类型", width=20)
        table.add_column("ID", width=8)
        table.add_column("标题", ratio=2)
        table.add_column("详情", ratio=1)

        for ev in events:
            style = _TYPE_STYLE.get(ev.change_type, ev.change_type.value)
            detail_str = "  ".join(f"{k}={v}" for k, v in ev.detail.items())
            table.add_row(style, str(ev.competition_id), ev.title, detail_str)

        console.print(table)

        # TODO: 接入邮件 / Slack / 飞书
        for ev in events:
            logger.info("变更事件: %s", ev)
