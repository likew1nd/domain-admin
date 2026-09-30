from __future__ import annotations

from datetime import date
from typing import Any

from .base import DownloadedFile, SourceAdapter, SourceError, build_url, ensure_authenticated, request_headers


class WestCnAdapter(SourceAdapter):
    """西部数码过期域名搜索导出适配器。"""

    export_path = "/services/grabnew/newlist.asp"

    def available_dates(self) -> list[str]:
        if not self.cookie.strip():
            raise SourceError("请先配置西部数码登录 Cookie")
        page_path = self.source.get("page_path") or "/booking/"
        url = build_url(self.source["base_url"], page_path)
        response = self.client.get(url, headers=request_headers(self.cookie, url))
        ensure_authenticated(response)
        return [date.today().isoformat()]

    def download_suffix(self, suffix: str, file_format: str = "txt") -> DownloadedFile:
        if not self.cookie.strip():
            raise SourceError("请先配置西部数码登录 Cookie")
        if file_format != "txt":
            raise SourceError("西部数码当前只支持导出 TXT")
        path = self.source.get("download_template") or self.export_path
        if "{suffix}" in path:
            path = path.format(suffix=suffix, date=date.today().isoformat(), format=file_format)
        url = build_url(self.source["base_url"], path)
        params: dict[str, Any] = {
            "mode": "wedel",
            "domjsbh": "1",
            "domkey": "",
            "arrdomext": suffix,
            # 西部数码默认会带上“所有待删除”条件；这里明确清空所有
            # 过滤项，只保留当前后缀，避免采集结果被站点默认条件截断。
            "domunkey1": "",
            "domlen1": "",
            "domlen2": "",
            "topmoney": "",
            "topmoneymax": "",
            "price": "",
            "pricemax": "",
            "expday": "",
            "expdaymax": "",
            "othercn": "",
            "domclass": "",
            "domleiab": "",
            "deldate": "",
            "regyear": "",
            "regyearmax": "",
            "freeyd": "",
            "deltype": "",
            "ordby": "",
            "ordtp": "",
            "sogoupr": "",
            "sogouprmax": "",
            "baidupr": "",
            "baiduprmax": "",
            "sgsoulu": "",
            "sgsoulumax": "",
            "bingsoulu": "",
            "bingsoulumax": "",
            "bdsoulu": "",
            "bdsoulumax": "",
            "bdfanlian": "",
            "bdfanlianmax": "",
            "wailian": "",
            "wailianmax": "",
            "sitehis": "",
            "sitehismax": "",
            "siteinfohis": "",
            "siteinfohismax": "",
            "bdrenzheng": "",
            "ishui": "",
            "sitechinese": "",
            "wxcheck": "",
            "qqcheck": "",
            "wallcheck": "",
            "bdpingjia": "",
            "ismiiban": "",
            "guonei": "",
            "linktype": "",
            "isqy": "",
            "viewcount": "",
            "sitetitle": "",
            "icpwzmc": "",
            "haveuser": "",
            "isbid": "",
            "datasource": "",
            "reward": "",
            "jing_status": "",
            "sellerid": "",
            "excludeid": "",
            "isbao": "",
            "jingjia_status": "",
            "dropzoom": "",
            "ispremium": "",
            "pageno": "1",
            "pagesize": "150000",
            "isexport": "1",
            "exportFileType": "txt",
        }
        response = self.client.post(url, data=params, headers=request_headers(self.cookie, url))
        ensure_authenticated(response)
        content_type = response.headers.get("content-type", "")
        if response.content.lstrip().startswith((b"<", b"\xef\xbb\xbf")) and "text/html" in content_type.lower():
            raise SourceError("西部数码没有返回导出文件，请检查登录 Cookie 或筛选条件")
        filename = f"west-{date.today().isoformat()}-{suffix}.txt"
        return DownloadedFile(response.content, filename, content_type)

    def download(self, requested_date: str, file_format: str) -> DownloadedFile:
        suffix = str(self.source.get("default_suffix") or "com").strip().lower().lstrip(".")
        return self.download_suffix(suffix, file_format)
