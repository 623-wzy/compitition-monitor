"""FetchScript — 纯 Python 两阶段过滤，无 AI 调用。

Phase 1: 关键词相关性过滤
  对 title + description 做子串匹配，只保留图像/视频处理类竞赛。

Phase 2: 日期窗口过滤
  已在索引中且有有效日期的竞赛直接复用 → 不额外调用详情 API。
  新竞赛调用 detail API 拿 phases 日期 → 过期或太遥远的丢弃。
  若详情 API 多次失败 → 以基础信息暂存，待下次重试。
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import Config
from ..platforms.codabench import CodabenchClient, Competition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 相关性关键词表（任意一个命中 title+description 即视为相关）
# ---------------------------------------------------------------------------

_RELEVANCE_KW = [
    # 核心视觉词
    "image", "video", "visual", "vision", "pixel",
    # 视觉专属任务
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
    # 三维视觉
    "point cloud", "lidar", "depth map", "disparity",
    "nerf", "novel view synthesis", "radiance field", "gaussian splatting",
    "stereo", "scene reconstruction",
    # 事件相机与光照
    "event camera", "event-based", "event guided", "event-guided",
    "illumination", "exposure correction", "brightness",
    # 特定视觉场景
    "crowd counting", "lane detection",
    "low light image", "haze removal", "rain removal",
    # 医学影像
    "medical image", "pathology", "histology", "radiology",
    "ct scan", "mri", "ultrasound",
    "cell segmentation", "nuclei", "lesion",
    # 中文兜底
    "图像", "视频", "分割",
]


def _is_relevant(comp: Competition) -> bool:
    """title + description 有任意关键词命中则返回 True。"""
    text = f"{comp.title} {comp.description or ''}".lower()
    return any(kw in text for kw in _RELEVANCE_KW)


# ---------------------------------------------------------------------------
# 日期工具
# ---------------------------------------------------------------------------

def _latest_end(comp: Competition) -> date | None:
    """取最后一个 phase 的结束日期。"""
    ends = [p.end for p in comp.phases if p.end]
    if not ends:
        return None
    try:
        return date.fromisoformat(max(ends)[:10])
    except ValueError:
        return None


def _earliest_start(comp: Competition) -> str | None:
    """取最早一个 phase 的开始日期（ISO 字符串）。"""
    starts = [p.start for p in comp.phases if p.start]
    if starts:
        return min(starts)[:10]
    v = (comp.first_phase_start or "")[:10]
    return v or None


# ---------------------------------------------------------------------------
# BasicCompetition — FetchScript 输出的轻量结构
# ---------------------------------------------------------------------------

@dataclass
class BasicCompetition:
    id: int
    title: str
    start: str | None
    end: str | None
    participant_count: int
    url: str
    description: str
    # 若 detail 已成功拉取，保留完整 Competition 供 Snapshot 复用
    detail: Competition | None = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# FetchResult
# ---------------------------------------------------------------------------

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
# FetchScript
# ---------------------------------------------------------------------------

class FetchScript:
    """
    纯 Python 竞赛抓取与过滤器，不调用 AI。

    existing: dict[int, Competition] — 上次快照（来自 StateStore）。
              有 phases 日期的条目直接复用，跳过详情 API 调用。
    """

    def __init__(self, codabench: CodabenchClient, config: Config):
        self._codabench = codabench
        self._config = config

    def run(self, existing: dict[int, Competition] | None = None) -> tuple[list[BasicCompetition], FetchResult]:
        existing = existing or {}
        result = FetchResult()

        # ── Phase 1: 拉取全量 + 相关性过滤 ──────────────────────────────
        all_comps = self._get_all()
        result.total_fetched = len(all_comps)
        logger.info("Codabench API 共返回 %d 个公开竞赛", len(all_comps))

        relevant = [c for c in all_comps if _is_relevant(c)]
        result.total_relevant = len(relevant)
        logger.info("相关性过滤后保留 %d 个图像/视频相关竞赛", len(relevant))

        # ── Phase 2: 日期窗口过滤 ─────────────────────────────────────────
        today = date.today()
        cutoff = today + timedelta(days=self._config.max_days_ahead)
        basics: list[BasicCompetition] = []

        for comp in relevant:
            ex = existing.get(comp.id)

            if ex:
                # 已在索引中 → 用已有 phases 数据判断日期，不再调 detail API
                end_date = _latest_end(ex)
                if end_date:
                    if today <= end_date <= cutoff:
                        basics.append(BasicCompetition(
                            id=ex.id,
                            title=ex.title,
                            start=_earliest_start(ex),
                            end=end_date.isoformat(),
                            participant_count=ex.participant_count,
                            url=f"{self._config.codabench_base_url}/competitions/{ex.id}/",
                            description=(ex.description or "")[:500],
                            detail=ex,
                        ))
                    else:
                        logger.debug("竞赛 %d 已过期或超出窗口，移出快照", comp.id)
                    result.skipped += 1
                else:
                    # phases 无日期 → 保守保留，待下次重试
                    basics.append(BasicCompetition(
                        id=ex.id,
                        title=ex.title,
                        start=_earliest_start(ex),
                        end=None,
                        participant_count=ex.participant_count,
                        url=f"{self._config.codabench_base_url}/competitions/{ex.id}/",
                        description=(ex.description or "")[:500],
                        detail=ex,
                    ))
                    result.skipped += 1
                continue

            # 新竞赛 → 调 detail API 拿准确日期
            detail = self._fetch_detail(comp.id)

            if detail is not None:
                end_date = _latest_end(detail)
                if not end_date:
                    logger.debug("竞赛 %d 无截止日期，跳过", comp.id)
                    result.skipped += 1
                    continue
                if end_date < today:
                    logger.debug("竞赛 %d 已截止 (%s)，跳过", comp.id, end_date)
                    result.skipped += 1
                    continue
                if end_date > cutoff:
                    logger.debug("竞赛 %d 截止超过 %d 天上限，跳过", comp.id, self._config.max_days_ahead)
                    result.skipped += 1
                    continue
                result.added.append(detail.id)
                basics.append(BasicCompetition(
                    id=detail.id,
                    title=detail.title,
                    start=_earliest_start(detail),
                    end=end_date.isoformat(),
                    participant_count=detail.participant_count,
                    url=f"{self._config.codabench_base_url}/competitions/{detail.id}/",
                    description=(detail.description or "")[:500],
                    detail=detail,
                ))
            else:
                # 详情 API 多次失败 → 以基础信息暂存，跳过日期过滤
                logger.warning("竞赛 %d 详情接口多次失败，以基础信息暂存", comp.id)
                result.errors.append(f"[{comp.id}] {comp.title}: 详情获取失败，已暂存待重试")
                basics.append(BasicCompetition(
                    id=comp.id,
                    title=comp.title,
                    start=None,
                    end=None,
                    participant_count=comp.participant_count,
                    url=f"{self._config.codabench_base_url}/competitions/{comp.id}/",
                    description=(comp.description or "")[:500],
                    detail=None,
                ))

        logger.info("日期过滤后保留 %d 个竞赛（新增 %d 条）", len(basics), len(result.added))
        basics.sort(key=lambda x: x.end or "9999")
        return basics, result

    # ------------------------------------------------------------------

    def _fetch_detail(
        self, competition_id: int, retries: int = 3, backoff: float = 2.0
    ) -> Competition | None:
        for attempt in range(retries):
            try:
                return self._codabench.get_competition_detail(competition_id)
            except Exception as e:
                if attempt < retries - 1:
                    wait = backoff * (2 ** attempt)
                    logger.warning(
                        "获取竞赛 %d 详情失败（第 %d/%d 次），%.0fs 后重试: %s",
                        competition_id, attempt + 1, retries, wait, e,
                    )
                    time.sleep(wait)
                else:
                    logger.error("获取竞赛 %d 详情失败，已重试 %d 次: %s", competition_id, retries, e)
        return None

    def _get_all(self) -> list[Competition]:
        try:
            return self._codabench.search_competitions("", limit=self._config.max_competitions)
        except Exception as e:
            logger.error("获取全量竞赛列表失败: %s", e)
            return []
