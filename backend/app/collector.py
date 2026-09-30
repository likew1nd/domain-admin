from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from . import db
from .crypto import decrypt_cookie
from .sources.base import SourceError, parse_domains
from .sources.registry import create_adapter


BASE_DIR = Path(__file__).resolve().parents[2]
DOWNLOAD_DIR = Path(os.getenv("DOMAIN_DOWNLOAD_DIR", str(BASE_DIR / "data" / "downloads")))
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="domain-import")
# West.cn keeps search state in the logged-in session. A single worker prevents
# concurrent suffix requests from triggering rate limits or account controls.
WEST_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="west-domain-import")


def _safe_filename(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value)[:180]


def _get_source(source_id: int) -> dict[str, Any]:
    source = db.fetch_one("SELECT * FROM sources WHERE id = ? AND deleted_at = ''", (source_id,))
    if source is None:
        raise SourceError("数据源不存在")
    return source


def _create_run(
    source: dict[str, Any], requested_date: str, file_format: str, trigger_type: str, status: str = "running"
) -> int:
    return db.execute(
        """
        INSERT INTO import_runs (
            source_id, source_key, requested_date, file_format, trigger_type,
            status, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source["id"], source["source_key"], requested_date, file_format, trigger_type, status, db.utc_now()),
    )


def _update_run(run_id: int, **values: Any) -> None:
    assignments = ", ".join(f"{key} = ?" for key in values)
    db.execute(f"UPDATE import_runs SET {assignments} WHERE id = ?", (*values.values(), run_id))


def _run_date(run_id: int, source: dict[str, Any], requested_date: str, file_format: str) -> None:
    adapter = None
    try:
        cookie = decrypt_cookie(source["cookie_ciphertext"])
        adapter = create_adapter(source, cookie)
        downloaded = adapter.download(requested_date, file_format)
        _run_content(
            run_id,
            source,
            requested_date,
            file_format,
            downloaded.content,
            downloaded.filename,
            source.get("parser", "line"),
        )
    except Exception as exc:  # the run is recorded so the UI can show the source error
        _update_run(run_id, status="failure", error=str(exc), finished_at=db.utc_now())
    finally:
        if adapter is not None:
            adapter.close()


def _run_content(
    run_id: int,
    source: dict[str, Any],
    requested_date: str,
    file_format: str,
    content: bytes,
    filename: str,
    parser: str = "line",
    persist_prefix: str = "",
) -> None:
    try:
        domains = parse_domains(content, file_format, parser)
        if not domains:
            raise SourceError("文件中未识别到有效域名")
        folder = DOWNLOAD_DIR / source["source_key"]
        folder.mkdir(parents=True, exist_ok=True)
        stored_name = f"{persist_prefix}{_safe_filename(filename)}" if persist_prefix else _safe_filename(filename)
        (folder / stored_name).write_bytes(content)
        inserted, updated = db.upsert_domains(source, requested_date, domains)
        _update_run(
            run_id,
            requested_date=requested_date,
            status="success",
            total_count=len(domains),
            inserted_count=inserted,
            updated_count=updated,
            downloaded_filename=filename,
            finished_at=db.utc_now(),
        )
    except Exception as exc:
        _update_run(run_id, status="failure", error=str(exc), finished_at=db.utc_now())


def _run_latest(run_id: int, source: dict[str, Any], file_format: str) -> None:
    adapter = None
    try:
        cookie = decrypt_cookie(source["cookie_ciphertext"])
        adapter = create_adapter(source, cookie)
        dates = adapter.available_dates()
        if not dates:
            raise SourceError("源站没有返回可下载日期")
        requested_date = dates[0]
        _run_date(run_id, source, requested_date, file_format)
    except Exception as exc:
        _update_run(run_id, status="failure", error=str(exc), finished_at=db.utc_now())
    finally:
        if adapter is not None:
            adapter.close()


def _run_west_suffix(run_id: int, source: dict[str, Any], requested_date: str, suffix: str) -> None:
    adapter = None
    try:
        cookie = decrypt_cookie(source["cookie_ciphertext"])
        adapter = create_adapter(source, cookie)
        download_suffix = getattr(adapter, "download_suffix", None)
        if download_suffix is None:
            raise SourceError("该数据源不支持按后缀导出")
        downloaded = download_suffix(suffix, "txt")
        _run_content(
            run_id,
            source,
            requested_date,
            "txt",
            downloaded.content,
            downloaded.filename,
            source.get("parser", "line"),
        )
    except Exception as exc:
        _update_run(run_id, status="failure", error=f"后缀 {suffix}：{exc}", finished_at=db.utc_now())
    finally:
        if adapter is not None:
            adapter.close()


def _run_west_suffixes(
    runs: list[tuple[int, str]], source: dict[str, Any], requested_date: str
) -> None:
    """Process one West.cn collection's suffixes in order.

    The site keeps search state in the authenticated session, so each suffix
    is fetched only after the previous suffix has finished importing.
    """
    for run_id, suffix in runs:
        _update_run(run_id, status="running", started_at=db.utc_now())
        _run_west_suffix(run_id, source, requested_date, suffix)


def queue_date(source_id: int, requested_date: str, file_format: str = "txt", trigger_type: str = "manual") -> int:
    if requested_date > date.today().isoformat():
        raise SourceError("不能采集未来日期")
    if file_format not in {"txt", "csv"}:
        raise SourceError("文件格式只支持 TXT 或 CSV")
    source = _get_source(source_id)
    run_id = _create_run(source, requested_date, file_format, trigger_type)
    EXECUTOR.submit(_run_date, run_id, source, requested_date, file_format)
    return run_id


def queue_latest(source_id: int, file_format: str = "txt", trigger_type: str = "schedule") -> int:
    if file_format not in {"txt", "csv"}:
        raise SourceError("文件格式只支持 TXT 或 CSV")
    source = _get_source(source_id)
    run_id = _create_run(source, "latest", file_format, trigger_type)
    EXECUTOR.submit(_run_latest, run_id, source, file_format)
    return run_id


def queue_west_suffixes(
    source_id: int,
    suffixes: list[str],
    requested_date: str | None = None,
    trigger_type: str = "manual-west",
) -> list[int]:
    source = _get_source(source_id)
    if source.get("adapter") != "west_cn":
        raise SourceError("只有西部数码数据源支持按后缀采集")
    if not source.get("cookie_ciphertext"):
        raise SourceError("请先在西部数码数据源中配置登录 Cookie")
    normalized = list(
        dict.fromkeys(str(suffix).strip().lower().lstrip(".") for suffix in suffixes if str(suffix).strip())
    )
    if not normalized:
        raise SourceError("请至少填写一个域名后缀")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", suffix) for suffix in normalized):
        raise SourceError("域名后缀格式不正确")
    requested = requested_date or date.today().isoformat()
    run_ids: list[int] = []
    runs: list[tuple[int, str]] = []
    for suffix in normalized:
        run_id = _create_run(source, requested, "txt", trigger_type, "queued")
        if trigger_type == "manual-west":
            _update_run(run_id, trigger_type=f"manual-west:{suffix}")
        run_ids.append(run_id)
        runs.append((run_id, suffix))
    WEST_EXECUTOR.submit(_run_west_suffixes, runs, source, requested)
    return run_ids


def queue_file(
    source_id: int,
    requested_date: str,
    content: bytes,
    filename: str,
    trigger_type: str = "manual-file",
) -> int:
    if not content:
        raise SourceError("上传文件不能为空")
    source = _get_source(source_id)
    run_id = _create_run(source, requested_date, "txt", trigger_type)
    safe_name = _safe_filename(filename or f"upload-{requested_date}.txt")
    EXECUTOR.submit(
        _run_content,
        run_id,
        source,
        requested_date,
        "txt",
        content,
        safe_name,
        "line",
        f"upload-{run_id}-",
    )
    return run_id


def shutdown_executor() -> None:
    EXECUTOR.shutdown(wait=False, cancel_futures=False)
    WEST_EXECUTOR.shutdown(wait=False, cancel_futures=False)
