r"""播种演示数据：角色卡 c_demo、世界书 w_demo、profile p_default。

前端聊天 store 的角色卡与世界书 id 是硬编码的（c_demo / w_demo），
预设 id p_demo 不存在时由后端回落到内置预设 p_builtin_rp（自带 output_spec）。
本脚本经后端 HTTP 接口写入，不直接改数据目录，可重复执行。

用法：
    python backend\tools\seed_demo_data.py
"""

from __future__ import annotations

import os
import sys

import httpx
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:

    sys.path.insert(0, str(TOOLS_DIR))

from local_paths import model_file  # noqa: E402


BASE = os.environ.get("XW_BACKEND", "http://127.0.0.1:8760")

CARD = {
    "id": "c_demo",
    "name": "角色K",
    "spec_version": "v2",
    "data": {
        "name": "角色K",
        "description": (
            "地址N地址U的调音师，三十出头。沉默寡言，观察力极强，"
            "习惯用最少的话回应，却总能点出对方没说出口的心思。"
            "对声音的细节有近乎固执的洁癖，对人却愿意给出恰好的分寸。"
        ),
        "personality": (
            "克制、沉稳、观察敏锐。不主动解释自己，也不追问对方的来意；"
            "偶有不动声色的温柔，但从不直说。"
        ),
        "scenario": (
            "雨夜的地址N。地址U在巷子尽头的二楼，只剩控制台那盏台灯亮着。"
            "窗玻璃上全是水痕，外面的霓虹被雨水揉碎成一片。"
        ),
        "first_mes": "门铃响了一声，他没抬头。",
        "mes_example": (
            "<START>" + chr(10) +
            "{{user}}: 我还以为你今晚不在。" + chr(10) +
            "{{char}}: 他把推子推回原位。" + chr(10) +
            "\"在。\"停了两秒，他才补上一句，\"带带了？\""
        ),
        "creator": "X-WORLD Demo",
        "character_version": "1.0",
    },
}

BOOK = {
    "id": "w_demo",
    "name": "地址N",
    "entries": [
        {
            "keys": ["地址U", "控制台", "调音台", "推子"],
            "content": (
                "地址U在二楼，控制台是三十年前的老机子，推子推起来有点涩。"
                "调音台上那盏台灯是唯一的光源，旋钮上落着一层薄灰。"
            ),
            "priority": 10,
        },
        {
            "keys": ["雨夜", "雨", "地址N", "巷子"],
            "content": (
                "地址N的雨夜很安静，霓虹灯在水洼里碎成一片。"
                "巷口偶尔有卡车碾过积水，声音一路拖到街尾才慢慢散掉。"
            ),
            "priority": 20,
        },
    ],
}

PROFILE = {
    "id": "p_default",
    "name": "默认档（ArityFlow 4B Q5_K_M）",
    "params": {
        "model_path": (
            model_file("hf/ArityFlow-Qwen3-4B-Instruct-2507-RolePlay.Q5_K_M.gguf")
        ),
        "n_ctx": 4096,
        "n_gpu_layers": 999,
        "kv_type": "f16",
        "tokenizer_type": "llamacpp",
        "top_k": 40,
        "min_p": 0.05,
        "decode_tok_per_s": 62.99,
        "prefill_tok_per_s": 1507.9,
        "turn_chars": 212,
        "turn_wait_s": 2.69,
    },
}


def main() -> int:
    failures = []

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        resp = client.post("/v1/cards/", json=CARD)
        if resp.status_code != 200:
            failures.append("card HTTP " + str(resp.status_code) + " " + resp.text[:200])
        else:
            print("card " + resp.json().get("id", "?") + " ok")

        resp = client.post("/v1/world-books/", json=BOOK)
        if resp.status_code != 200:
            failures.append("book HTTP " + str(resp.status_code) + " " + resp.text[:200])
        else:
            print("book " + resp.json().get("id", "?") + " ok")

        resp = client.put("/v1/settings/default_character_id", json={"value": "c_demo"})
        if resp.status_code != 200:
            failures.append("default_character HTTP " + str(resp.status_code) + " " + resp.text[:200])
        else:
            print("default_character c_demo ok")

        resp = client.post("/v1/profiles", json=PROFILE)
        if resp.status_code != 200:
            failures.append("profile HTTP " + str(resp.status_code) + " " + resp.text[:200])
        else:
            print("profile " + resp.json().get("id", "?") + " ok")

        resp = client.post(
            "/v1/models/active",
            json={"model_id": "xw-local-default", "profile_id": "p_default"},
        )
        if resp.status_code != 200:
            failures.append("active HTTP " + str(resp.status_code) + " " + resp.text[:200])
        else:
            print("active xw-local-default / p_default ok")

    if failures:
        for item in failures:
            print("[FAIL] " + item)
        return 1
    print("seed done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
