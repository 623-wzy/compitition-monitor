"""HtmlGenerator — 生成竞赛目录页 index.html 和各竞赛详情页 {id}.html。"""
import html
import json
import logging
from datetime import date, datetime
from pathlib import Path

from .platforms.codabench import Competition, Phase, classify_phase as _classify_phase_name

logger = logging.getLogger(__name__)

_CODABENCH_BASE = "https://www.codabench.org"


def _classify_phase(phase: Phase) -> str:
    return _classify_phase_name(phase.name)


def _fmt_date(s: str | None) -> str:
    if not s:
        return "—"
    try:
        return date.fromisoformat(s[:10]).strftime("%Y-%m-%d")
    except ValueError:
        return s[:10]


def _days_left(end: str | None) -> str:
    if not end:
        return ""
    try:
        delta = date.fromisoformat(end[:10]) - date.today()
        if delta.days < 0:
            return "<span class='ended'>已结束</span>"
        if delta.days == 0:
            return "<span class='urgent'>今天截止</span>"
        if delta.days <= 7:
            return f"<span class='urgent'>{delta.days} 天后</span>"
        return f"<span class='normal'>{delta.days} 天后</span>"
    except ValueError:
        return ""


def _short_desc(desc_zh: str | None, desc: str | None, limit: int = 200) -> str:
    text = (desc_zh or desc or "").strip()
    if not text:
        return "暂无描述"
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return html.escape(text)


def _phase_rows(phases: list[Phase]) -> str:
    if not phases:
        return "<p class='no-data'>暂无阶段信息</p>"
    rows = []
    for p in phases:
        kind = _classify_phase(p)
        badge = {"dev": "badge-dev", "test": "badge-test"}.get(kind, "badge-other")
        label = {"dev": "开发", "test": "测试"}.get(kind, "其他")
        rows.append(
            f"<tr>"
            f"<td><span class='badge {badge}'>{label}</span> {html.escape(p.name)}</td>"
            f"<td>{_fmt_date(p.start)}</td>"
            f"<td>{_fmt_date(p.end)}</td>"
            f"<td>{p.max_submissions_per_day if p.max_submissions_per_day is not None else '—'}</td>"
            f"</tr>"
        )
    return "".join(rows)


def _phase_summary(comp: Competition) -> dict:
    """返回供 card 展示的 dev/test 阶段摘要。"""
    summary = {"dev_start": None, "dev_end": None, "test_start": None, "test_end": None}
    for p in comp.phases:
        kind = _classify_phase(p)
        if kind == "dev":
            if summary["dev_start"] is None or (p.start and p.start < (summary["dev_start"] or "z")):
                summary["dev_start"] = p.start
            if summary["dev_end"] is None or (p.end and p.end > (summary["dev_end"] or "")):
                summary["dev_end"] = p.end
        elif kind == "test":
            if summary["test_start"] is None or (p.start and p.start < (summary["test_start"] or "z")):
                summary["test_start"] = p.start
            if summary["test_end"] is None or (p.end and p.end > (summary["test_end"] or "")):
                summary["test_end"] = p.end
    # fallback: 如果没有明确分类，用第一和最后一个 phase
    if not any(summary.values()) and comp.phases:
        first, last = comp.phases[0], comp.phases[-1]
        summary["dev_start"] = first.start
        summary["dev_end"] = first.end
        if len(comp.phases) > 1:
            summary["test_start"] = last.start
            summary["test_end"] = last.end
    return summary


_CSS = """
:root {
    --bg: #0f1117;
    --card-bg: #1a1d2e;
    --border: #2d3152;
    --accent: #6c8fff;
    --accent2: #a78bfa;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --dev: #34d399;
    --test: #f59e0b;
    --other: #94a3b8;
    --ended: #ef4444;
    --urgent: #f59e0b;
    --radius: 12px;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── Header ── */
.site-header { background: linear-gradient(135deg, #1a1d2e 0%, #0f1117 100%); border-bottom: 1px solid var(--border); padding: 24px 32px; display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
.site-header h1 { font-size: 1.5rem; font-weight: 700; color: var(--accent); }
.site-header .meta { color: var(--muted); font-size: .875rem; }

/* ── Grid ── */
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 20px; padding: 32px; max-width: 1600px; margin: 0 auto; }

/* ── Card ── */
.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; display: flex; flex-direction: column; gap: 12px; transition: border-color .2s, transform .2s; }
.card:hover { border-color: var(--accent); transform: translateY(-2px); }
.card-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 8px; }
.card-title { font-size: 1rem; font-weight: 600; line-height: 1.4; }
.card-title a { color: var(--text); }
.card-title a:hover { color: var(--accent); text-decoration: none; }
.card-id { font-size: .75rem; color: var(--muted); white-space: nowrap; }
.card-desc { font-size: .85rem; color: var(--muted); line-height: 1.6; flex: 1; }
.card-phases { display: flex; flex-direction: column; gap: 6px; }
.phase-row { display: flex; align-items: center; gap: 8px; font-size: .8rem; }
.phase-label { font-weight: 600; width: 38px; }
.phase-dates { color: var(--muted); }
.card-footer { display: flex; align-items: center; justify-content: space-between; padding-top: 8px; border-top: 1px solid var(--border); font-size: .8rem; }
.participants { color: var(--muted); }
.participants span { color: var(--text); font-weight: 600; }

/* ── Badges ── */
.badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: .75rem; font-weight: 600; }
.badge-dev { background: rgba(52,211,153,.15); color: var(--dev); }
.badge-test { background: rgba(245,158,11,.15); color: var(--test); }
.badge-other { background: rgba(148,163,184,.1); color: var(--other); }

/* ── Status labels ── */
.ended { color: var(--ended); font-size: .75rem; }
.urgent { color: var(--urgent); font-size: .75rem; }
.normal { color: var(--dev); font-size: .75rem; }

/* ── Detail page ── */
.detail-wrap { max-width: 960px; margin: 0 auto; padding: 32px; }
.detail-wrap h2 { font-size: 1.6rem; font-weight: 700; margin-bottom: 8px; }
.detail-meta { color: var(--muted); font-size: .875rem; margin-bottom: 24px; display: flex; flex-wrap: wrap; gap: 16px; }
.section { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 20px; }
.section h3 { font-size: 1rem; font-weight: 600; color: var(--accent); margin-bottom: 14px; }
table.phases-table { width: 100%; border-collapse: collapse; font-size: .875rem; }
table.phases-table th { text-align: left; color: var(--muted); font-weight: 500; padding: 6px 10px; border-bottom: 1px solid var(--border); }
table.phases-table td { padding: 8px 10px; border-bottom: 1px solid rgba(45,49,82,.5); }
.page-content { font-size: .875rem; line-height: 1.8; color: var(--muted); white-space: pre-wrap; word-break: break-word; }
.kv-grid { display: grid; grid-template-columns: 160px 1fr; gap: 8px 16px; font-size: .875rem; }
.kv-key { color: var(--muted); }
.kv-val { color: var(--text); }
.no-data { color: var(--muted); font-size: .85rem; }
.back-link { display: inline-block; margin-bottom: 20px; color: var(--muted); font-size: .875rem; }
.back-link:hover { color: var(--accent); }
.ext-link { font-size: .8rem; color: var(--accent); }
"""

_INDEX_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codabench 竞赛监测</title>
<style>{css}</style>
</head>
<body>
<header class="site-header">
  <h1>Codabench 竞赛监测</h1>
  <div class="meta">共 {count} 个进行中竞赛 · 更新于 {updated}</div>
</header>
<main class="grid">
{cards}
</main>
</body>
</html>"""

_DETAIL_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Codabench 竞赛监测</title>
<style>{css}</style>
</head>
<body>
<header class="site-header">
  <h1>Codabench 竞赛监测</h1>
  <div class="meta"><a href="index.html" style="color:var(--muted)">← 返回目录</a></div>
</header>
<div class="detail-wrap">
  <a class="back-link" href="index.html">← 返回竞赛目录</a>
  <h2>{title}</h2>
  <div class="detail-meta">
    <span>ID: {cid}</span>
    <span>创建者: {created_by}</span>
    <span>创建时间: {created_when}</span>
    <span>参与人数: {participants}</span>
    <span>提交次数: {submissions}</span>
    <a class="ext-link" href="{url}" target="_blank" rel="noopener">在 Codabench 查看 ↗</a>
  </div>

  <div class="section">
    <h3>竞赛描述</h3>
    <div class="page-content">{description}</div>
  </div>

  <div class="section">
    <h3>竞赛阶段</h3>
    <table class="phases-table">
      <thead><tr><th>阶段</th><th>开始</th><th>截止</th><th>每日提交上限</th></tr></thead>
      <tbody>{phase_rows}</tbody>
    </table>
  </div>

{pages_section}

  <div class="section">
    <h3>原始数据</h3>
    <pre class="page-content" style="font-size:.8rem">{raw_json}</pre>
  </div>
</div>
</body>
</html>"""


def _build_card(comp: Competition, output_dir: Path) -> str:
    summary = _phase_summary(comp)
    url = f"{_CODABENCH_BASE}/competitions/{comp.id}/"
    detail_href = f"{comp.id}.html"

    phase_html = ""
    if summary["dev_start"] or summary["dev_end"]:
        phase_html += (
            f"<div class='phase-row'>"
            f"<span class='phase-label' style='color:var(--dev)'>开发</span>"
            f"<span class='phase-dates'>{_fmt_date(summary['dev_start'])} → {_fmt_date(summary['dev_end'])}</span>"
            f"&nbsp;{_days_left(summary['dev_end'])}"
            f"</div>"
        )
    if summary["test_start"] or summary["test_end"]:
        phase_html += (
            f"<div class='phase-row'>"
            f"<span class='phase-label' style='color:var(--test)'>测试</span>"
            f"<span class='phase-dates'>{_fmt_date(summary['test_start'])} → {_fmt_date(summary['test_end'])}</span>"
            f"&nbsp;{_days_left(summary['test_end'])}"
            f"</div>"
        )
    if not phase_html:
        phase_html = f"<span class='no-data'>暂无阶段数据</span>"

    return (
        f"<article class='card'>"
        f"<div class='card-header'>"
        f"<div class='card-title'><a href='{detail_href}'>{html.escape(comp.title)}</a></div>"
        f"<div class='card-id'>#{comp.id}</div>"
        f"</div>"
        f"<div class='card-desc'>{_short_desc(comp.description_zh, comp.description)}</div>"
        f"<div class='card-phases'>{phase_html}</div>"
        f"<div class='card-footer'>"
        f"<div class='participants'>参与者 <span>{comp.participant_count}</span> 人</div>"
        f"<a href='{url}' target='_blank' rel='noopener' class='ext-link'>Codabench ↗</a>"
        f"</div>"
        f"</article>"
    )


def _build_detail(comp: Competition) -> str:
    pages_html = ""
    for page in comp.pages:
        raw = page.content_zh or page.content or ""
        if raw.strip():
            pages_html += (
                f"<div class='section'>"
                f"<h3>{html.escape(page.title or f'页面 {page.index}')}</h3>"
                f"<div class='page-content'>{html.escape(raw.strip())}</div>"
                f"</div>"
            )

    raw = json.dumps(comp.model_dump(by_alias=True), ensure_ascii=False, indent=2)

    return _DETAIL_TEMPLATE.format(
        css=_CSS,
        title=html.escape(comp.title),
        cid=comp.id,
        created_by=html.escape(comp.created_by or "—"),
        created_when=_fmt_date(comp.created_when),
        participants=comp.participant_count,
        submissions=comp.submission_count,
        url=f"{_CODABENCH_BASE}/competitions/{comp.id}/",
        description=html.escape(comp.description_zh or comp.description or "暂无描述"),
        phase_rows=_phase_rows(comp.phases),
        pages_section=pages_html,
        raw_json=html.escape(raw),
    )


class HtmlGenerator:
    """将竞赛快照渲染为 index.html + {id}.html，输出到 output_dir。"""

    def __init__(self, output_dir: Path):
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def generate(self, snapshot: dict[int, Competition]) -> None:
        updated = datetime.now().strftime("%Y-%m-%d %H:%M")

        # 按截止日期排序
        comps = sorted(
            snapshot.values(),
            key=lambda c: (
                min((p.end for p in c.phases if p.end), default="9999"),
                c.title,
            ),
        )

        # 详情页
        for comp in comps:
            detail_path = self._dir / f"{comp.id}.html"
            detail_path.write_text(_build_detail(comp), encoding="utf-8")

        # 目录页
        cards = "\n".join(_build_card(c, self._dir) for c in comps)
        index_html = _INDEX_TEMPLATE.format(
            css=_CSS,
            count=len(comps),
            updated=updated,
            cards=cards,
        )
        (self._dir / "index.html").write_text(index_html, encoding="utf-8")
        logger.info("HTML 已生成：%s（%d 个竞赛）", self._dir / "index.html", len(comps))
