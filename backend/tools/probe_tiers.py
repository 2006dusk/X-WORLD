r"""模型档位探针：档位清单、切换与停服。

走真实接口：GET /v1/inference/tiers、POST /v1/inference/start、
POST /v1/inference/stop、GET /v1/inference/status。

切换会先停后起，8GB 显存装不下两个大档位并存。

用法：
    python backend\tools\probe_tiers.py [档位id ...]
"""

from __future__ import annotations

import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8760"


def get(path: str) -> dict:
    """读一个接口。"""
    resp = httpx.get(BASE + path, timeout=60.0)
    resp.raise_for_status()
    return resp.json()


def post(path: str, payload: dict) -> dict:
    """写一个接口。"""
    resp = httpx.post(BASE + path, json=payload, timeout=900.0)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    """逐个档位验证。"""
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")]
    data = get("/v1/inference/tiers")
    items = data.get("items") or []
    print("档位清单：")
    for item in items:
        measured = item.get("measured") or {}
        print(
            "  %-12s %-18s %s MB  n_ctx=%s kv=%s  实测 %s tok/s  每轮 %s s  %s"
            % (
                item.get("profile_id"),
                item.get("model_file"),
                item.get("size_mb"),
                item.get("n_ctx"),
                item.get("kv_type"),
                measured.get("decode_tok_per_s"),
                measured.get("turn_wait_s"),
                "运行中" if item.get("running") else "未运行",
            )
        )
    if not items:
        print("没有档位")
        return 1

    targets = [item for item in items if not wanted or item.get("profile_id") in wanted]
    failures = 0
    for item in targets:
        profile_id = str(item.get("profile_id"))
        print("\n切换到 %s（%s）" % (profile_id, item.get("name")))
        started = time.perf_counter()
        try:
            service = post("/v1/inference/start", {"profile_id": profile_id})
        except httpx.HTTPError as exc:
            print("    启动失败：%s" % exc)
            failures += 1
            continue
        print("    启动回执：%s" % json.dumps(service.get("service"), ensure_ascii=False))
        print("    用时 %.1f s" % (time.perf_counter() - started))
        status = get("/v1/inference/status")
        svc = status.get("service") or {}
        profile = status.get("profile") or {}
        print("    服务可达：%s，地址 %s" % (svc.get("reachable"), svc.get("base_url")))
        print("    生效档位：%s（%s）" % (profile.get("id"), profile.get("name")))
        if not svc.get("reachable"):
            print("    失败：服务不可达")
            failures += 1
        if profile.get("id") != profile_id:
            print("    失败：生效档位与目标不一致")
            failures += 1

    print("\n停掉托管中的服务")
    result = post("/v1/inference/stop", {})
    print("    停掉：%s" % result.get("stopped"))
    print("\n失败项：%d" % failures)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
