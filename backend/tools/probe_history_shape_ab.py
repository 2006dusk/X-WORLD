r"""历史场景形态三组对照实验。

比较三种「历史里角色回复保留多少场景」的做法，找既不场景回退又不照抄的形态：
A 组 场景全文（截断 120 字）
B 组 场景只留最后一句完整句（当前代码 _scene_result）
C 组 场景不进历史，只留对白最后一句

做法：改 backend/app/assembly/pipeline.py 的 _history_reply 取法，
重启后端跑满 6 轮，同一组输入，比较 scene_copy_ratio / in_band /
replace 次数 / reentry 次数。

用法：
    python backend\tools\probe_history_shape_ab.py
输出：
    docs\实践情况\data\sweep\history_shape_ab.md
    docs\实践情况\data\sweep\exp_a.json / exp_b.json / exp_c.json
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = Path(__file__).resolve().parents[2]
PIPELINE = BACKEND / "app" / "assembly" / "pipeline.py"
PROBE = ROOT / "backend" / "tools" / "probe_scene_continuity.py"
SWEEP = ROOT.parent / "docs" / "实践情况" / "data" / "sweep"

# 三组各自在 _history_reply 里的取法实现。
VARIANTS = {
    "A": (
        "A 组：场景全文（截断 HISTORY_SCENE_CHARS）",
        "    scene = _truncate_scene(scene, HISTORY_SCENE_CHARS)",
    ),
    "B": (
        "B 组：场景只留最后一句完整句",
        "    scene = _scene_result(scene, HISTORY_SCENE_CHARS)",
    ),
    "C": (
        "C 组：场景不进历史，只留对白最后一句",
        "    scene = ''",
    ),
}

# _history_reply 里给 scene 赋值的可能形态。
SCENE_ASSIGN_LINES = [
    "    scene = _truncate_scene(scene, HISTORY_SCENE_CHARS)",
    "    scene = _scene_result(scene, HISTORY_SCENE_CHARS)",
    "    scene = ''",
]

# 实验结束后回到的默认形态：B 组（动作结果句）。
DEFAULT_VARIANT = "B"


def backend_ready(timeout: float = 60.0) -> bool:
    """后端健康检查。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8760/healthz", timeout=5) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(2)
    return False


def restart_backend() -> None:
    """重启后端使 pipeline.py 的改动生效。"""
    subprocess.run(
        ["powershell", "-Command",
         "Get-NetTCPConnection -State Listen -LocalPort 8760 -ErrorAction SilentlyContinue "
         "| ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"],
        capture_output=True,
    )
    time.sleep(2)
    subprocess.Popen(
        ["F:\\python\\python.exe", "packaging\\launcher.py"],
        cwd=str(BACKEND),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if not backend_ready():
        raise RuntimeError("后端重启后未就绪")


def apply_variant(key: str) -> None:
    """把 _history_reply 改成指定组的取法。

    只在 _history_reply 函数体内替换给 scene 赋值的那一行。
    """
    text = PIPELINE.read_text(encoding="utf-8")
    lines = text.split(chr(10))
    start = None
    for index, line in enumerate(lines):
        if line.startswith("def _history_reply("):
            start = index
            break
    if start is None:
        raise RuntimeError("pipeline.py 里找不到 _history_reply")
    target = None
    for index in range(start, min(start + 40, len(lines))):
        if lines[index] in SCENE_ASSIGN_LINES:
            target = index
            break
    if target is None:
        raise RuntimeError("_history_reply 里找不到 scene 赋值行")
    lines[target] = VARIANTS[key][1]
    PIPELINE.write_text(chr(10).join(lines), encoding="utf-8")


def restore_original() -> None:
    """把 pipeline.py 恢复成默认形态（B 组）。"""
    apply_variant(DEFAULT_VARIANT)


def run_group(key: str) -> dict:
    """跑一组：改代码、重启、跑探针、收数据。"""
    apply_variant(key)
    restart_backend()
    out_name = "exp_" + key.lower() + ".json"
    session = "s_exp_" + key.lower()
    env = dict(os.environ)
    env["XW_SESSION"] = session
    env["XW_OUT"] = out_name
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        ["F:\\python\\python.exe", str(PROBE)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
    )
    data_path = SWEEP / out_name
    payload = json.loads(data_path.read_text(encoding="utf-8")) if data_path.exists() else {}
    return {
        "key": key,
        "label": VARIANTS[key][0],
        "stdout": proc.stdout,
        "summary": payload.get("summary", {}),
        "rows": payload.get("rows", []),
    }


def main() -> int:
    """跑三组并写对比报告。"""
    SWEEP.mkdir(parents=True, exist_ok=True)
    groups = []
    for key in ("A", "B", "C"):
        data_path = SWEEP / ("exp_" + key.lower() + ".json")
        if data_path.exists():
            # 已有该组数据，复用，不重跑。
            payload = json.loads(data_path.read_text(encoding="utf-8"))
            groups.append({
                "key": key,
                "label": VARIANTS[key][0],
                "stdout": "",
                "summary": payload.get("summary", {}),
                "rows": payload.get("rows", []),
            })
            print("=== 复用已有 " + VARIANTS[key][0])
            continue
        print("=== 跑 " + VARIANTS[key][0])
        groups.append(run_group(key))
    restore_original()
    restart_backend()

    lines = [
        "# 历史场景形态三组对照",
        "",
        "三组只改 _history_reply 里场景的取法，其余代码、输入脚本、模型与采样一致。",
        "每组用独立会话跑满 6 轮连续动作的输入。",
        "",
        "## 设置",
        "",
    ]
    for group in groups:
        lines.append("- " + group["label"] + "（数据 exp_" + group["key"].lower() + ".json）")
    lines += [
        "",
        "## 逐轮指标",
        "",
        "| 组 | 轮 | 字数 | 带内 | 场景复制度 | 照抄 | 重复入场 | 代对方说话 | replace | 秒 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for group in groups:
        for row in group["rows"]:
            lines.append(
                "| %s | %d | %d | %s | %.2f | %s | %s | %s | %d | %.2f |"
                % (
                    group["key"],
                    row["turn"],
                    row["chars"],
                    "是" if row["in_band"] else "否",
                    row.get("scene_copy_ratio", 0.0),
                    "是" if row.get("scene_copied") else "否",
                    "是" if row.get("reentry_markers") else "否",
                    "是" if row.get("user_voice_markers") else "否",
                    row["replace_events"],
                    row["elapsed"],
                )
            )
    lines += ["", "## 汇总", "", "| 组 | 带内 | 场景照抄轮数 | 重复入场 | 代对方说话 | replace 合计 | 平均秒 |", "|---|---|---|---|---|---|---|"]
    for group in groups:
        summary = group["summary"]
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                group["key"],
                summary.get("in_band"),
                summary.get("scene_copied"),
                summary.get("reentry_turns"),
                summary.get("user_voice_turns"),
                summary.get("replace_events"),
                summary.get("elapsed_avg"),
            )
        )
    (SWEEP / "history_shape_ab.md").write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    print(chr(10).join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
