from __future__ import annotations

import itertools
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.getenv("DOMAIN_DB_PATH", str(BASE_DIR / "data" / "domains.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_db_lock = threading.RLock()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_chinese(char: str) -> bool:
    return "㐀" <= char <= "䶿" or "一" <= char <= "鿿" or "豈" <= char <= "﫿"


# 域名主体字符类别：英文、数字、中文、符号（其余字符，如 -）；label_kind 按此顺序拼接所含类别的代码
CHAR_CLASSES = {"letter": "a", "digit": "n", "chinese": "c", "symbol": "s"}
# 旧版单选的域名组成取值，读取历史配置与任务时转换为类别组合
LEGACY_COMPOSITIONS = {
    "english": ["letter"],
    "numeric": ["digit"],
    "alphanumeric": ["letter", "digit"],
    "chinese": ["chinese"],
}
LEGACY_LABEL_KINDS = ("", "english", "numeric", "alnum", "chinese", "other")


def _char_class(char: str) -> str:
    if char.isascii() and char.isalpha():
        return "a"
    if char.isascii() and char.isdigit():
        return "n"
    if _is_chinese(char):
        return "c"
    return "s"


def label_kind(value: Any) -> str:
    found = {_char_class(char) for char in str(value or "")}
    return "".join(code for code in CHAR_CLASSES.values() if code in found)


def domain_label_kind(domain: Any) -> str:
    """预先计算域名主体包含的字符类别，存入 domains.label_kind 以便按组成筛选时走索引。"""
    return label_kind(str(domain or "").rpartition(".")[0] or str(domain or ""))


def split_domain(domain: Any) -> tuple[str, str]:
    """拆出域名主体与后缀（最后一段），与 SQL 中 reverse/instr 的拆分规则一致。"""
    value = str(domain or "")
    label, dot, suffix = value.rpartition(".")
    return (label, suffix.lower()) if dot else (value, "")


def normalize_composition(value: Any) -> list[str]:
    """统一为字符类别列表（兼容旧版单选值与逗号分隔字符串），空列表表示不限。"""
    if isinstance(value, str):
        value = LEGACY_COMPOSITIONS.get(value) or value.replace("，", ",").split(",")
    selected = {str(item).strip() for item in value or []}
    return [item for item in CHAR_CLASSES if item in selected]


# 含英文的组合覆盖了绝大多数域名，走 label_kind 索引反而要对上百万行排序；用一元 + 让 SQLite 按 joined_at 顺序扫描过滤
def label_kind_condition(classes: list[str], column: str = "label_kind") -> tuple[str, list[str]]:
    """只由所选类别字符组成：label_kind 为所选类别代码的任一非空子集。"""
    codes = [CHAR_CLASSES[item] for item in classes]
    kinds = [
        "".join(code for code, used in zip(codes, mask) if used)
        for mask in itertools.product((False, True), repeat=len(codes))
        if any(mask)
    ]
    prefix = "+" if "letter" in classes else ""
    return f"{prefix}{column} IN ({','.join('?' for _ in kinds)})", kinds


def get_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.create_function("reverse", 1, lambda value: str(value)[::-1])
    connection.create_function("label_kind_of", 1, label_kind)
    return connection


def init_db() -> None:
    with _db_lock, get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                adapter TEXT NOT NULL DEFAULT 'generic',
                base_url TEXT NOT NULL,
                page_path TEXT NOT NULL DEFAULT '',
                download_template TEXT NOT NULL,
                parser TEXT NOT NULL DEFAULT 'line',
                cookie_ciphertext TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                deleted_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS domains (
                domain TEXT PRIMARY KEY,
                source_id INTEGER NOT NULL,
                source_name TEXT NOT NULL,
                joined_at TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                query_time TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );

            CREATE INDEX IF NOT EXISTS idx_domains_joined_at ON domains(joined_at DESC);
            CREATE INDEX IF NOT EXISTS idx_domains_source_id ON domains(source_id);

            CREATE TABLE IF NOT EXISTS import_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                source_key TEXT NOT NULL,
                requested_date TEXT NOT NULL,
                file_format TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                status TEXT NOT NULL,
                total_count INTEGER NOT NULL DEFAULT 0,
                inserted_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                downloaded_filename TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );

            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL,
                run_time TEXT NOT NULL DEFAULT '08:00',
                suffixes_json TEXT NOT NULL DEFAULT '[]',
                enabled INTEGER NOT NULL DEFAULT 1,
                last_run_key TEXT NOT NULL DEFAULT '',
                last_run_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS query_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                filters_json TEXT NOT NULL,
                proxy_json TEXT NOT NULL,
                threads INTEGER NOT NULL DEFAULT 5,
                whois_retries INTEGER NOT NULL DEFAULT 2,
                icp_retries INTEGER NOT NULL DEFAULT 2,
                qq_retries INTEGER NOT NULL DEFAULT 1,
                wechat_retries INTEGER NOT NULL DEFAULT 1,
                douyin_retries INTEGER NOT NULL DEFAULT 1,
                blocked_retries INTEGER NOT NULL DEFAULT 2,
                continuous INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'created',
                stop_requested INTEGER NOT NULL DEFAULT 0,
                total_count INTEGER NOT NULL DEFAULT 0,
                processed_count INTEGER NOT NULL DEFAULT 0,
                qualified_count INTEGER NOT NULL DEFAULT 0,
                unqualified_count INTEGER NOT NULL DEFAULT 0,
                proxy_acquired_count INTEGER NOT NULL DEFAULT 0,
                proxy_current_available INTEGER NOT NULL DEFAULT 0,
                proxy_current_ip TEXT NOT NULL DEFAULT '',
                current_domain TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT '',
                finished_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS query_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                settings_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_secrets (
                name TEXT PRIMARY KEY,
                ciphertext TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS domain_checks (
                domain TEXT PRIMARY KEY,
                task_id INTEGER NOT NULL,
                result TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                deletion_status TEXT NOT NULL DEFAULT '',
                whois_status TEXT NOT NULL DEFAULT '',
                expiration_date TEXT NOT NULL DEFAULT '',
                creation_date TEXT NOT NULL DEFAULT '',
                icp_found INTEGER NOT NULL DEFAULT 0,
                qq_status TEXT NOT NULL DEFAULT '否',
                wechat_status TEXT NOT NULL DEFAULT '否',
                douyin_status TEXT NOT NULL DEFAULT '否',
                blocked_status TEXT NOT NULL DEFAULT '否',
                pollution_status TEXT NOT NULL DEFAULT '否',
                blacklist_status TEXT NOT NULL DEFAULT '否',
                filing_nature TEXT NOT NULL DEFAULT '',
                filing_info TEXT NOT NULL DEFAULT '',
                icp_data TEXT NOT NULL DEFAULT '',
                checked_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES query_tasks(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_domain_checks_result ON domain_checks(result);
            CREATE INDEX IF NOT EXISTS idx_domain_checks_checked_at ON domain_checks(checked_at DESC);

            CREATE TABLE IF NOT EXISTS query_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                stage TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_query_logs_created_at ON query_logs(created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_query_logs_task_id ON query_logs(task_id, id DESC);

            CREATE TABLE IF NOT EXISTS registrar_apis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                adapter TEXT NOT NULL DEFAULT 'http_json',
                endpoint TEXT NOT NULL,
                token_ciphertext TEXT NOT NULL DEFAULT '',
                headers_json TEXT NOT NULL DEFAULT '{}',
                config_json TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                interval_seconds INTEGER NOT NULL DEFAULT 30,
                concurrency INTEGER NOT NULL DEFAULT 5,
                whois_retries INTEGER NOT NULL DEFAULT 2,
                auto_register INTEGER NOT NULL DEFAULT 0,
                auto_start INTEGER NOT NULL DEFAULT 0,
                availability_api_id INTEGER NOT NULL DEFAULT 0,
                api_ids_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL DEFAULT 'stopped',
                total_count INTEGER NOT NULL DEFAULT 0,
                checked_count INTEGER NOT NULL DEFAULT 0,
                available_count INTEGER NOT NULL DEFAULT 0,
                registered_count INTEGER NOT NULL DEFAULT 0,
                kicked_count INTEGER NOT NULL DEFAULT 0,
                current_domain TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS monitor_domain_state (
                domain TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'monitoring',
                last_checked_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS kicked_domains (
                domain TEXT PRIMARY KEY,
                reason TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'monitor',
                kicked_at TEXT NOT NULL,
                deletion_status TEXT NOT NULL DEFAULT '',
                creation_date TEXT NOT NULL DEFAULT '',
                expiration_date TEXT NOT NULL DEFAULT '',
                wechat_status TEXT NOT NULL DEFAULT '否',
                qq_status TEXT NOT NULL DEFAULT '否',
                pollution_status TEXT NOT NULL DEFAULT '否',
                blocked_status TEXT NOT NULL DEFAULT '否',
                blacklist_status TEXT NOT NULL DEFAULT '否',
                filing_nature TEXT NOT NULL DEFAULT '',
                filing_info TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS registration_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                registrar_api_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                attempted_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (registrar_api_id) REFERENCES registrar_apis(id)
            );
            CREATE INDEX IF NOT EXISTS idx_registration_attempts_domain ON registration_attempts(domain, id DESC);

            CREATE TABLE IF NOT EXISTS monitor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                stage TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_monitor_logs_created_at ON monitor_logs(id DESC);
            """
        )

        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO monitor_settings (id, updated_at) VALUES (1, ?)",
            (now,),
        )
        connection.execute(
            "INSERT OR IGNORE INTO monitor_state (id, updated_at) VALUES (1, ?)",
            (now,),
        )

        domain_columns = {row["name"] for row in connection.execute("PRAGMA table_info(domains)").fetchall()}
        if "query_time" not in domain_columns:
            connection.execute("ALTER TABLE domains ADD COLUMN query_time TEXT NOT NULL DEFAULT ''")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_domains_query_time ON domains(query_time)")
        if "label_kind" not in domain_columns:
            connection.execute("ALTER TABLE domains ADD COLUMN label_kind TEXT NOT NULL DEFAULT ''")
        connection.create_function("domain_label_kind", 1, domain_label_kind)
        # 旧版按英文/数字/英数/中文/其他分类，一次性重算为字符类别组合
        connection.execute(
            f"UPDATE domains SET label_kind = domain_label_kind(domain) WHERE label_kind IN ({','.join('?' for _ in LEGACY_LABEL_KINDS)})",
            LEGACY_LABEL_KINDS,
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_domains_label_kind ON domains(label_kind)")
        if "suffix" not in domain_columns:
            connection.execute("ALTER TABLE domains ADD COLUMN suffix TEXT NOT NULL DEFAULT ''")
        if "label_length" not in domain_columns:
            connection.execute("ALTER TABLE domains ADD COLUMN label_length INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            """
            UPDATE domains
            SET suffix = lower(substr(domain, length(domain) - instr(reverse(domain), '.') + 2)),
                label_length = length(substr(domain, 1, length(domain) - instr(reverse(domain), '.')))
            WHERE label_length = 0
            """
        )
        # 覆盖查询任务预览常用的筛选列，统计数量时无需回表
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_domains_suffix ON domains(suffix, label_length, label_kind, query_time)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_domains_label_length ON domains(label_length, label_kind, query_time)"
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_domains_joined_domain ON domains(joined_at DESC, domain)")
        registrar_columns = {row["name"] for row in connection.execute("PRAGMA table_info(registrar_apis)").fetchall()}
        if "config_json" not in registrar_columns:
            connection.execute("ALTER TABLE registrar_apis ADD COLUMN config_json TEXT NOT NULL DEFAULT '{}'")
        monitor_columns = {row["name"] for row in connection.execute("PRAGMA table_info(monitor_settings)").fetchall()}
        if "availability_api_id" not in monitor_columns:
            connection.execute("ALTER TABLE monitor_settings ADD COLUMN availability_api_id INTEGER NOT NULL DEFAULT 0")
        source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)").fetchall()}
        if "deleted_at" not in source_columns:
            connection.execute("ALTER TABLE sources ADD COLUMN deleted_at TEXT NOT NULL DEFAULT ''")
        schedule_columns = {row["name"] for row in connection.execute("PRAGMA table_info(schedules)").fetchall()}
        if "suffixes_json" not in schedule_columns:
            connection.execute("ALTER TABLE schedules ADD COLUMN suffixes_json TEXT NOT NULL DEFAULT '[]'")
        query_task_columns = {row["name"] for row in connection.execute("PRAGMA table_info(query_tasks)").fetchall()}
        for name, definition in {
            "whois_retries": "INTEGER NOT NULL DEFAULT 2",
            "icp_retries": "INTEGER NOT NULL DEFAULT 2",
            "qq_retries": "INTEGER NOT NULL DEFAULT 1",
            "wechat_retries": "INTEGER NOT NULL DEFAULT 1",
            "douyin_retries": "INTEGER NOT NULL DEFAULT 1",
            "blocked_retries": "INTEGER NOT NULL DEFAULT 2",
            "pollution_retries": "INTEGER NOT NULL DEFAULT 2",
            "blacklist_retries": "INTEGER NOT NULL DEFAULT 2",
            "proxy_acquired_count": "INTEGER NOT NULL DEFAULT 0",
            "proxy_current_available": "INTEGER NOT NULL DEFAULT 0",
            "proxy_current_ip": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in query_task_columns:
                connection.execute(f"ALTER TABLE query_tasks ADD COLUMN {name} {definition}")
        check_columns = {row["name"] for row in connection.execute("PRAGMA table_info(domain_checks)").fetchall()}
        for name, definition in {
            "deletion_status": "TEXT NOT NULL DEFAULT ''",
            "qq_status": "TEXT NOT NULL DEFAULT '否'",
            "wechat_status": "TEXT NOT NULL DEFAULT '否'",
            "douyin_status": "TEXT NOT NULL DEFAULT '否'",
            "blocked_status": "TEXT NOT NULL DEFAULT '否'",
            "pollution_status": "TEXT NOT NULL DEFAULT '否'",
            "blacklist_status": "TEXT NOT NULL DEFAULT '否'",
            "filing_nature": "TEXT NOT NULL DEFAULT ''",
            "filing_info": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in check_columns:
                connection.execute(f"ALTER TABLE domain_checks ADD COLUMN {name} {definition}")
        kicked_columns = {row["name"] for row in connection.execute("PRAGMA table_info(kicked_domains)").fetchall()}
        for name, definition in {
            "deletion_status": "TEXT NOT NULL DEFAULT ''",
            "creation_date": "TEXT NOT NULL DEFAULT ''",
            "expiration_date": "TEXT NOT NULL DEFAULT ''",
            "wechat_status": "TEXT NOT NULL DEFAULT '否'",
            "qq_status": "TEXT NOT NULL DEFAULT '否'",
            "pollution_status": "TEXT NOT NULL DEFAULT '否'",
            "blocked_status": "TEXT NOT NULL DEFAULT '否'",
            "blacklist_status": "TEXT NOT NULL DEFAULT '否'",
            "filing_nature": "TEXT NOT NULL DEFAULT ''",
            "filing_info": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in kicked_columns:
                connection.execute(f"ALTER TABLE kicked_domains ADD COLUMN {name} {definition}")
        connection.execute(
            """
            UPDATE kicked_domains
            SET deletion_status = COALESCE((SELECT deletion_status FROM domain_checks WHERE domain = kicked_domains.domain), deletion_status),
                creation_date = COALESCE((SELECT creation_date FROM domain_checks WHERE domain = kicked_domains.domain), creation_date),
                expiration_date = COALESCE((SELECT expiration_date FROM domain_checks WHERE domain = kicked_domains.domain), expiration_date),
                wechat_status = COALESCE((SELECT wechat_status FROM domain_checks WHERE domain = kicked_domains.domain), wechat_status),
                qq_status = COALESCE((SELECT qq_status FROM domain_checks WHERE domain = kicked_domains.domain), qq_status),
                pollution_status = COALESCE((SELECT pollution_status FROM domain_checks WHERE domain = kicked_domains.domain), pollution_status),
                blocked_status = COALESCE((SELECT blocked_status FROM domain_checks WHERE domain = kicked_domains.domain), blocked_status),
                blacklist_status = COALESCE((SELECT blacklist_status FROM domain_checks WHERE domain = kicked_domains.domain), blacklist_status),
                filing_nature = COALESCE((SELECT filing_nature FROM domain_checks WHERE domain = kicked_domains.domain), filing_nature),
                filing_info = COALESCE((SELECT filing_info FROM domain_checks WHERE domain = kicked_domains.domain), filing_info)
            WHERE EXISTS (SELECT 1 FROM domain_checks WHERE domain = kicked_domains.domain)
            """
        )


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with _db_lock, get_connection() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    with _db_lock, get_connection() as connection:
        row = connection.execute(sql, params).fetchone()
        return dict(row) if row else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    with _db_lock, get_connection() as connection:
        cursor = connection.execute(sql, params)
        return int(cursor.lastrowid or 0)


def claim_schedule(schedule_id: int, run_key: str) -> bool:
    with _db_lock, get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE schedules
            SET last_run_key = ?, last_run_at = ?, updated_at = ?
            WHERE id = ? AND enabled = 1 AND last_run_key != ?
            """,
            (run_key, utc_now(), utc_now(), schedule_id, run_key),
        )
        connection.commit()
        return cursor.rowcount == 1


def soft_delete_source(source_id: int) -> int:
    # Collected domains and import runs keep a foreign key to the source, so the row is hidden instead of removed.
    now = utc_now()
    with _db_lock, get_connection() as connection:
        removed = connection.execute("DELETE FROM schedules WHERE source_id = ?", (source_id,)).rowcount
        connection.execute(
            """
            UPDATE sources
            SET source_key = source_key || ':deleted:' || id, enabled = 0, deleted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, source_id),
        )
        return removed


def upsert_domains(
    source: dict[str, Any], requested_date: str, domains: set[str]
) -> tuple[int, int]:
    inserted = 0
    updated = 0
    imported_at = utc_now()

    with _db_lock, get_connection() as connection:
        for domain in sorted(domains):
            existing = connection.execute(
                "SELECT joined_at, source_id, source_name, first_seen_at, query_time FROM domains WHERE domain = ?",
                (domain,),
            ).fetchone()

            if existing is None:
                connection.execute(
                    """
                    INSERT INTO domains (
                        domain, source_id, source_name, joined_at, first_seen_at, last_seen_at, imported_at, query_time,
                        label_kind, suffix, label_length
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                    """,
                    (
                        domain,
                        source["id"],
                        source["name"],
                        requested_date,
                        requested_date,
                        requested_date,
                        imported_at,
                        domain_label_kind(domain),
                        split_domain(domain)[1],
                        len(split_domain(domain)[0]),
                    ),
                )
                inserted += 1
                continue

            latest_date = max(existing["joined_at"], requested_date)
            query_time = "" if requested_date > existing["joined_at"] else existing["query_time"]
            effective_source_id = source["id"] if requested_date >= existing["joined_at"] else existing["source_id"]
            effective_source_name = source["name"] if requested_date >= existing["joined_at"] else existing["source_name"]
            changed = (
                latest_date != existing["joined_at"]
                or existing["source_id"] != effective_source_id
                or existing["source_name"] != effective_source_name
            )
            connection.execute(
                """
                UPDATE domains
                    SET source_id = ?, source_name = ?, joined_at = ?,
                    first_seen_at = ?, last_seen_at = ?, imported_at = ?, query_time = ?
                WHERE domain = ?
                """,
                (
                    effective_source_id,
                    effective_source_name,
                    latest_date,
                    min(existing["first_seen_at"], requested_date),
                    max(existing["joined_at"], requested_date),
                    imported_at,
                    query_time,
                    domain,
                ),
            )
            if changed:
                updated += 1

        connection.commit()

    return inserted, updated


def seed_gname_source(encrypt_cookie) -> None:
    existing = fetch_one("SELECT id FROM sources WHERE source_key = ? OR adapter = ?", ("gname", "gname"))
    if existing:
        return

    now = utc_now()
    execute(
        """
        INSERT INTO sources (
            source_key, name, adapter, base_url, page_path, download_template,
            parser, cookie_ciphertext, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gname",
            "GNAME",
            "gname",
            "https://www.gname.com",
            "/download/expired",
            "/request/downfile?xm=delete&date={date}&lx={format}",
            "line",
            encrypt_cookie(""),
            1,
            now,
            now,
        ),
    )


def seed_west_source(encrypt_cookie) -> None:
    existing = fetch_one("SELECT id FROM sources WHERE source_key = ? OR adapter = ?", ("west_cn", "west_cn"))
    if existing:
        return

    now = utc_now()
    execute(
        """
        INSERT INTO sources (
            source_key, name, adapter, base_url, page_path, download_template,
            parser, cookie_ciphertext, enabled, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "west_cn",
            "西部数码",
            "west_cn",
            "https://www.west.cn",
            "/booking/",
            "/services/grabnew/newlist.asp",
            "line",
            encrypt_cookie(""),
            1,
            now,
            now,
        ),
    )
