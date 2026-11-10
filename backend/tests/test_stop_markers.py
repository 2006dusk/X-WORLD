"""推理请求体的 stop 约定测试。

模板标记必须随请求下发成 stop：模型被推离分布时会自己续写多轮对话，
把 <|im_start|> 这类标记当正文吐出来。服务端按累积内容匹配 stop，
标记跨 token 切分也拦得住；只在后端事后截断的话，标记后面的 token 已经白生成。
"""

from __future__ import annotations

from app.contracts import ChatMessage
from app.inference.base import InferenceParams
from app.inference.llamacpp import LlamaCppAdapter
from app.inference.normalize import TEMPLATE_MARKERS


def _payload(**kwargs) -> dict:
    """用默认参数搭一份请求体。"""
    adapter = LlamaCppAdapter(base_url="http://127.0.0.1:9")
    return adapter._build_payload(
        messages=[ChatMessage(role="user", content="\u9686\u4e0b\uff0c\u4eca\u65e5\u600e\u6837\uff1f")],
        params=InferenceParams(temperature=0.8, top_p=0.95, max_tokens=256, repetition_penalty=1.1),
        profile=kwargs.get("profile"),
        stream=False,
    )


def test_template_markers_are_sent_as_stop() -> None:
    """模板标记随请求下发，生成在标记处立刻停。"""
    stop = _payload()["stop"]
    for marker in TEMPLATE_MARKERS:
        assert marker in stop


def test_profile_stop_is_merged_not_replaced() -> None:
    """档位里自己带的 stop 不被覆盖掉。"""
    stop = _payload(profile={"stop": ["\u3010\u6b62\u6b62\u3011"]})["stop"]
    assert "\u3010\u6b62\u6b62\u3011" in stop
    for marker in TEMPLATE_MARKERS:
        assert marker in stop


def test_profile_stop_string_is_merged() -> None:
    """档位里 stop 写成字符串时也并进来。"""
    stop = _payload(profile={"stop": "\u3010\u6b62\u6b62\u3011"})["stop"]
    assert "\u3010\u6b62\u6b62\u3011" in stop