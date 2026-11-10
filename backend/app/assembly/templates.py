"""Chat Template 渲染。

支持 chatml 与 llama3 两种模板。

输出为字符串形式，可直接送入推理后端。
Token ID 化由推理适配层负责。
"""

from __future__ import annotations

from app.contracts import ChatMessage


def render_chatml(messages: list[ChatMessage]) -> str:
    """ChatML 模板渲染。

    格式：
    <|im_start|>system
    ...<|im_end|>
    <|im_start|>user
    ...<|im_end|>
    <|im_start|>assistant
    ...<|im_end|>

    每个消息 role 映射到 ChatML 角色标签。
    """
    parts: list[str] = []
    for msg in messages:
        role = _map_role_chatml(msg.role)
        parts.append(f"<|im_start|>{role}\n{msg.content}<|im_end|>")
    # 末尾添加 assistant 起始标记
    parts.append("<|im_start|>assistant\n")
    return "\n".join(parts)


def render_llama3(messages: list[ChatMessage]) -> str:
    """Llama 3 模板渲染。

    格式：
    <|begin_of_text|><|start_header_id|>system<|end_header_id|>

    ...<|eot_id|><|start_header_id|>user<|end_header_id|>

    ...<|eot_id|><|start_header_id|>assistant<|end_header_id|>

    ...
    """
    parts: list[str] = ["<|begin_of_text|>"]
    for msg in messages:
        role = _map_role_llama3(msg.role)
        parts.append(
            f"<|start_header_id|>{role}<|end_header_id|>\n\n{msg.content}<|eot_id|>"
        )
    # 末尾添加 assistant 起始
    parts.append("<|start_header_id|>assistant<|end_header_id|>\n\n")
    return "".join(parts)


def _map_role_chatml(role: str) -> str:
    """将通用 role 映射到 ChatML 角色名。"""
    mapping = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "tool",
    }
    return mapping.get(role, role)


def _map_role_llama3(role: str) -> str:
    """将通用 role 映射到 Llama 3 角色名。"""
    mapping = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "ipython",
    }
    return mapping.get(role, role)


RENDERERS: dict[str, object] = {
    "chatml": render_chatml,
    "llama3": render_llama3,
}