"""Local rollout simulator with atomic admission and persistent rollback."""
import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).with_name("gateway.sqlite3")
PRICE = {"stable": 80, "canary": 40}  # Simulated micro-USD per admitted request.
LIMITS = {"support": (10_000, 200), "research": (3_000, 20)}


class Gateway:
    def __init__(self, path=DB_PATH):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rollout (
                    id INTEGER PRIMARY KEY CHECK(id=1), canary_percent INTEGER NOT NULL,
                    rolled_back INTEGER NOT NULL, trigger_request TEXT
                );
                INSERT OR IGNORE INTO rollout VALUES (1,25,0,NULL);
                CREATE TABLE IF NOT EXISTS requests (
                    id INTEGER PRIMARY KEY, app TEXT NOT NULL, request_id TEXT NOT NULL,
                    admitted_at REAL NOT NULL, day INTEGER NOT NULL, model TEXT NOT NULL,
                    status TEXT NOT NULL, cost INTEGER NOT NULL, latency_ms REAL,
                    UNIQUE(app, request_id)
                );
                CREATE INDEX IF NOT EXISTS by_app_day ON requests(app,day);
                CREATE INDEX IF NOT EXISTS by_app_time ON requests(app,admitted_at);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def admit(self, app, request_id):
        if app not in LIMITS:
            raise ValueError("Unknown application.")
        budget, rate_limit = LIMITS[app]
        now = time.time()
        day = int(now // 86400)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM requests WHERE app=? AND request_id=?",
                                  (app, request_id)).fetchone()
            if previous:
                return {"admitted": False, "replayed": True, **dict(previous)}
            state = db.execute("SELECT * FROM rollout WHERE id=1").fetchone()
            bucket = int.from_bytes(hashlib.sha256(f"{app}:{request_id}".encode()).digest()[:8], "big") % 100
            model = "canary" if not state["rolled_back"] and bucket < state["canary_percent"] else "stable"
            recent = db.execute("SELECT count(*) FROM requests WHERE app=? AND admitted_at>?",
                                (app, now - 60)).fetchone()[0]
            spent = db.execute("SELECT coalesce(sum(cost),0) FROM requests WHERE app=? AND day=?",
                               (app, day)).fetchone()[0]
            if recent >= rate_limit:
                return {"admitted": False, "status": "rate_limit_exceeded"}
            if spent + PRICE[model] > budget:
                return {"admitted": False, "status": "budget_exceeded"}
            row = db.execute("""INSERT INTO requests(app,request_id,admitted_at,day,model,status,cost)
                              VALUES (?,?,?,?,?,'running',?)""",
                             (app, request_id, now, day, model, PRICE[model])).lastrowid
            return {"admitted": True, "id": row, "model": model}

    def finish(self, row_id, success, latency_ms):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE requests SET status=?, latency_ms=? WHERE id=? AND status='running'",
                       ("ok" if success else "error", latency_ms, row_id))
            row = db.execute("SELECT * FROM requests WHERE id=?", (row_id,)).fetchone()
            if row["model"] == "canary":
                outcomes = db.execute("""SELECT status FROM requests WHERE model='canary'
                    AND status IN ('ok','error') ORDER BY id DESC LIMIT 10""").fetchall()
                if len(outcomes) >= 5 and sum(r["status"] == "error" for r in outcomes) / len(outcomes) >= 0.4:
                    db.execute("UPDATE rollout SET rolled_back=1,trigger_request=? WHERE id=1 AND rolled_back=0",
                               (f"{row['app']}:{row['request_id']}",))
            return dict(row)

    def metrics(self):
        with self.connect() as db:
            state = dict(db.execute("SELECT canary_percent,rolled_back,trigger_request FROM rollout").fetchone())
            models = db.execute("""SELECT model,status,count(*) AS requests,sum(cost) AS cost_micro_usd,
                    round(avg(latency_ms),3) AS mean_latency_ms FROM requests GROUP BY model,status
                    ORDER BY model,status""").fetchall()
            apps = db.execute("""SELECT app,count(*) AS requests,sum(cost) AS cost_micro_usd
                    FROM requests WHERE day=? GROUP BY app ORDER BY app""", (int(time.time() // 86400),)).fetchall()
        state["rolled_back"] = bool(state["rolled_back"])
        state["effective_canary_percent"] = 0 if state["rolled_back"] else state["canary_percent"]
        return {"mode": "simulation", "rollout": state, "models": [dict(r) for r in models],
                "applications_today": [dict(r) for r in apps]}


def simulate(gateway, app, request_id, fail_canary=False):
    admission = gateway.admit(app, request_id)
    if not admission["admitted"]:
        return admission
    model = admission["model"]
    # Fixed fixture latency illustrates the trade-off without waiting or calling a provider.
    latency = 180 if model == "stable" else 90
    result = gateway.finish(admission["id"], not (fail_canary and model == "canary"), latency)
    return {"admitted": True, **result}
