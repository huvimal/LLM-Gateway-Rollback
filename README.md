# LLM Gateway & Rollback

Local simulator for per-application admission limits and an LLM canary rollout.
SQLite persists request records, admission charges, and the rollback decision.
Provider responses, costs and latencies are fixtures.

## Run the demo on Windows

```powershell
python demo.py
Get-Content reports/demo.json -Encoding utf8
python -m unittest discover -s tests -v
```

The demo uses a fresh temporary database. It injects failures into canary
requests, records the rollback, sends five more requests, and sends 30
research requests concurrently. The expected output is 25% to 0% canary
traffic, five subsequent stable routes, and 20 admitted research requests
with 10 rejected by the rate limit. Replaying the same application and
request ID returns the original record without another charge.

## Try the local API

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8508
```

Open http://127.0.0.1:8508/docs. In POST /simulate, send:

```json
{"app":"support","request_id":"manual-1","fail_canary":false}
```

Send it twice and compare the responses. The second response includes
`replayed: true`. GET /metrics shows requests and simulated usage by
application and model, plus rollout status. The API database is separate
from the temporary demo database. Run the CLI demo to reproduce rollback
without changing the API database.

## Routing and budget policy

The stable and canary models cost 80 and 40 simulated micro-USD per admitted
request. Their fixture latencies are 180 and 90 ms. Admission atomically
checks a UTC daily budget and the last 60 seconds of accepted traffic.
The hash of application and request ID determines the initial canary cohort.
After at least five completed canary requests, a 40% or higher error rate
in the latest ten canary outcomes by request ID disables canary routing
for future admissions. Requests already admitted retain their route.

| App | Daily budget, simulated micro-USD | Requests per 60 seconds |
| --- | ---: | ---: |
| support | 10000 | 200 |
| research | 3000 | 20 |

Use the recorded results to explain why the overall mean latency changes
as the traffic mix shifts. The supplied latencies are fixed scenario inputs.
This prototype has local simulated applications and no external model calls.
