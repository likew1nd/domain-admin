#!/usr/bin/env python3
"""通过正式适配器执行只读 Dynadot 鉴权检查。"""
import os
import sys

from app.registrar_adapters import DynadotRegistrar


def main() -> int:
    api_key = os.environ.get("DYNADOT_KEY", "").strip()
    api_secret = os.environ.get("DYNADOT_SECRET", "").strip()
    if not api_key or not api_secret:
        print("请先设置 DYNADOT_KEY 和 DYNADOT_SECRET 环境变量。", file=sys.stderr)
        return 2

    endpoint = (os.environ.get("DYNADOT_ENDPOINT") or "https://api.dynadot.com").strip().rstrip("/")
    if endpoint not in {"https://api.dynadot.com", "https://api-sandbox.dynadot.com"}:
        print("DYNADOT_ENDPOINT 只能使用 https://api.dynadot.com 或 https://api-sandbox.dynadot.com。", file=sys.stderr)
        return 2

    try:
        success, status, response = DynadotRegistrar({"endpoint": endpoint}, f"{api_key}:{api_secret}").test()
    except Exception:
        # Do not print exceptions or response bodies: they may contain credentials or account data.
        print("请求失败：请检查网络连接、API 配置和后端依赖。", file=sys.stderr)
        return 1

    if success:
        print(f"鉴权成功（HTTP {status}）。")
        return 0

    hint = (
        "签名校验失败，请检查同一环境的 API Key/Secret，并确认后端已更新。"
        if "x-signature" in response.lower()
        else "请检查 API Key/Secret、所选环境、API 权限和 IP 白名单。"
    )
    print(f"鉴权失败（HTTP {status}）：{hint}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
