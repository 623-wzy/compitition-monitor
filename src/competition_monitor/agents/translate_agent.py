"""TranslateAgent — 用 Claude 将竞赛描述和页面内容翻译成中文。

只翻译尚未有中文内容的字段（description_zh / page.content_zh 为 None 时）。
已有翻译的竞赛直接跳过，避免重复调用 API。
"""
import logging

import anthropic

from ..platforms.codabench import Competition

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是一个科技竞赛信息翻译助手。"
    "将用户提供的英文竞赛文本翻译成简体中文，保持专业术语准确，"
    "输出纯翻译结果，不加任何解释或前缀。"
    "如果原文已经是中文，原样返回。"
)


class TranslateAgent:
    def __init__(self, client: anthropic.Anthropic, model: str):
        self._ai = client
        self._model = model

    def translate(self, comps: dict[int, Competition]) -> None:
        """原地翻译 comps 中尚未有中文内容的竞赛，直接修改对象。"""
        for comp in comps.values():
            self._translate_comp(comp)

    def _translate_comp(self, comp: Competition) -> None:
        if comp.description and not comp.description_zh:
            comp.description_zh = self._call(comp.description[:3000])
            logger.debug("已翻译竞赛 %d 描述", comp.id)

        for page in comp.pages:
            if page.content and not page.content_zh:
                page.content_zh = self._call(page.content[:4000])
                logger.debug("已翻译竞赛 %d 页面 %d", comp.id, page.index)

    def _call(self, text: str) -> str:
        try:
            resp = self._ai.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_SYSTEM,
                messages=[{"role": "user", "content": text}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            logger.warning("翻译失败: %s", e)
            return ""
