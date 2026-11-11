import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from demo import demo
from gateway import Gateway, simulate


class GatewayTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test.sqlite3"

    def test_rollback_persists_and_stops_new_canary_admissions(self):
        result = demo(self.path)
        self.assertEqual(result["before"]["rollout"]["effective_canary_percent"], 25)
        self.assertEqual(result["after_rollback"]["rollout"]["effective_canary_percent"], 0)
        self.assertEqual({row["model"] for row in result["after_requests"]}, {"stable"})
        self.assertEqual(result["concurrent_research_statuses"], {"ok": 20, "rate_limit_exceeded": 10})
        self.assertTrue(result["duplicate_replayed"])
        restarted = Gateway(self.path)
        self.assertTrue(restarted.metrics()["rollout"]["rolled_back"])
        self.assertEqual(simulate(restarted, "support", "after-restart")["model"], "stable")

    def test_atomic_budget_and_duplicate_admission(self):
        gateway = Gateway(self.path)
        with patch.dict("gateway.LIMITS", {"support": (80, 200)}):
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(lambda number: simulate(gateway, "support", f"budget-{number}"), range(20)))
            accepted = [item for item in results if item["admitted"]]
            self.assertGreaterEqual(len(accepted), 1)
            self.assertLessEqual(sum(item["cost"] for item in accepted), 80)
            self.assertTrue(any(item["status"] == "budget_exceeded" for item in results))
            replay = simulate(gateway, "support", accepted[0]["request_id"])
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["cost"], accepted[0]["cost"])
            spend = gateway.metrics()["applications_today"][0]["cost_micro_usd"]
            self.assertEqual(spend, sum(item["cost"] for item in accepted))
