"""对外数据契约。

形状取自设计稿第 4 节，前端据此渲染。字段只增不改。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryMode = Literal["none", "summary", "vector", "hybrid"]


class XwOptions(BaseModel):
    """请求级装配选项。"""

    character_id: str | None = None
    preset_id: str | None = None
    world_book_ids: list[str] = Field(default_factory=list)
    # 对话所在的世界区域：世界书里的一条区域条目，按 id 指定。
    region_id: str | None = None
    memory_mode: MemoryMode = "hybrid"
    max_context_tokens: int = 16384
    # 自动回合：用户这一轮不说话，由 AI 自己往下走。
    # continue 是接着角色上一轮的话继续推进；describe 是只推进场景描写。
    # 空串是普通轮次，用户说了话。
    auto_turn: Literal["", "continue", "describe"] = ""


class ChatMessage(BaseModel):
    role: str
    content: str
    reasoning: str | None = None
    # 结构化回合的块内容，块名是客户端自己的输出契约
    blocks: dict[str, str] | None = None


class ChatRequest(BaseModel):
    model: str = "xw-local-default"
    messages: list[ChatMessage]
    session: str | None = None
    stream: bool = True
    xw: XwOptions = Field(default_factory=XwOptions)
    # 轮次动作：append 追加一轮；regenerate 把某条角色回复原地换掉后重跑；
    # revise 用新输入替换某条用户输入后重跑，新旧两版并存成兄弟分支。
    # 历史以服务端会话流水为准，regenerate 与 revise 由后端改写流水。
    turn_action: Literal["append", "regenerate", "revise"] = "append"
    # 要改写的节点 id：重新生成给角色回复的节点，改写给用户输入的节点。
    # 留空时取当前路径上最后一个对应角色的节点。
    target_id: str | None = None


class BudgetInfo(BaseModel):
    limit: int
    reserved_output: int
    used: int


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    budget: BudgetInfo | None = None
    # 生成速率，每秒输出 token 数。流式取首个增量到计量事件的解码窗口，
    # 非流式取整个调用耗时（含提示词预填充），两者口径不同。
    generation_tps: float | None = None


class ContextSegment(BaseModel):
    name: str
    tokens: int
    entries: list[str] | None = None
    hits: int | None = None


class ContextReport(BaseModel):
    segments: list[ContextSegment] = Field(default_factory=list)
    template: str | None = None
    tokenizer: str | None = None
    truncated: bool = False


class ChatChoice(BaseModel):
    index: int = 0
    message: ChatMessage
    finish_reason: str = "stop"


class EditRequest(BaseModel):
    """编辑角色回复的请求。

    content 是完整的编辑源文本，含场景、对白与状态三段；后端按生效预设
    声明的结构解析，正文进气泡，状态块写进世界状态。新回复挂在 parent_id
    下，与被改的那一轮互为兄弟分支。
    """

    model: str = "xw-local-default"
    session: str
    parent_id: str = ""
    content: str
    xw: XwOptions = Field(default_factory=XwOptions)


class ChatResponse(BaseModel):
    id: str
    choices: list[ChatChoice]
    usage: Usage
    xw: dict[str, Any] = Field(default_factory=dict)


class PreviewMessage(BaseModel):
    role: str
    content: str
    source: str | None = None


class PromptPreview(BaseModel):
    segments: list[ContextSegment] = Field(default_factory=list)
    template: str | None = None
    tokenizer: str | None = None
    truncated: bool = False
    messages: list[PreviewMessage] = Field(default_factory=list)


class ModelInfo(BaseModel):
    id: str
    label: str
    provider: str
    context_length: int
    supports_reasoning: bool = False


class Profile(BaseModel):
    id: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
