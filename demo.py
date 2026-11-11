"""Run a reproducible local rollout and record evidence of rollback."""
import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from gateway import Gateway, simulate


def demo(path):
    gateway = Gateway(path)
    before = gateway.metrics()
    for number in range(80):
        simulate(gateway, "support", f"demo-{number}", fail_canary=True)
        if gateway.metrics()["rollout"]["rolled_back"]:
            break
    after = gateway.metrics()
    if not after["rollout"]["rolled_back"]:
        raise RuntimeError("The demo did not trigger rollback.")
    follow_up = [simulate(gateway, "support", f"after-{number}") for number in range(5)]
    repeated = simulate(gateway, "support", "after-0")
    with ThreadPoolExecutor(max_workers=8) as workers:
        load = list(workers.map(lambda number: simulate(gateway, "research", f"load-{number}"), range(30)))
    return {"before": before, "after_rollback": after,
            "after_requests": [{"model": item["model"], "status": item["status"]} for item in follow_up],
            "duplicate_replayed": repeated["replayed"],
            "concurrent_research_statuses": dict(Counter(item["status"] for item in load)),
            "final": gateway.metrics()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/demo.json"))
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        result = demo(Path(folder) / "demo.sqlite3")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Canary traffic:", result["before"]["rollout"]["effective_canary_percent"], "->",
          result["after_rollback"]["rollout"]["effective_canary_percent"])
    print("Following requests:", [row["model"] for row in result["after_requests"]])
    print("Concurrent research requests:", result["concurrent_research_statuses"])
    print("Report:", args.output)


if __name__ == "__main__":
    main()
