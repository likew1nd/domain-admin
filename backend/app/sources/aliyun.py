from __future__ import annotations

from datetime import date

from .base import DownloadedFile, SourceAdapter, SourceError, build_url, ensure_authenticated, parse_domains, request_headers


class AliyunAdapter(SourceAdapter):
    """Export release-date searches through Aliyun's authenticated TXT endpoint."""

    def available_dates(self) -> list[str]:
        if not self.cookie.strip():
            raise SourceError("请先在阿里云数据源中配置登录 Cookie（CK）")
        # The search page has no downloadable date index; latest means today.
        return [date.today().isoformat()]

    def download(self, requested_date: str, file_format: str) -> DownloadedFile:
        if not self.cookie.strip():
            raise SourceError("请先在阿里云数据源中配置登录 Cookie（CK）")
        if file_format != "txt":
            raise SourceError("阿里云只支持 TXT 导出")
        url = build_url(self.source["base_url"], self.source["download_template"])
        suffixes = self.source.get("collection_suffixes") or ["com"]
        domains: set[str] = set()
        for suffix in suffixes:
            response = self.client.get(
                url,
                params={
                    "fetchSearchTotal": "true",
                    "originReleaseDate": requested_date,
                    "releaseDate": requested_date,
                    "quickSearch": '[""]',
                    "dataType": "",
                    "suffixes": suffix,
                },
                headers=request_headers(self.cookie, "https://mi.aliyun.com/"),
            )
            ensure_authenticated(response)
            content_type = response.headers.get("content-type", "").lower()
            if "json" in content_type or response.content.lstrip().startswith(b"{"):
                try:
                    message = str(response.json().get("message") or "源站未返回域名文件")
                except (ValueError, AttributeError):
                    raise SourceError("阿里云返回了无效的导出响应") from None
                if "登录" in message or "登陆" in message:
                    raise SourceError("阿里云 Cookie 已失效，请重新登录 mi.aliyun.com 后更新 Cookie")
                raise SourceError(f"阿里云导出失败：{message}")
            if any(kind in content_type for kind in ("html", "javascript", "ecmascript")):
                raise SourceError("阿里云返回网页或脚本，请检查登录 Cookie")
            exported = parse_domains(response.content, encoding="utf-8-sig")
            # ponytail: no automatic partitioning; reject a potentially truncated export.
            if len(exported) >= 10_000:
                raise SourceError(f"阿里云后缀 {suffix} 达到 10000 条导出上限，结果可能不完整；请在源站缩小筛选后使用 TXT 导入")
            domains.update(exported)
        content = ("\n".join(sorted(domains)) + "\n").encode("utf-8") if domains else b""
        return DownloadedFile(content, f"{self.source['source_key']}-{requested_date}.txt", "text/plain", "utf-8")
