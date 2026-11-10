"""模型档案（profile）。

参考 StarHub 的 core/adapters/traits.py 的思路：把「这个模型跑起来是什么样」
表达成一份声明，而不是散落在各处 if 判断。本模块只描述本机实测过的档位。

字段与服务端参数名的对应关系见 docs/实践情况/模型清单.md 的 5.3 节。
自写服务端用连字符写法：
- n_ctx 对应 --ctx-size
- n_gpu_layers 对应 --n-gpu-layers
- kv_type 对应 --cache-type-k 与 --cache-type-v，取字符串枚举
服务端固定用模型自带模板，没有「指定 chat_format」这一说。

档案来源有两处：store 里保存的 profile 文件优先，缺字段时用本模块的默认档补齐。

默认档的选择依据见 docs/实践情况/质量与速度平衡点.md：剧情推进优先于高频交互，
并且要求资源平衡、低负载、部署机器可能更换，因此不引入 32B 内存档。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.paths import model_file

# 只用自写服务端 world-work-cpp。Python 版 llama_cpp.server 的支持已经去掉：
# 它没有控制向量、原地换模型与运行时声线生成这三样能力，
# 留着只会让人以为「换个服务端也行」。
SERVER_KIND_WWC = "world-work-cpp"


@dataclass(frozen=True)
class ModelProfile:
    """一个本地模型档位。"""

    id: str
    name: str
    model_path: str
    n_ctx: int = 16384
    n_gpu_layers: int = 999
    kv_type: str = "f16"
    tokenizer_type: str = "llamacpp"
    measured: dict[str, Any] = field(default_factory=dict)

    def server_args(self) -> list[str]:
        """拼出自写服务端的启动参数。"""
        args = [
            "--model",
            self.model_path,
            "--ctx-size",
            str(self.n_ctx),
            "--n-gpu-layers",
            str(self.n_gpu_layers),
        ]
        if self.kv_type != "f16":
            args += ["--cache-type-k", self.kv_type, "--cache-type-v", self.kv_type]
        return args

    def to_dict(self) -> dict[str, Any]:
        """对外形状。"""
        data = asdict(self)
        data["server_args"] = self.server_args()
        return data


# 本机实测过的档位。速率、回合长度与显存数据取自
# docs/实践情况/data 下的扫描结果，结论见质量与速度平衡点报告。
#
# 默认档选 ArityFlow-Qwen3-4B-Instruct-2507-RolePlay Q5_K_M：
# 四轮剧情推进实测里，只有它稳定维持 146 到 180 字的完整回合并每轮带引号对白；
# 权重 2756 MB 全层驻留即可，换机器不必重调卸载层数与 KV 量化。
DEFAULT_PROFILES: tuple[ModelProfile, ...] = (
    # 质量档（默认）：本轮待测名单里综合最好的档位。
    # 四轮实测照搬率恒为 0、推进分 10/10/10/10、矛盾 0、回应率 1.0；
    # 代价是解码 36 tok/s、每轮 5.5 秒。权重 5690 MB，
    # 上下文 16384 用 q8_0 KV 缓存（f16 放不下 8GB 显存）。
    ModelProfile(
        id="quality",
        name="质量档",
        model_path=model_file("local/qwen3.5-9b/Qwen3.5-9B-UD-Q4_K_XL.gguf"),
        n_ctx=16384,
        n_gpu_layers=999,
        kv_type="q8_0",
        measured={
            "decode_tok_per_s": 36.06,
            "prefill_tok_per_s": 1068.3,
            "vram_mib": None,
            "ttft_s": 0.251,
            "turn_chars": 278,
            "turn_dialogues": 2,
            "turn_wait_s": 5.5,
            "multi_turn_chars": "257-324",
            "judge_score": None,
            "note": "四轮推进分 10/10/10/10，照搬率 0",
        },
    ),
    # 高速档：交互与试跑用。解码 122 tok/s、每轮 1.57 秒，
    # 字数四轮全在 150 到 300 区间，回应率 1.0。
    ModelProfile(
        id="fast",
        name="高速档",
        model_path=model_file("local/qwen3.5-2b/Qwen3.5-2B-UD-Q4_K_XL.gguf"),
        n_ctx=16384,
        n_gpu_layers=999,
        kv_type="f16",
        measured={
            "decode_tok_per_s": 122.4,
            "prefill_tok_per_s": 1779.2,
            "vram_mib": None,
            "ttft_s": 0.258,
            "turn_chars": 201,
            "turn_dialogues": 3,
            "turn_wait_s": 1.57,
            "multi_turn_chars": "182-240",
            "judge_score": None,
            "note": "推进分均值 8.75，叙事密度低于质量档",
        },
    ),
    # 轻量档：原默认档，四轮 146 到 180 字，第 4 轮照搬率 0.29。
    ModelProfile(
        id="interaction",
        name="轻量档",
        model_path=model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf"),
        n_ctx=16384,
        n_gpu_layers=999,
        kv_type="f16",
        measured={
            "decode_tok_per_s": 59.21,
            "prefill_tok_per_s": 1589.0,
            "vram_mib": None,
            "ttft_s": 0.201,
            "turn_chars": 158,
            "turn_dialogues": 3,
            "turn_wait_s": 1.84,
            "multi_turn_chars": "146-180",
            "judge_score": None,
            "note": "第 4 轮照搬率 0.29，靠客户端复读闸门兜",
        },
    ),
)


def default_profile(profile_id: str | None = None) -> ModelProfile:
    """按 id 取默认档，取不到时给交互档。"""
    for profile in DEFAULT_PROFILES:
        if profile.id == profile_id:
            return profile
    return DEFAULT_PROFILES[0]


def profile_from_dict(payload: dict[str, Any]) -> ModelProfile:
    """从 store 里的 profile 文件构造档案，缺字段用默认档补齐。"""
    fallback = default_profile(str(payload.get("id") or ""))
    params = payload.get("params")
    params = params if isinstance(params, dict) else {}

    def pick(key: str, default: Any) -> Any:
        value = params.get(key, payload.get(key, default))
        return value if value is not None else default

    measured = payload.get("measured")
    if not isinstance(measured, dict):
        # 自建 profile 没有实测数据时不要继承别档位的数字：
        # 界面上那会让用户以为它也跑过同样的速度。
        builtin = builtin_profile(str(payload.get("id") or ""))
        measured = builtin.measured if builtin is not None else {}

    return ModelProfile(
        id=str(payload.get("id") or fallback.id),
        name=str(payload.get("name") or fallback.name),
        model_path=str(pick("model_path", fallback.model_path)),
        n_ctx=int(pick("n_ctx", fallback.n_ctx)),
        n_gpu_layers=int(pick("n_gpu_layers", fallback.n_gpu_layers)),
        kv_type=str(pick("kv_type", fallback.kv_type)),
        tokenizer_type=str(pick("tokenizer_type", fallback.tokenizer_type)),
        measured=measured,
    )


def builtin_profile(profile_id: str) -> ModelProfile | None:
    """按 id 取内置档位，没有就返回 None。"""
    for profile in DEFAULT_PROFILES:
        if profile.id == profile_id:
            return profile
    return None


def resolve_profile(profile_id: str | None = None) -> ModelProfile:
    """解析生效档案：store 里的 profile 优先，其次内置档位，最后默认档。

    内置档位不在 store 里，按 id 直接命中内置表；切档位只写 active_model，
    不必往 store 里另存一份。
    """
    from app import store

    if profile_id:
        stored = store.get_profile(profile_id)
        if isinstance(stored, dict):
            return profile_from_dict(stored)
        builtin = builtin_profile(profile_id)
        if builtin is not None:
            return builtin

    active = store.get_setting("active_model")
    if isinstance(active, dict):
        stored_id = active.get("profile_id")
        if isinstance(stored_id, str) and stored_id:
            stored = store.get_profile(stored_id)
            if isinstance(stored, dict):
                return profile_from_dict(stored)
            builtin = builtin_profile(stored_id)
            if builtin is not None:
                return builtin

    return default_profile(profile_id)
