import asyncio
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.query_service import ProxyLeasePool


class ProxyConcurrencyTests(unittest.TestCase):
    def test_api_pool_consumes_a_fetched_batch_before_fetching_again(self):
        async def scenario():
            pool = ProxyLeasePool(
                {"mode": "api", "endpoint": "https://proxy.test", "max_requests": 2},
                task_id=0,
                max_slots=2,
            )
            pool._save_stats = lambda *args, **kwargs: None
            batches = 0

            async def fetch_batch():
                nonlocal batches
                batches += 1
                return [f"192.0.2.{index}:8080" for index in range(1, 5)]

            pool._fetch_batch = fetch_batch
            used = []
            for _ in range(4):
                proxy = await pool.acquire()
                used.append(proxy)
                await pool.report(proxy, True)
            return batches, used

        batches, used = asyncio.run(scenario())
        self.assertEqual(batches, 1)
        self.assertEqual(used, ["192.0.2.1:8080", "192.0.2.1:8080", "192.0.2.2:8080", "192.0.2.2:8080"])

    def test_api_pool_uses_one_request_per_ip_but_allows_multiple_ips(self):
        async def scenario():
            pool = ProxyLeasePool(
                {"mode": "api", "endpoint": "https://proxy.test", "max_requests": 10},
                task_id=0,
                max_slots=3,
            )
            pool._save_stats = lambda *args, **kwargs: None
            fetched = 0
            active = 0
            peak = 0
            active_by_proxy = {}
            per_proxy_peak = {}

            async def fetch_batch():
                nonlocal fetched
                fetched += 1
                return [f"192.0.2.{fetched}:8080"]

            pool._fetch_batch = fetch_batch

            async def worker():
                nonlocal active, peak
                proxy = await pool.acquire()
                active += 1
                peak = max(peak, active)
                active_by_proxy[proxy] = active_by_proxy.get(proxy, 0) + 1
                per_proxy_peak[proxy] = max(per_proxy_peak.get(proxy, 0), active_by_proxy[proxy])
                await asyncio.sleep(0.01)
                active_by_proxy[proxy] -= 1
                active -= 1
                await pool.report(proxy, True)

            await asyncio.gather(*(worker() for _ in range(3)))
            await asyncio.gather(*(worker() for _ in range(3)))
            return fetched, peak, max(per_proxy_peak.values())

        fetched, peak, per_proxy_peak = asyncio.run(scenario())
        self.assertEqual(fetched, 3)
        self.assertEqual(peak, 3)
        self.assertEqual(per_proxy_peak, 1)


if __name__ == "__main__":
    unittest.main()
