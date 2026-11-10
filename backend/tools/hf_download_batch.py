r"""按清单批量下载候选权重，逐文件校验字节数。

清单来自本轮待测名单，仓库与文件名在镜像上确认过。
下载用 hf_pget 的并行分段逻辑：多镜像回退，分块续传，字节数不符就删掉重下。

同时把进度写进日志文件，便于事后核对（控制台管道会缓冲输出）。

用法：
    python backend\tools\hf_download_batch.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
TOOLS_DIR = Path(__file__).resolve().parent

if str(TOOLS_DIR) not in sys.path:

    sys.path.insert(0, str(TOOLS_DIR))

from local_paths import local_path  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "backend" / "tools"
PYTHON = r"python"
LOG_FILE = ROOT.parent / "docs" / "实践情况" / "data" / "sweep" / "hf_download.log"

# (仓库, 文件名, 目标目录)
BATCH = [
    (
        "mradermacher/Qwen2-7B-Multilingual-RP-GGUF",
        "Qwen2-7B-Multilingual-RP.Q6_K.gguf",
        local_path("models_hf_dir"),
    ),
    (
        "Lewdiculous/Aura_v2_7B-GGUF-IQ-Imatrix",
        "Aura_v2_7B-IQ3_M-imat.gguf",
        local_path("models_hf_dir"),
    ),
    (
        "ArliAI/Mistral-Nemo-12B-ArliAI-RPMax-v1.1-GGUF",
        "ArliAI-RPMax-12B-v1.1-Q3_K_M.gguf",
        local_path("models_hf_dir"),
    ),
    (
        "mradermacher/Gemma4-E4B-MiniFantasy-V1-GGUF",
        "Gemma4-E4B-MiniFantasy-V1.Q4_K_M.gguf",
        local_path("models_hf_dir"),
    ),
]


def log(text: str) -> None:
    """同时写到控制台和日志文件。"""
    print(text, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
    except OSError:
        pass


def main() -> int:
    """逐个下载。"""
    failed: list[str] = []
    for index, (repo, filename, dest) in enumerate(BATCH, 1):
        log("[%d/%d] %s / %s" % (index, len(BATCH), repo, filename))
        code = subprocess.call(
            [PYTHON, str(TOOLS / "hf_pget.py"), repo, filename, dest],
            stdout=None,
            stderr=None,
        )
        if code != 0:
            failed.append(repo + " / " + filename)
            log("    失败，退出码 %d" % code)
        else:
            log("    完成")
        time.sleep(15)
    log("")
    log("失败清单：")
    for item in failed:
        log("  " + item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
