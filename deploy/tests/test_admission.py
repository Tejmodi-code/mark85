import asyncio
import unittest

from admission import AdaptiveAdmission


class AdaptiveAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_request_admits_without_queueing(self):
        gate = AdaptiveAdmission(4)
        self.assertTrue(await gate.acquire())
        self.assertEqual(gate.snapshot()["active"], 1)
        self.assertEqual(gate.snapshot()["queued"], 0)
        await gate.release(10)

    async def test_bounded_queue_and_capacity_rejection(self):
        gate = AdaptiveAdmission(1, queue_limit=1, wait_seconds=0.05)
        self.assertTrue(await gate.acquire())
        waiting = asyncio.create_task(gate.acquire())
        await asyncio.sleep(0.005)
        self.assertEqual(gate.snapshot()["queued"], 1)
        self.assertFalse(await gate.acquire())
        await gate.release(10)
        self.assertTrue(await waiting)
        await gate.release(10)
        self.assertEqual(gate.snapshot()["active"], 0)
        self.assertEqual(gate.snapshot()["rejected"], 1)

    async def test_adapts_down_on_overload_then_recovers_slowly(self):
        gate = AdaptiveAdmission(8, target_latency_ms=100)
        self.assertTrue(await gate.acquire())
        await gate.release(900)
        reduced = gate.current_limit
        self.assertLess(reduced, 8)
        for _ in range(32):
            self.assertTrue(await gate.acquire())
            await gate.release(2)
        self.assertGreater(gate.current_limit, reduced)
        self.assertLessEqual(gate.current_limit, gate.hard_limit)


if __name__ == "__main__":
    unittest.main()
