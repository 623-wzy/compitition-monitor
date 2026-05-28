"""Codabench REST API 客户端（只读，不需登录）。"""
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CODABENCH_BASE = "https://www.codabench.org"

_DEV_KEYWORDS = ("development", "develop", "dev", "train", "练习", "开发", "训练")
_TEST_KEYWORDS = ("test", "final", "eval", "submission", "测试", "最终", "评估", "提交")


def classify_phase(phase_name: str) -> str:
    """返回 'dev' / 'test' / 'other'。"""
    name = phase_name.lower()
    if any(k in name for k in _DEV_KEYWORDS):
        return "dev"
    if any(k in name for k in _TEST_KEYWORDS):
        return "test"
    return "other"


class CompetitionSummary(BaseModel):
    """list API 返回的轻量条目，仅含过滤所需字段。"""
    id: int
    title: str
    description: str | None = None
    first_phase_start: str | None = None

    model_config = {"populate_by_name": True}


class Phase(BaseModel):
    id: int = 0
    name: str = ""
    start: str | None = None
    end: str | None = None
    max_submissions_per_day: int | None = None


class Page(BaseModel):
    index: int = 0
    title: str = ""
    content: str = ""
    content_zh: str | None = None  # Claude 翻译的中文内容


class Competition(BaseModel):
    id: int
    title: str
    description: str | None = None
    description_zh: str | None = None  # Claude 翻译的中文描述
    created_by: str | None = None
    created_when: str | None = None
    logo: str | None = None
    participant_count: int = Field(default=0, alias="participants_count")
    submission_count: int = Field(default=0, alias="submissions_count")
    first_phase_start: str | None = None
    phases: list[Phase] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class CodabenchAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(f"HTTP {status_code}: {message}")
        self.status_code = status_code


class _TokenBucket:
    """令牌桶限速器，线程安全。"""

    def __init__(self, rate: float):
        self._rate = rate
        self._tokens = rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._rate,
                self._tokens + (now - self._last) * self._rate,
            )
            self._last = now
            if self._tokens < 1:
                time.sleep((1 - self._tokens) / self._rate)
                self._tokens = 0
            else:
                self._tokens -= 1


class CodabenchClient:
    def __init__(
        self,
        base_url: str = CODABENCH_BASE,
        token: str | None = None,
        rate: float = 2.0,
    ):
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Token {token}"
        self._http = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=60.0,
            follow_redirects=True,
        )
        self._bucket = _TokenBucket(rate)

    def list_competitions(self, limit: int = 5000) -> list[CompetitionSummary]:
        """GET /api/competitions/public/ — 仅拉取 id/title/description/first_phase_start。"""
        return self._paginate("/api/competitions/public/", {"limit": 200}, limit, CompetitionSummary)

    def get_competition_detail(self, competition_id: int) -> tuple[Competition, dict]:
        """GET /api/competitions/<id>/ — 含 phases 和 pages。返回 (Competition, raw_json)。"""
        self._bucket.acquire()
        resp = self._http.get(f"/api/competitions/{competition_id}/")
        self._check(resp)
        raw = resp.json()
        data = dict(raw)
        phases_raw = data.pop("phases", [])
        pages_raw = data.pop("pages", [])
        comp = Competition.model_validate(data)
        comp.phases = [Phase.model_validate(p) for p in phases_raw]
        comp.pages = [
            Page(index=p.get("index", i), title=p.get("title", ""), content=p.get("content", ""))
            for i, p in enumerate(pages_raw)
            if isinstance(p, dict)
        ]
        # 把 phases/pages 放回 raw 供调用方保存原始数据
        raw["phases"] = phases_raw
        raw["pages"] = pages_raw
        return comp, raw

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _rewrite_url(self, url: str) -> str:
        parsed = urlparse(url)
        base = urlparse(str(self._http.base_url))
        if parsed.netloc and parsed.netloc != base.netloc:
            url = urlunparse(parsed._replace(scheme=base.scheme, netloc=base.netloc))
        return url

    def _paginate(self, path: str, params: dict, max_results: int, model=None) -> list:
        if model is None:
            model = Competition
        results = []
        url: str | None = path
        while url and len(results) < max_results:
            self._bucket.acquire()
            try:
                if url.startswith("http"):
                    resp = self._http.get(self._rewrite_url(url))
                else:
                    resp = self._http.get(url, params=params if url == path else {})
                self._check(resp)
                body = resp.json()
            except Exception as e:
                logger.warning("分页请求失败（已获 %d 条）: %s", len(results), e)
                break
            for item in body.get("results", []):
                results.append(model.model_validate(item))
            url = body.get("next")
        return results[:max_results]

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise CodabenchAPIError(resp.status_code, resp.text[:200])
