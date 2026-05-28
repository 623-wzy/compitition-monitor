"""competition-monitor CLI 入口。"""
import logging
import signal
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import load_config
from .platforms.codabench import CodabenchClient
from .scheduler import MonitorScheduler
from .store import StateStore

console = Console()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@click.group()
@click.version_option(package_name="competition-monitor")
def main():
    """Codabench 竞赛 24h 监测机器人"""


# ---------------------------------------------------------------------------
# start — 启动持续监测
# ---------------------------------------------------------------------------


@main.command()
@click.option("--run-now", is_flag=True, default=False, help="启动后立即执行一次监测")
@click.option("--interval", type=int, default=None, help="覆盖配置中的间隔小时数")
def start(run_now: bool, interval: int | None):
    """启动持续监测（前台运行，Ctrl+C 退出）。"""
    try:
        config = load_config()
    except RuntimeError as e:
        console.print(f"[bold red]配置错误：[/bold red] {e}")
        raise SystemExit(1) from e

    if interval is not None:
        config.fetch_interval_hours = interval

    scheduler = MonitorScheduler(config)

    def _shutdown(sig, frame):
        console.print("\n[dim]正在停止监测...[/dim]")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    console.print(
        Panel(
            f"[bold cyan]Codabench 竞赛监测机器人已启动[/bold cyan]\n"
            f"监测间隔：每 {config.fetch_interval_hours} 小时\n"
            f"数据目录：{config.data_dir}\n\n"
            "[dim]Ctrl+C 退出[/dim]",
            title="competition-monitor",
            border_style="cyan",
        )
    )

    if run_now:
        with console.status("[bold green]立即执行首次监测...", spinner="dots"):
            n, summary = scheduler.run_once(push_index=True)
        console.print(f"[bold green]✓[/bold green] 首次监测完成，检测到 {n} 条变更")

    scheduler.start()

    try:
        signal.pause()
    except AttributeError:
        import time
        while True:
            time.sleep(3600)


# ---------------------------------------------------------------------------
# fetch — 一次性拉取
# ---------------------------------------------------------------------------


@main.command()
def fetch():
    """从 Codabench 拉取竞赛列表（单次）。"""
    import anthropic
    from .agents.fetch_agent import FetchAgent
    from .agents.translate_agent import TranslateAgent

    try:
        config = load_config()
    except RuntimeError as e:
        console.print(f"[bold red]配置错误：[/bold red] {e}")
        raise SystemExit(1) from e

    ai = anthropic.Anthropic(api_key=config.anthropic_api_key, base_url=config.anthropic_base_url)
    codabench = CodabenchClient(
        base_url=config.codabench_base_url,
        token=config.codabench_token,
        rate=config.rate_limit_rps,
    )
    agent = FetchAgent(codabench=codabench, config=config)
    translator = TranslateAgent(client=ai, model=config.haiku_model)
    store = StateStore(config.state_file)
    from .html_generator import HtmlGenerator
    html_gen = HtmlGenerator(config.html_dir)
    try:
        with console.status("[bold green]拉取中...", spinner="dots"):
            new_snapshot, result = agent.run(existing=store.load())
        with console.status("[bold green]翻译中...", spinner="dots"):
            translator.translate(new_snapshot)
        store.save(new_snapshot)
        store.update_timestamp()
        html_gen.generate(new_snapshot)
        store.git_push(len(new_snapshot))
        console.print(f"[bold green]✓[/bold green] {result}（已保存 {len(new_snapshot)} 条竞赛）")
        for err in result.errors:
            console.print(f"  [yellow]⚠[/yellow] {err}")
    finally:
        codabench.close()


# ---------------------------------------------------------------------------
# status — 显示上次运行状态
# ---------------------------------------------------------------------------


@main.command()
def status():
    """显示监测状态（上次运行时间、已跟踪竞赛数）。"""
    try:
        config = load_config()
    except RuntimeError as e:
        console.print(f"[bold red]配置错误：[/bold red] {e}")
        raise SystemExit(1) from e

    store = StateStore(config.state_file)
    snapshot = store.load()
    last_run_path = config.data_dir / "last_run.txt"
    last_run = last_run_path.read_text(encoding="utf-8").strip() if last_run_path.exists() else "—"

    console.print(
        Panel(
            f"上次运行：[cyan]{last_run}[/cyan]\n"
            f"已跟踪竞赛：[cyan]{len(snapshot)}[/cyan] 条\n"
            f"数据目录：{config.data_dir}",
            title="监测状态",
            border_style="blue",
        )
    )

    if snapshot:
        table = Table(show_lines=True, border_style="dim", expand=True)
        table.add_column("ID", width=8)
        table.add_column("标题", ratio=2)
        table.add_column("参与人数", width=10)
        for comp in list(snapshot.values())[:20]:
            table.add_row(str(comp.id), comp.title, str(comp.participant_count))
        if len(snapshot) > 20:
            table.add_row("...", f"还有 {len(snapshot) - 20} 条", "")
        console.print(table)


# ---------------------------------------------------------------------------
# notify — 手动推送竞赛列表到飞书
# ---------------------------------------------------------------------------


@main.command()
@click.option("--webhook", default=None, help="飞书 Webhook URL（优先于环境变量）")
def notify(webhook: str | None):
    """将当前竞赛列表推送到飞书机器人。"""
    from .feishu import push

    try:
        config = load_config()
    except RuntimeError as e:
        console.print(f"[bold red]配置错误：[/bold red] {e}")
        raise SystemExit(1) from e

    url = webhook or config.feishu_webhook
    if not url:
        console.print(
            "[bold red]未配置 Webhook URL。[/bold red]\n"
            "可通过以下任一方式提供：\n"
            "  1. competition-monitor notify --webhook https://open.feishu.cn/...\n"
            "  2. export FEISHU_WEBHOOK=https://open.feishu.cn/..."
        )
        raise SystemExit(1)

    store = StateStore(config.state_file)
    snapshot = store.load()
    if not snapshot:
        console.print("[yellow]快照为空，请先运行 fetch。[/yellow]")
        raise SystemExit(1)

    comps = sorted(
        snapshot.values(),
        key=lambda c: c.phases[0].start if c.phases and c.phases[0].start else "",
        reverse=True,
    )

    with console.status("[bold green]推送中...", spinner="dots"):
        push(url, comps)
    console.print(f"[bold green]✓[/bold green] 已推送 {len(comps)} 条竞赛到飞书")


# ---------------------------------------------------------------------------
# html — 从当前快照生成 HTML 页面
# ---------------------------------------------------------------------------


@main.command()
@click.option("--output", default=None, help="输出目录（默认 data/html/）")
def html(output: str | None):
    """从本地快照生成 HTML 竞赛目录页和详情页。"""
    from pathlib import Path
    from .html_generator import HtmlGenerator

    try:
        config = load_config()
    except RuntimeError as e:
        console.print(f"[bold red]配置错误：[/bold red] {e}")
        raise SystemExit(1) from e

    store = StateStore(config.state_file)
    snapshot = store.load()

    if not snapshot:
        console.print("[yellow]快照为空，请先运行 fetch 或 start --run-now。[/yellow]")
        raise SystemExit(1)

    out_dir = Path(output) if output else config.html_dir
    gen = HtmlGenerator(out_dir)

    with console.status("[bold green]生成 HTML...", spinner="dots"):
        gen.generate(snapshot)

    index_path = out_dir / "index.html"
    console.print(f"[bold green]✓[/bold green] HTML 已生成：{index_path}（{len(snapshot)} 个竞赛）")
