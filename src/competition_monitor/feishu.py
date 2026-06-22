"""飞书 Webhook 推送。"""
import logging

import httpx

from .agents.monitor_agent import ChangeEvent, ChangeType
from .platforms.codabench import Competition

logger = logging.getLogger(__name__)

_CODABENCH_BASE = "https://www.codabench.org"
_PAGES_BASE = "https://623-wzy.github.io/compitition-monitor"


def _fmt_date(s: str | None) -> str:
    return s[:10] if s else "—"


def _comp_line(comp: Competition) -> str:
    detail_url = f"{_PAGES_BASE}/{comp.id}.html"
    phases_str = "　".join(
        f"{p.name}: {_fmt_date(p.start)} → {_fmt_date(p.end)}"
        for p in comp.phases
    ) or "暂无阶段信息"
    return f"**[{comp.title}]({detail_url})**\n{phases_str}\n参与者 {comp.participant_count} 人"


def push_error(webhook_url: str, title: str, detail: str) -> None:
    """推送异常告警到飞书。"""
    content = f"**⚠️ {title}**\n\n{detail}"
    payload = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "body": {
                "elements": [{"tag": "markdown", "content": content}]
            },
            "header": {
                "title": {"tag": "plain_text", "content": "Codabench 竞赛监测异常"},
                "template": "red",
            },
        },
    }
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.error("飞书异常告警推送失败: %s", e)


def push_updates(
    webhook_url: str,
    events: list[ChangeEvent],
    snapshot: dict[int, Competition],
    expired_ids: list[int],
    expired_titles: list[str],
) -> None:
    """推送本次监测的新增、变更、过期摘要到飞书。"""
    new_ids = [e.competition_id for e in events if e.change_type == ChangeType.NEW_COMPETITION]

    sections = []

    if new_ids:
        lines = ["**🆕 新增竞赛**"]
        for cid in new_ids:
            comp = snapshot.get(cid)
            if comp:
                lines.append(_comp_line(comp))
        sections.append("\n\n".join(lines))

    if expired_ids:
        lines = ["**🗑️ 过期清理**"]
        for title in expired_titles:
            lines.append(f"- {title}")
        sections.append("\n".join(lines))

    if not sections:
        return

    update_url = f"{_PAGES_BASE}/update.html"
    content = "\n\n---\n\n".join(sections) + f"\n\n[查看完整更新]({update_url})"

    payload = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "body": {
                "elements": [{"tag": "markdown", "content": content}]
            },
            "header": {
                "title": {"tag": "plain_text", "content": "Codabench 竞赛监测更新"},
                "template": "blue",
            },
        },
    }

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            logger.error("飞书推送失败: %s", data)
        else:
            logger.info("飞书推送成功")
    except Exception as e:
        logger.error("飞书推送异常: %s", e)


def push(webhook_url: str, comps: list[Competition]) -> None:
    """手动推送竞赛列表到飞书（notify 命令使用）。"""
    if not comps:
        logger.warning("竞赛列表为空，跳过推送")
        return

    lines = [f"**📋 Codabench 竞赛监测 — 共 {len(comps)} 个进行中竞赛**\n"]
    for comp in comps:
        lines.append(_comp_line(comp))

    update_url = f"{_PAGES_BASE}/index.html"
    content = "\n\n---\n\n".join(lines) + f"\n\n[查看完整列表]({update_url})"

    payload = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "body": {
                "elements": [{"tag": "markdown", "content": content}]
            },
            "header": {
                "title": {"tag": "plain_text", "content": "Codabench 竞赛监测"},
                "template": "blue",
            },
        },
    }

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0:
            logger.error("飞书推送失败: %s", data)
        else:
            logger.info("飞书推送成功，共 %d 条竞赛", len(comps))
    except Exception as e:
        logger.error("飞书推送异常: %s", e)
