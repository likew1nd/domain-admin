from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import aiohttp

from . import db
from .boce_client import CHECKS, STATUS_COLUMNS, BoceClient, BoceError, BoceFatalError, get_api_key
from .icp_client import IcpClient
from .whois_client import deletion_status, lookup, match_filters


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class ProxyLeasePool:
    """Reuse paid exit IPs; each IP has one active request at a time."""

    def __init__(self, config: dict[str, Any], task_id: int, max_slots: int = 1) -> None:
        self.task_id = task_id
        self.mode = config.get("mode", "direct")
        self.endpoint = str(config.get("endpoint", "") or "").strip()
        self.max_requests = max(1, int(config.get("max_requests", 50) or 50))
        self.max_slots = 1 if self.mode == "tunnel" else max(1, int(max_slots or 1))
        self._slots: list[dict[str, Any]] = []
        self._condition = asyncio.Condition()

    async def acquire(self) -> str | None:
        if self.mode == "direct":
            return None
        slot: dict[str, Any]
        async with self._condition:
            while True:
                slot = next(
                    (
                        item
                        for item in self._slots
                        if not item["busy"] and item["used"] < self.max_requests
                    ),
                    None,
                )
                if slot is not None:
                    slot["busy"] = True
                    slot["used"] += 1
                    self._save_stats()
                    return str(slot["proxy"])
                if len(self._slots) < self.max_slots:
                    slot = {"proxy": self.endpoint if self.mode == "tunnel" else "", "used": 1, "busy": True}
                    self._slots.append(slot)
                    break
                # 添加超时防止死锁：如果 60 秒内没有可用 IP，抛出异常让调用方重试
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=60)
                except asyncio.TimeoutError:
                    raise RuntimeError("代理池等待超时：所有 IP 槽位都被占用，请增加线程数或减少并发")

        try:
            proxy = self.endpoint if self.mode == "tunnel" else await self._fetch_one()
        except Exception:
            async with self._condition:
                if slot in self._slots:
                    self._slots.remove(slot)
                self._save_stats()
                self._condition.notify_all()
            raise

        async with self._condition:
            slot["proxy"] = proxy or ""
            self._save_stats(acquired=self.mode == "api", proxy=slot["proxy"])
            return str(slot["proxy"]) or None

    async def report(self, proxy: str | None, success: bool) -> None:
        if self.mode == "direct":
            return
        async with self._condition:
            slot = next((item for item in self._slots if item["busy"] and item["proxy"] == proxy), None)
            if slot is None:
                return
            if not success or slot["used"] >= self.max_requests:
                self._slots.remove(slot)
            else:
                slot["busy"] = False
            self._save_stats()
            self._condition.notify_all()

    def _save_stats(self, acquired: bool = False, proxy: str | None = None) -> None:
        if self.mode != "api":
            return
        available = sum(max(0, self.max_requests - int(slot["used"])) for slot in self._slots)
        current_ip = proxy or next((str(slot["proxy"]) for slot in self._slots if slot["proxy"]), "")
        if acquired:
            db.execute(
                """
                UPDATE query_tasks
                SET proxy_acquired_count = proxy_acquired_count + 1,
                    proxy_current_available = ?, proxy_current_ip = ?, updated_at = ?
                WHERE id = ?
                """,
                (available, current_ip, now(), self.task_id),
            )
            return
        db.execute(
            "UPDATE query_tasks SET proxy_current_available = ?, proxy_current_ip = ?, updated_at = ? WHERE id = ?",
            (available, current_ip, now(), self.task_id),
        )

    async def _fetch_one(self) -> str:
        if not self.endpoint:
            raise RuntimeError("未配置代理 API")
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(self.endpoint) as response:
                if response.status >= 400:
                    raise RuntimeError(f"代理 API HTTP {response.status}")
                text = await response.text()
        try:
            payload = json.loads(text)
            values = payload if isinstance(payload, list) else payload.get("data", payload.get("proxy", ""))
            if isinstance(values, list):
                text = "\n".join(str(item) for item in values)
            else:
                text = str(values)
        except json.JSONDecodeError:
            pass
        proxy = next((item.strip() for item in text.replace(",", "\n").splitlines() if item.strip()), "")
        if not proxy:
            raise RuntimeError("代理 API 未返回 IP")
        return proxy


class QueryTaskManager:
    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="domain-query")
        self._runtimes: dict[int, threading.Event] = {}
        self._lock = threading.RLock()

    def start(self, payload: dict[str, Any]) -> int:
        with self._lock:
            running = db.fetch_one("SELECT id FROM query_tasks WHERE status = 'running' LIMIT 1")
            if running:
                raise RuntimeError("已有查询任务正在运行")
            timestamp = now()
            task_id = db.execute(
                """
                INSERT INTO query_tasks (
                    name, filters_json, proxy_json, threads, whois_retries, icp_retries,
                    qq_retries, wechat_retries, douyin_retries, blocked_retries, pollution_retries,
                    blacklist_retries, continuous, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)
                """,
                (
                    payload.get("name") or "域名查询任务",
                    json.dumps(payload.get("filters") or {}, ensure_ascii=False),
                    json.dumps(payload.get("proxy") or {}, ensure_ascii=False),
                    max(1, min(50, int(payload.get("threads", 5) or 5))),
                    max(1, min(99, int(payload.get("whois_retries", 2) or 2))),
                    max(1, min(99, int(payload.get("icp_retries", 2) or 2))),
                    max(1, min(99, int(payload.get("qq_retries", 1) or 1))),
                    max(1, min(99, int(payload.get("wechat_retries", 1) or 1))),
                    max(1, min(99, int(payload.get("douyin_retries", 1) or 1))),
                    max(1, min(99, int(payload.get("blocked_retries", 2) or 2))),
                    max(1, min(99, int(payload.get("pollution_retries", 2) or 2))),
                    max(1, min(99, int(payload.get("blacklist_retries", 2) or 2))),
                    int(bool(payload.get("continuous", True))),
                    timestamp,
                    timestamp,
                ),
            )
            stop_event = threading.Event()
            self._runtimes[task_id] = stop_event
            self._executor.submit(self._run_sync, task_id, stop_event)
            return task_id

    def preview(self, filters: dict[str, Any]) -> int:
        return self._count_candidates(filters)

    def resume_all(self) -> None:
        for task in db.fetch_all(
            "SELECT id FROM query_tasks WHERE status IN ('created', 'running') AND stop_requested = 0"
        ):
            self.resume(int(task["id"]))

    def resume(self, task_id: int) -> None:
        with self._lock:
            if task_id in self._runtimes:
                return
            task = db.fetch_one("SELECT status, stop_requested FROM query_tasks WHERE id = ?", (task_id,))
            if not task or task["stop_requested"] or task["status"] not in {"created", "running"}:
                return
            stop_event = threading.Event()
            self._runtimes[task_id] = stop_event
            self._executor.submit(self._run_sync, task_id, stop_event)

    def stop(self, task_id: int) -> None:
        with self._lock:
            event = self._runtimes.get(task_id)
            if event:
                event.set()
            db.execute(
                "UPDATE query_tasks SET stop_requested = 1, updated_at = ? WHERE id = ? AND status = 'running'",
                (now(), task_id),
            )

    def shutdown(self) -> None:
        with self._lock:
            for event in self._runtimes.values():
                event.set()
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_sync(self, task_id: int, stop_event: threading.Event) -> None:
        asyncio.run(self._run(task_id, stop_event))

    @staticmethod
    def _log(
        task_id: int,
        stage: str,
        message: str,
        domain: str = "",
        level: str = "info",
        detail: str = "",
    ) -> None:
        db.execute(
            """
            INSERT INTO query_logs (task_id, created_at, level, stage, domain, message, detail)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, now(), level, stage, domain, message, detail[:1000]),
        )
        db.execute(
            "DELETE FROM query_logs WHERE id NOT IN (SELECT id FROM query_logs ORDER BY id DESC LIMIT 500)"
        )

    async def _run(self, task_id: int, stop_event: threading.Event) -> None:
        task = db.fetch_one("SELECT * FROM query_tasks WHERE id = ?", (task_id,))
        if task is None:
            return
        db.execute(
            "UPDATE query_tasks SET status = 'running', started_at = ?, updated_at = ? WHERE id = ?",
            (now(), now(), task_id),
        )
        self._log(task_id, "任务", "查询任务开始", detail=f"查询线程 {task['threads']}，持续查询 {'开启' if task['continuous'] else '关闭'}")
        filters = json.loads(task["filters_json"] or "{}")
        proxy_config = json.loads(task["proxy_json"] or "{}")
        proxy_stages = set(proxy_config.get("stages") or [])
        if proxy_config.get("mode") == "api":
            db.execute(
                "UPDATE query_tasks SET proxy_current_available = 0, proxy_current_ip = '', updated_at = ? WHERE id = ?",
                (now(), task_id),
            )
        proxy_pool = ProxyLeasePool(proxy_config, task_id, max_slots=task["threads"])
        client = IcpClient()
        intercept_items = [item for item in CHECKS if item in (filters.get("intercept_checks") or [])]
        boce_client: BoceClient | None = None
        fatal_errors: list[str] = []
        known_total = int(task["total_count"] or 0)
        try:
            if intercept_items:
                api_key = get_api_key()
                if not api_key:
                    raise RuntimeError("已勾选拦截检测，但未配置拦截检测 API Key")
                boce_client = BoceClient(api_key)
                self._log(
                    task_id,
                    "拦截检测",
                    "已启用拦截检测",
                    detail="、".join(CHECKS[item][0] for item in intercept_items),
                )
            # 使用队列实现真正的并发处理，避免 gather() 阻塞
            queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def process(row: dict[str, Any]) -> None:
                if stop_event.is_set():
                    return
                whois_info: dict[str, Any] = {}
                icp_result: dict[str, Any] = {“found”: False, “queried”: False, “data”: {}, “records”: []}
                # 未勾选或未执行的检测项记为”未检测”
                statuses = {column: “未检测” for column in STATUS_COLUMNS.values()}
                try:
                    self._log(task_id, “WHOIS”, “开始查询”, row[“domain”])
                    # 更新当前处理的域名（移到日志后面，避免阻塞）
                    db.execute(
                        “UPDATE query_tasks SET current_domain = ?, updated_at = ? WHERE id = ?”,
                        (row[“domain”], now(), task_id),
                    )
                    whois_info = await self._lookup_whois(
                        row[“domain”],
                        task[“whois_retries”],
                        task_id,
                        proxy_pool if “whois” in proxy_stages else None,
                    )
                    self._log(
                        task_id,
                        “WHOIS”,
                        “查询成功”,
                        row[“domain”],
                        detail=f”删除状态：{self._deletion_status(whois_info) or '未知'}”,
                    )
                    whois_ok, reason = match_filters(whois_info, filters)
                    self._log(
                        task_id,
                        “WHOIS”,
                        “通过筛选” if whois_ok else “未通过筛选”,
                        row[“domain”],
                        “info” if whois_ok else “warning”,
                        reason,
                    )
                    exception_ok = False
                    exception_reason = “”
                    intercepted = False
                    if whois_ok:
                        for attempt in range(max(1, task[“icp_retries”])):
                            self._log(
                                task_id,
                                “备案”,
                                f”开始查询（第 {attempt + 1} 次）”,
                                row[“domain”],
                            )
                            proxy = None
                            try:
                                icp_result[“queried”] = True
                                proxy = await proxy_pool.acquire() if “icp” in proxy_stages else None
                                icp_result = await client.query(row[“domain”], proxy)
                                if proxy is not None:
                                    await proxy_pool.report(proxy, True)
                                self._log(
                                    task_id,
                                    “备案”,
                                    “查询成功” if icp_result[“found”] else “查询完成，未发现备案”,
                                    row[“domain”],
                                    “info” if icp_result[“found”] else “warning”,
                                    f”备案记录 {len(icp_result.get('records') or [])} 条”,
                                )
                                break
                            except Exception as exc:
                                if proxy is not None:
                                    await proxy_pool.report(proxy, False)
                                self._log(
                                    task_id,
                                    “备案”,
                                    f”查询失败（第 {attempt + 1} 次）”,
                                    row[“domain”],
                                    “error” if attempt + 1 >= max(1, task[“icp_retries”]) else “warning”,
                                    str(exc),
                                )
                                if attempt + 1 >= max(1, task[“icp_retries”]):
                                    icp_result = {
                                        “found”: False,
                                        “queried”: True,
                                        “data”: {},
                                        “records”: [],
                                        “error”: str(exc),
                                    }
                                    raise
                        exception_ok, exception_reason = self._match_exception(row[“domain”], filters)
                        # 备案之后再做拦截检测；未备案且不命中例外的域名必然不符合，不再消耗检测额度
                        hits: list[str] = []
                        if boce_client and (icp_result[“found”] or exception_ok):
                            hits = await self._intercept_check(
                                task,
                                row[“domain”],
                                intercept_items,
                                boce_client,
                                proxy_pool,
                                proxy_stages,
                                statuses,
                            )
                        intercepted = bool(hits)
                        hit_text = “、”.join(hits)
                        if icp_result[“found”] and not intercepted:
                            reason = “WHOIS、备案与拦截检测均符合” if boce_client else “WHOIS 与 ICP 条件均符合”
                        elif exception_ok:
                            if icp_result[“found”]:
                                prefix = f”已备案，拦截检测未通过（{hit_text}）”
                            elif intercepted:
                                prefix = f”WHOIS 通过，未备案，拦截检测未通过（{hit_text}）”
                            else:
                                prefix = “WHOIS 通过，未备案”
                            reason = f”{prefix}，命中例外策略：{exception_reason}”
                            self._log(
                                task_id,
                                “例外策略”,
                                “命中例外策略，归类为符合”,
                                row[“domain”],
                                detail=exception_reason,
                            )
                        elif intercepted:
                            reason = f”拦截检测未通过：{hit_text}”
                        else:
                            reason = “未查询到 ICP 备案”
                    else:
                        self._log(task_id, “备案”, “跳过备案查询”, row[“domain”], “warning”, reason)
                    # 例外策略优先：命中例外即符合；否则需已备案且未被拦截
                    passed = whois_ok and (exception_ok or (icp_result[“found”] and not intercepted))
                    result = “qualified” if passed else “unqualified”
                    self._save_result(task_id, row[“domain”], result, reason, whois_info, icp_result, statuses)
                    self._log(
                        task_id,
                        “结果”,
                        “已归类为符合” if result == “qualified” else “已归类为不符合”,
                        row[“domain”],
                        “info” if result == “qualified” else “warning”,
                        reason,
                    )
                except BoceFatalError as exc:
                    # 鉴权失败、波点不足时继续查询只会重复失败，直接终止任务
                    fatal_errors.append(str(exc))
                    stop_event.set()
                except Exception as exc:
                    self._log(task_id, “任务”, “查询失败，等待重试”, row[“domain”], “error”, str(exc))
                    await asyncio.sleep(2)

            async def worker() -> None:
                “””工作协程：从队列中取出域名并处理”””
                while True:
                    row = await queue.get()
                    if row is None:  # 结束信号
                        queue.task_done()
                        break
                    try:
                        await process(row)
                    finally:
                        queue.task_done()

            # 启动工作线程
            workers = [asyncio.create_task(worker()) for _ in range(task[“threads”])]

            while not stop_event.is_set():
                candidates = self._candidates(filters, max(20, task[“threads”] * 4))
                pending = self._count_candidates(filters)
                processed = db.fetch_one(“SELECT processed_count FROM query_tasks WHERE id = ?”, (task_id,))[“processed_count”]
                known_total = max(known_total, int(processed) + pending)
                db.execute(“UPDATE query_tasks SET total_count = ?, updated_at = ? WHERE id = ?”, (known_total, now(), task_id))
                if not candidates:
                    if not task[“continuous”]:
                        break
                    await asyncio.to_thread(stop_event.wait, 15)
                    continue

                # 将候选域名加入队列
                for row in candidates:
                    await queue.put(row)

                # 等待当前批次处理完成
                await queue.join()

                if fatal_errors:
                    raise RuntimeError(fatal_errors[0])

            # 发送结束信号给所有worker
            for _ in range(task[“threads”]):
                await queue.put(None)

            # 等待所有worker结束
            await asyncio.gather(*workers)
            final_status = "stopped" if stop_event.is_set() else "completed"
            db.execute(
                "UPDATE query_tasks SET status = ?, finished_at = ?, current_domain = '', updated_at = ? WHERE id = ?",
                (final_status, now(), now(), task_id),
            )
            self._log(task_id, "任务", "查询任务已停止" if final_status == "stopped" else "查询任务已完成")
        except Exception as exc:
            db.execute(
                "UPDATE query_tasks SET status = 'failed', error = ?, finished_at = ?, updated_at = ? WHERE id = ?",
                (str(exc), now(), now(), task_id),
            )
            self._log(task_id, "任务", "查询任务异常终止", level="error", detail=str(exc))
        finally:
            with self._lock:
                self._runtimes.pop(task_id, None)

    @staticmethod
    async def _lookup_whois(
        domain: str,
        retries: int,
        task_id: int | None = None,
        proxy_pool: ProxyLeasePool | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(max(1, retries)):
            proxy: str | None = None
            proxy_acquire_failed = False
            try:
                if proxy_pool is not None:
                    if task_id is not None:
                        QueryTaskManager._log(
                            task_id,
                            "代理池",
                            f"正在获取代理 IP（第 {attempt + 1} 次尝试）",
                            domain,
                            "info",
                        )
                    try:
                        proxy = await proxy_pool.acquire()
                        if task_id is not None and proxy:
                            QueryTaskManager._log(
                                task_id,
                                "代理池",
                                f"已获取代理 IP（第 {attempt + 1} 次尝试）",
                                domain,
                                "info",
                                f"代理：{proxy}",
                            )
                    except Exception as proxy_exc:
                        proxy_acquire_failed = True
                        if task_id is not None:
                            QueryTaskManager._log(
                                task_id,
                                "代理池",
                                f"获取代理 IP 失败（第 {attempt + 1} 次尝试）",
                                domain,
                                "error",
                                str(proxy_exc),
                            )
                        raise
                lookup_call = (lookup, domain, proxy) if proxy else (lookup, domain)
                result = await asyncio.wait_for(asyncio.to_thread(*lookup_call), timeout=30)
                if not deletion_status(result):
                    raise RuntimeError("WHOIS 返回信息不足，无法确定域名状态")
                if proxy is not None:
                    await proxy_pool.report(proxy, True)
                return result
            except Exception as exc:
                if proxy is not None and proxy_pool is not None:
                    await proxy_pool.report(proxy, False)
                last_error = exc
                if task_id is not None and not proxy_acquire_failed:
                    proxy_detail = (
                        f"；代理：{proxy}"
                        if proxy
                        else ("；代理：获取失败" if proxy_pool and proxy_pool.mode != "direct" else "；代理：直连")
                    )
                    QueryTaskManager._log(
                        task_id,
                        "WHOIS",
                        f"查询失败（第 {attempt + 1} 次）",
                        domain,
                        "error" if attempt + 1 >= max(1, retries) else "warning",
                        f"{exc}{proxy_detail}",
                    )
                # 如果是代理池超时，不再继续重试（因为重试也会卡住）
                if "代理池等待超时" in str(exc):
                    raise
        raise last_error or RuntimeError("WHOIS 查询失败")

    @staticmethod
    def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        conditions = ["(query_time = '' OR query_time IS NULL)"]
        params: list[Any] = []
        label_expression = "substr(domain, 1, length(domain) - instr(reverse(domain), '.'))"
        lengths = [int(value) for value in filters.get("lengths", []) if str(value).isdigit()]
        # suffix、label_length 为导入时预先计算的列，可走 idx_domains_suffix 索引
        if lengths:
            conditions.append(f"label_length IN ({','.join('?' for _ in lengths)})")
            params.extend(lengths)
        suffixes = [str(value).lower().lstrip('.') for value in filters.get("suffixes", []) if str(value).strip()]
        if suffixes:
            conditions.append(f"suffix IN ({','.join('?' for _ in suffixes)})")
            params.extend(suffixes)
        exclude_chars = [
            str(value).strip().lower()
            for value in filters.get("exclude_chars", [])
            if str(value).strip()
        ]
        for value in exclude_chars:
            conditions.append(f"instr(lower({label_expression}), ?) = 0")
            params.append(value)
        composition = db.normalize_composition(filters.get("domain_composition"))
        if composition:
            condition, kinds = db.label_kind_condition(composition)
            conditions.append(condition)
            params.extend(kinds)
        return " AND ".join(conditions), params

    def _candidates(self, filters: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        where, params = self._where(filters)
        return db.fetch_all(f"SELECT domain FROM domains WHERE {where} ORDER BY joined_at DESC, domain LIMIT ?", (*params, limit))

    def _count_candidates(self, filters: dict[str, Any]) -> int:
        where, params = self._where(filters)
        row = db.fetch_one(f"SELECT COUNT(*) AS total FROM domains WHERE {where}", tuple(params))
        return int(row["total"] if row else 0)

    @staticmethod
    def _match_exception(domain: str, filters: dict[str, Any]) -> tuple[bool, str]:
        """Apply exception rules after WHOIS has passed; configured rules are OR-ed."""
        exception = filters.get("exceptions") or {}
        if not exception.get("enabled"):
            return False, ""
        label, _, suffix = str(domain).strip().lower().partition(".")
        lengths = {int(value) for value in exception.get("lengths", []) if str(value).isdigit()}
        if lengths and len(label) in lengths:
            return True, f"长度 {len(label)}"
        suffixes = {str(value).strip().lower().lstrip(".") for value in exception.get("suffixes", []) if str(value).strip()}
        if suffixes and suffix in suffixes:
            return True, f"后缀 .{suffix}"
        patterns = {str(value).strip().upper() for value in exception.get("patterns", []) if str(value).strip()}
        for pattern in patterns:
            if len(pattern) != len(label) or not pattern.isascii() or not pattern.isalpha():
                continue
            markers: dict[str, str] = {}
            used_chars: dict[str, str] = {}
            valid = True
            for marker, char in zip(pattern, label):
                if marker in markers and markers[marker] != char:
                    valid = False
                    break
                if marker not in markers and char in used_chars and used_chars[char] != marker:
                    valid = False
                    break
                markers[marker] = char
                used_chars[char] = marker
            if valid:
                return True, f"模式 {pattern}"
        contains = [str(value).strip().lower() for value in exception.get("contains", []) if str(value).strip()]
        for value in contains:
            if value in label:
                return True, f"包含字符 {value}"
        return False, ""

    async def _intercept_check(
        self,
        task: dict[str, Any],
        domain: str,
        items: list[str],
        client: BoceClient,
        proxy_pool: ProxyLeasePool,
        proxy_stages: set[str],
        statuses: dict[str, str],
    ) -> list[str]:
        """并发执行勾选的检测项，结果写入 statuses，返回命中的检测项名称。"""
        task_id = task["id"]

        async def run(item: str) -> str | None:
            label = CHECKS[item][0]
            retries = max(1, int(task.get(f"{item}_retries") or 1))
            for attempt in range(retries):
                last = attempt + 1 >= retries
                proxy = await proxy_pool.acquire() if item in proxy_stages else None
                try:
                    hit, text = await client.check(item, domain, proxy)
                except BoceFatalError:
                    raise
                except Exception as exc:
                    if proxy is not None:
                        await proxy_pool.report(proxy, False)
                    retryable = not isinstance(exc, BoceError) or exc.retryable
                    message = str(exc) or exc.__class__.__name__
                    self._log(
                        task_id,
                        "拦截检测",
                        f"{label}检测失败（第 {attempt + 1} 次）",
                        domain,
                        "error" if last or not retryable else "warning",
                        message,
                    )
                    if not retryable:
                        # 如域名格式错误，重试无意义，标记后按未命中处理
                        statuses[STATUS_COLUMNS[item]] = "检测失败"
                        return None
                    if last:
                        raise RuntimeError(f"{label}检测失败：{message}") from exc
                    continue
                if proxy is not None:
                    await proxy_pool.report(proxy, True)
                statuses[STATUS_COLUMNS[item]] = "是" if hit else "否"
                self._log(task_id, "拦截检测", f"{label}：{text}", domain, "warning" if hit else "info")
                return label if hit else None
            return None

        results = await asyncio.gather(*(run(item) for item in items))
        return [label for label in results if label]

    @staticmethod
    def _save_result(
        task_id: int,
        domain: str,
        result: str,
        reason: str,
        whois_info: dict[str, Any],
        icp_result: dict[str, Any],
        statuses: dict[str, str],
    ) -> None:
        checked_at = now()
        deletion_status = QueryTaskManager._deletion_status(whois_info)
        filing_nature, filing_info = QueryTaskManager._filing_info(icp_result)
        db.execute(
            """
            INSERT INTO domain_checks (
                domain, task_id, result, reason, deletion_status, whois_status, expiration_date,
                creation_date, icp_found, qq_status, wechat_status, douyin_status, blocked_status,
                pollution_status, blacklist_status,
                filing_nature, filing_info, icp_data, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '否', ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                task_id = excluded.task_id, result = excluded.result, reason = excluded.reason,
                deletion_status = excluded.deletion_status,
                whois_status = excluded.whois_status, expiration_date = excluded.expiration_date,
                creation_date = excluded.creation_date, icp_found = excluded.icp_found,
                qq_status = excluded.qq_status, wechat_status = excluded.wechat_status,
                douyin_status = excluded.douyin_status, blocked_status = excluded.blocked_status,
                pollution_status = excluded.pollution_status, blacklist_status = excluded.blacklist_status,
                filing_nature = excluded.filing_nature, filing_info = excluded.filing_info,
                icp_data = excluded.icp_data, checked_at = excluded.checked_at
            """,
            (
                domain,
                task_id,
                result,
                reason,
                deletion_status,
                whois_info.get("status", ""),
                whois_info.get("expiration_date", ""),
                whois_info.get("creation_date", ""),
                int(bool(icp_result.get("found"))),
                statuses["qq_status"],
                statuses["wechat_status"],
                statuses["blocked_status"],
                statuses["pollution_status"],
                statuses["blacklist_status"],
                filing_nature,
                filing_info,
                json.dumps(icp_result.get("data", {}), ensure_ascii=False),
                checked_at,
            ),
        )
        db.execute("UPDATE domains SET query_time = ? WHERE domain = ?", (checked_at, domain))
        if result == "qualified":
            field = "qualified_count"
        else:
            field = "unqualified_count"
        db.execute(
            f"UPDATE query_tasks SET processed_count = processed_count + 1, {field} = {field} + 1, updated_at = ? WHERE id = ?",
            (checked_at, task_id),
        )

    @staticmethod
    def _deletion_status(whois_info: dict[str, Any]) -> str:
        return deletion_status(whois_info)

    @staticmethod
    def _filing_info(icp_result: dict[str, Any]) -> tuple[str, str]:
        if icp_result.get("error"):
            return "查询失败", str(icp_result["error"])
        if icp_result.get("queried"):
            records = icp_result.get("records") or []
            if not records:
                return "未备案", ""
        else:
            return "未查询", ""
        records = icp_result.get("records") or []
        if not records:
            return "", ""
        record = records[0]
        nature = str(record.get("natureName") or record.get("mainUnitNature") or "")
        unit = str(record.get("unitName") or record.get("mainUnitName") or "")
        licence = str(record.get("mainLicence") or record.get("serviceLicence") or "")
        return nature or "已备案", "\n".join(item for item in (unit, licence) if item)


query_manager = QueryTaskManager()
