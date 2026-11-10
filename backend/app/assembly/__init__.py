"""上下文装配管线。

按固定 8 段顺序装配：system、character、preset_prompts、post_history、
world_info、memory、history、user。支持 token 预算裁剪、世界书匹配、
chat template 渲染与 tokenizer 选择。
"""

from __future__ import annotations

from .budget import SegmentData, trim_to_budget
from .matching import MatchedEntry, match_entries
from .pipeline import AssembledContext, assemble
from .templates import render_chatml, render_llama3
from .tokenizer import (
    ApproxTokenizer,
    LlamaCppTokenizer,
    Tokenizer,
    get_tokenizer,
)

__all__ = [
    "ApproxTokenizer",
    "AssembledContext",
    "LlamaCppTokenizer",
    "MatchedEntry",
    "SegmentData",
    "Tokenizer",
    "assemble",
    "get_tokenizer",
    "match_entries",
    "render_chatml",
    "render_llama3",
    "trim_to_budget",
]