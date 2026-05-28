import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..config import Config
from ..platforms.codabench import CodabenchClient, Competition, CompetitionSummary, Phase, classify_phase

logger = logging.getLogger(__name__)

_RELEVANCE_KW = [
    "image", "video", "visual", "vision", "pixel",
    "image segmentation", "video segmentation",
    "semantic segmentation", "instance segmentation", "panoptic segmentation",
    "super resolution", "super-resolution",
    "optical flow", "depth estimation", "stereo matching",
    "image restoration", "image enhancement", "image denoising",
    "image deblurring", "image inpainting", "image colorization",
    "image compression", "image generation", "image synthesis",
    "image retrieval", "image captioning", "image matting",
    "image harmonization", "image quality",
    "video compression", "video super resolution",
    "video interpolation", "frame interpolation",
    "video quality", "video synthesis", "video generation",
    "video object", "video understanding",
    "point cloud", "lidar", "depth map", "disparity",
    "nerf", "novel view synthesis", "radiance field", "gaussian splatting",
    "stereo", "scene reconstruction",
    "event camera", "event-based", "event guided", "event-guided",
    "illumination", "exposure correction", "brightness",
    "crowd counting", "lane detection",
    "low light image", "haze removal", "rain removal",
    "图像", "视频", "分割",
]


@dataclass
class FetchResult:
    added: list[int] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    total_fetched: int = 0
    total_relevant: int = 0

    def __str__(self) -> str:
        return (
            f"共抓取 {self.total_fetched} 条，相关 {self.total_relevant} 条，"
            f"新增 {len(self.added)} 条，跳过 {self.skipped} 条"
            + (f"，错误 {len(self.errors)} 条" if self.errors else "")
        )


# ---------------------------------------------------------------------------
# 过滤谓词
# ---------------------------------------------------------------------------

_EXCLUDE_KW = [
    # 课程作业
    "exercise", "assignment", "homework", "coursework",
    "course project", "class project",
    # 课程标识
    "- group ", "group project",
    # 描述中明确是课程
    "evaluation server for the exercise",
    "from the course", "of this course", "in this course",
    "for the course", "course assignment",
    # 中文
    "课程作业", "练习题", "作业",
]


def _is_relevant(comp: CompetitionSummary) -> bool:
    text = f"{comp.title} {comp.description or ''}".lower()
    if any(kw in text for kw in _EXCLUDE_KW):
        return False
    return any(kw in text for kw in _RELEVANCE_KW)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


@dataclass
class PhaseInfo:
    """单个阶段的日期摘要。"""
    name: str
    kind: str          # 'dev' / 'test' / 'other'
    start: date | None
    end: date | None


def _extract_phases(comp: Competition) -> list[PhaseInfo]:
    """提取每个阶段的分类与日期。"""
    return [
        PhaseInfo(
            name=p.name,
            kind=classify_phase(p.name),
            start=_parse_date(p.start),
            end=_parse_date(p.end),
        )
        for p in comp.phases
    ]


def _should_keep(comp: Competition, today: date) -> str | None:
    """返回过滤原因（被过滤时），None 表示保留。"""
    phases = _extract_phases(comp)

    # 所有阶段均无截止日期
    if not any(p.end for p in phases):
        return "所有阶段均无截止日期"

    for p in phases:
        # 任意阶段截止日期小于当前日期
        if p.end and p.end < today:
            return f"阶段「{p.name}」已截止（{p.end}）"
        # 任意阶段截止日期大于当前日期 60 天
        if p.end and (p.end - today).days > 60:
            return f"阶段「{p.name}」截止日期超过 60 天（{p.end}）"

    # 只对最近一个尚未结束的阶段检查开始日期
    upcoming = next(
        (p for p in phases if p.end is None or p.end >= today),
        None,
    )
    if upcoming and upcoming.start and (upcoming.start - today).days > 30:
        return f"最近阶段「{upcoming.name}」开始日期超过 30 天（{upcoming.start}）"

    return None


# ---------------------------------------------------------------------------
# FetchScript
# ---------------------------------------------------------------------------

class FetchScript:
    def __init__(self, codabench: CodabenchClient, config: Config):
        self._codabench = codabench
        self._config = config
        self._ori_dir = config.data_dir / "ori"

    def run(self, existing: dict[int, Competition] | None = None) -> tuple[list[Competition], FetchResult]:
        existing = existing or {}
        result = FetchResult()
        today = date.today()

        # ── Step 1: 拉取轻量列表（仅 id/title/description/first_phase_start）
        summaries = self._get_all()
        result.total_fetched = len(summaries)
        logger.info("list API 共返回 %d 个公开竞赛", len(summaries))

        # ── Step 2: 关键词过滤
        relevant = [c for c in summaries if _is_relevant(c)]
        result.total_relevant = len(relevant)
        logger.info("关键词过滤后保留 %d 个相关竞赛", len(relevant))

        kept: list[Competition] = []
        to_fetch: list[CompetitionSummary] = []

        # ── Step 3: 预检，跳过开放日期超两个月的 ─────────────────────────
        for comp in relevant:
            pre_start = _parse_date(comp.first_phase_start)
            if pre_start and (pre_start - today).days > 60:
                logger.debug("竞赛 %d 预检：开放日期超过两个月，跳过", comp.id)
                result.skipped += 1
                continue
            to_fetch.append(comp)

        logger.info("进入 detail 拉取队列：%d 条", len(to_fetch))

        # ── Step 4: 并行拉取 detail ───────────────────────────────────────
        with ThreadPoolExecutor(max_workers=self._config.fetch_workers) as pool:
            futures = {pool.submit(self._fetch_detail, comp.id): comp for comp in to_fetch}
            for future in as_completed(futures):
                comp = futures[future]
                detail, raw = future.result()

                if detail is None:
                    logger.warning("竞赛 %d 详情接口多次失败，跳过", comp.id)
                    result.errors.append(f"[{comp.id}] {comp.title}: 详情获取失败")
                    result.skipped += 1
                    continue

                self._save_ori(comp.id, raw)

                # ── Step 5: 完整日期过滤 ──────────────────────────────────
                reason = _should_keep(detail, today)
                if reason:
                    logger.debug("竞赛 %d 跳过：%s", comp.id, reason)
                    result.skipped += 1
                    continue

                if comp.id not in existing:
                    result.added.append(detail.id)

                kept.append(detail)

        logger.info("过滤后保留 %d 个竞赛（新增 %d 条）", len(kept), len(result.added))
        kept.sort(key=lambda c: max(
            (p.end for p in _extract_phases(c) if p.end), default=date.max
        ).isoformat())
        return kept, result

    # ------------------------------------------------------------------

    def _fetch_detail(
        self, competition_id: int, retries: int = 3, backoff: float = 2.0
    ) -> tuple[Competition, dict] | tuple[None, None]:
        for attempt in range(retries):
            try:
                return self._codabench.get_competition_detail(competition_id)
            except Exception as e:
                if attempt < retries - 1:
                    wait = backoff * (2 ** attempt)
                    logger.warning("获取竞赛 %d 详情失败（%d/%d），%.0fs 后重试: %s",
                                   competition_id, attempt + 1, retries, wait, e)
                    time.sleep(wait)
                else:
                    logger.error("获取竞赛 %d 详情失败，已重试 %d 次: %s", competition_id, retries, e)
        return None, None

    def _save_ori(self, competition_id: int, raw: dict) -> None:
        self._ori_dir.mkdir(parents=True, exist_ok=True)
        path = self._ori_dir / f"{competition_id}.json"
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    def _get_all(self) -> list[CompetitionSummary]:
        try:
            return self._codabench.list_competitions(limit=self._config.max_competitions)
        except Exception as e:
            logger.error("获取全量竞赛列表失败: %s", e)
            return []
