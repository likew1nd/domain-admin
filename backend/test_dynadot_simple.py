#!/usr/bin/env python3
"""兼容旧入口；执行正式 Dynadot 鉴权检查，不尝试其他签名格式。"""
from test_dynadot_auth import main


if __name__ == "__main__":
    raise SystemExit(main())
