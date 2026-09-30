from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urljoin

import httpx


DATE_RE = re.compile(r"(?:data-sj=[\"']|date=)(\d{4}-\d{2}-\d{2})")
DOMAIN_RE = re.compile(
    r"(?i)(?<![a-z0-9-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?![a-z0-9-])"
)


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedFile:
    content: bytes
    filename: str
    content_type: str


def build_url(base_url: str, path: str) -> str:
    return urljoin(f"{base_url.rstrip('/')}/", path.lstrip('/'))


def request_headers(cookie: str, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DomainCollector/1.0)",
        "Accept": "text/html, text/plain, text/csv, application/octet-stream, */*",
    }
    if cookie.strip():
        headers["Cookie"] = cookie.strip()
    if referer:
        headers["Referer"] = referer
    return headers


def ensure_authenticated(response: httpx.Response) -> None:
    body = response.content[:200_000].lower()
    if response.status_code in {401, 403}:
        raise SourceError(f"源站拒绝访问（HTTP {response.status_code}）")
    if b"<title>account login" in body or b"<title>login" in body:
        raise SourceError("Cookie 已失效或未配置，请重新登录源站后更新 Cookie")
    try:
        login_text = response.content[:200_000].decode("gb18030", errors="ignore")
    except UnicodeDecodeError:
        login_text = ""
    if any(phrase in login_text for phrase in ("请登录", "请登陆", "登录后再操作", "登陆后再操作")):
        raise SourceError("Cookie 已失效或未配置，请重新登录源站后更新 Cookie")
    if response.status_code >= 400:
        raise SourceError(f"源站请求失败（HTTP {response.status_code}）")


def parse_available_dates(html: str) -> list[str]:
    dates = sorted(set(DATE_RE.findall(html)), reverse=True)
    return [item for item in dates if item <= date.today().isoformat()]


def extract_filename(response: httpx.Response, fallback: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8''|\"?)([^;\"]+)", disposition, re.I)
    return match.group(1).strip() if match else fallback


def normalize_domain(value: str) -> str | None:
    candidate = value.strip().strip('"\'').lower().rstrip(".")
    candidate = candidate.removeprefix("http://").removeprefix("https://").split("/", 1)[0]
    match = DOMAIN_RE.fullmatch(candidate)
    return match.group(0) if match else None


def parse_domains(content: bytes, file_format: str = "txt", parser: str = "line") -> set[str]:
    raw = content
    if raw[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            files = [item for item in archive.infolist() if not item.is_dir()]
            if not files:
                return set()
            raw = archive.read(files[0])

    text = raw.decode("utf-8-sig", errors="replace")
    values: list[str] = []
    if file_format.lower() == "csv" or parser == "csv":
        try:
            values = [cell for row in csv.reader(io.StringIO(text)) for cell in row[:3]]
        except csv.Error as exc:
            raise SourceError(f"CSV 文件解析失败：{exc}") from exc
    else:
        values = re.split(r"[\r\n,;\t ]+", text)

    return {domain for value in values if (domain := normalize_domain(value))}


class SourceAdapter:
    def __init__(self, source: dict[str, Any], cookie: str):
        self.source = source
        self.cookie = cookie
        self.client = httpx.Client(follow_redirects=True, timeout=45)

    def close(self) -> None:
        self.client.close()

    def available_dates(self) -> list[str]:
        page_path = self.source.get("page_path", "")
        if not page_path:
            return []
        url = build_url(self.source["base_url"], page_path)
        response = self.client.get(url, headers=request_headers(self.cookie, url))
        ensure_authenticated(response)
        return parse_available_dates(response.text)

    def download(self, requested_date: str, file_format: str) -> DownloadedFile:
        path = self.source["download_template"].format(date=requested_date, format=file_format)
        url = build_url(self.source["base_url"], path)
        response = self.client.get(url, headers=request_headers(self.cookie, self.source.get("base_url")))
        ensure_authenticated(response)
        filename = extract_filename(response, f"{self.source['source_key']}-{requested_date}.{file_format}")
        return DownloadedFile(response.content, filename, response.headers.get("content-type", ""))

    def parse(self, downloaded: DownloadedFile, file_format: str) -> set[str]:
        return parse_domains(downloaded.content, file_format, self.source.get("parser", "line"))
