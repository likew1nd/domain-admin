from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import time
import uuid
from typing import Any

import aiohttp
import numpy as np
from PIL import Image


class IcpQueryError(RuntimeError):
    pass


class IcpClient:
    """Small in-process adapter for ICP_Query's website query flow."""

    AUTH = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/auth"
    CAPTCHA = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/image/getCheckImagePoint"
    CHECK_CAPTCHA = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/image/checkImage"
    QUERY = "https://hlwicpfwc.miit.gov.cn/icpproject_query/api/icpAbbreviateInfo/queryByCondition/"

    def __init__(self, timeout: float = 35) -> None:
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    @staticmethod
    def _proxy(proxy: str | None) -> str | None:
        if not proxy:
            return None
        return proxy if "://" in proxy else f"http://{proxy}"

    async def _post(self, url: str, *, data: Any = None, headers: dict[str, str], proxy: str | None) -> str:
        connector = aiohttp.TCPConnector(ssl=False, limit=10)
        async with aiohttp.ClientSession(timeout=self.timeout, connector=connector) as session:
            async with session.post(url, data=data, headers=headers, proxy=self._proxy(proxy)) as response:
                text = await response.text()
                if response.status >= 400:
                    raise IcpQueryError(f"ICP 查询接口 HTTP {response.status}")
                return text

    async def _token(self, proxy: str | None) -> tuple[str, dict[str, str]]:
        timestamp = round(time.time() * 1000)
        auth_key = hashlib.md5(f"testtest{timestamp}".encode()).hexdigest()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Origin": "https://beian.miit.gov.cn",
            "Referer": "https://beian.miit.gov.cn/",
            "Cookie": f"__jsluid_s={uuid.uuid4().hex}",
            "Accept": "application/json, text/plain, */*",
        }
        text = await self._post(
            self.AUTH,
            data={"authKey": auth_key, "timeStamp": timestamp},
            headers=headers,
            proxy=proxy,
        )
        try:
            payload = __import__("json").loads(text)
            token = payload["params"]["bussiness"]
        except (KeyError, TypeError, ValueError) as exc:
            raise IcpQueryError("ICP 查询令牌获取失败") from exc
        return token, headers

    @staticmethod
    def _offset(small_image: str, big_image: str) -> int:
        small = base64.b64decode(small_image)
        big = base64.b64decode(big_image)
        with Image.open(io.BytesIO(small)) as image:
            sw, sh = image.size
        pixels = np.asarray(Image.open(io.BytesIO(big)).convert("RGB"))[::2, ::2]
        height, width = pixels.shape[:2]
        minimum = max(1, int(min(sw, sh) * 0.25))
        skip_left = sw // 4
        good_enough = (minimum * minimum * 3) // 2
        quantized = pixels.astype(np.int32) & ~3
        colors = quantized[:, :, 0] + quantized[:, :, 1] * 256 + quantized[:, :, 2] * 65536
        unique, counts = np.unique(colors.ravel(), return_counts=True)
        top_indices = np.argpartition(counts, max(-3, -len(counts)))[-3:]
        best_x, best_area = 0, 0
        column_run = np.empty((height, width), dtype=np.int32)
        for index in top_indices:
            mask = colors == unique[index]
            column_run[0] = mask[0]
            for y in range(1, height):
                column_run[y] = (column_run[y - 1] + 1) * mask[y]
            for y in range(minimum, height):
                row = column_run[y]
                x = skip_left
                while x < width:
                    if row[x] < minimum:
                        x += 1
                        continue
                    start = x
                    while x < width and row[x] >= minimum:
                        x += 1
                    run_width = x - start
                    run_height = int(row[start])
                    if run_height > 0:
                        ratio = run_width / run_height
                        area = run_width * run_height
                        if 0.7 < ratio < 1.4 and area > best_area:
                            best_area, best_x = area, start
                            if area >= good_enough:
                                return best_x * 2
        if not best_area:
            raise IcpQueryError("验证码缺口识别失败")
        return best_x * 2

    async def _captcha(self, proxy: str | None) -> tuple[str, str, str, dict[str, str]]:
        token, headers = await self._token(proxy)
        client_uid = "point-" + str(uuid.uuid4())
        headers = {**headers, "Content-Type": "application/json", "token": token}
        text = await self._post(
            self.CAPTCHA,
            data=__import__("json").dumps({"clientUid": client_uid}),
            headers=headers,
            proxy=proxy,
        )
        try:
            payload = __import__("json").loads(text)["params"]
            captcha_id = payload["uuid"]
            offset = self._offset(payload["smallImage"], payload["bigImage"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IcpQueryError("验证码图片获取失败") from exc
        check_data = __import__("json").dumps({"key": captcha_id, "value": str(offset)})
        result_text = await self._post(
            self.CHECK_CAPTCHA,
            data=check_data,
            headers=headers,
            proxy=proxy,
        )
        try:
            result = __import__("json").loads(result_text)
            if not result.get("success"):
                raise IcpQueryError("验证码识别失败")
            return captcha_id, token, result["params"], headers
        except (KeyError, TypeError, ValueError) as exc:
            raise IcpQueryError("验证码校验失败") from exc

    async def query(self, domain: str, proxy: str | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                captcha_id, token, sign, headers = await self._captcha(proxy)
                payload = {"pageNum": "", "pageSize": "", "unitName": domain, "serviceType": 1}
                headers = {
                    **headers,
                    "Content-Length": str(len(__import__("json").dumps(payload, ensure_ascii=False).encode())),
                    "uuid": captcha_id,
                    "token": token,
                    "sign": sign,
                }
                text = await self._post(
                    self.QUERY,
                    data=__import__("json").dumps(payload, ensure_ascii=False),
                    headers=headers,
                    proxy=proxy,
                )
                data = __import__("json").loads(text)
                if not isinstance(data, dict):
                    raise IcpQueryError("备案接口返回格式无效")
                params = data.get("params")
                if not isinstance(params, dict) or "list" not in params:
                    raise IcpQueryError("备案接口未返回确定结果")
                records = params.get("list") or []
                if not isinstance(records, list):
                    raise IcpQueryError("备案接口返回记录格式无效")
                return {"found": bool(records), "queried": True, "data": data, "records": records}
            except (aiohttp.ClientError, asyncio.TimeoutError, IcpQueryError, ValueError) as exc:
                last_error = exc
                await asyncio.sleep(0.3)
        raise IcpQueryError(str(last_error or "ICP 查询失败"))
