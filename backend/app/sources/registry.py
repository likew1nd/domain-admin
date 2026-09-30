from __future__ import annotations

from typing import Any

from .base import SourceAdapter, SourceError
from .gname import GnameAdapter
from .west_cn import WestCnAdapter


ADAPTERS = {
    "gname": GnameAdapter,
    "west_cn": WestCnAdapter,
    "generic": SourceAdapter,
}


def create_adapter(source: dict[str, Any], cookie: str) -> SourceAdapter:
    adapter_class = ADAPTERS.get(source.get("adapter", "generic"))
    if adapter_class is None:
        raise SourceError(f"不支持的数据源类型：{source.get('adapter')}")
    return adapter_class(source, cookie)
