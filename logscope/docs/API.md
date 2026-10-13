# Flask API

Base URL locally: http://127.0.0.1:5000 . JSON requests use `Content-Type: application/json`. Optional API token uses an Authorization Bearer header. Maximum HTTP body is 20 MB.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/health` | Runtime versions and readiness |
| POST | `/api/analyze` | Train, score, evaluate, store result and model |
| POST | `/api/compare` | Adaptive and 1/5/10-minute baseline ablation |
| POST | `/api/parse` | Download structured log CSV |
| GET | `/api/runs` | Latest 50 stored run metadata entries |
| GET | `/api/runs/<id>` | Full stored result |
| GET | `/api/runs/<id>/model` | Trained joblib bundle |
| GET | `/api/runs/<id>/incidents` | Saved incident statuses |
| POST | `/api/runs/<id>/incidents/<window_id>` | Set open / acknowledged / resolved |

Example analyze body:

```json
{"text":"<raw file contents>","labels":"","options":{"dataset":"HDFS","mode":"adaptive","fixed_seconds":60,"min_seconds":5,"max_seconds":300,"target_logs":50,"train_fraction":0.6,"contamination":0.05,"trees":100,"partitions":3,"seed":42}}
```

`labels` is optional CSV content. Results include `run_id`, `quality`, `summary`, `windows`, `metrics`, `timing`, `partitions`, `warnings`, `eda` and runtime `versions`. This is synchronous bounded batch analysis. There is no streaming endpoint or job queue.

Parse body: `{"text":"...","dataset":"HDFS","labels":""}`. Incident body: `{"status":"acknowledged"}`. Unknown incident IDs return 404; invalid configuration/input returns 400; oversized bodies return 413; protected API requests without a token return 401.

## Version 1.1 additions

`GET /api/health` includes `release` and `auth_required`. Responses include `X-Request-ID`. Analysis accepts an optional `input_name` (up to 255 characters).

`GET /api/runs/<id>/triage` returns a window-ID map of `status`, `owner`, `notes`, `updated`, `version`. `POST /api/runs/<id>/incidents/<window>` accepts those first three fields and optional `expected_version`; stale versions return 409. Notes allow 2,000 characters and owners 100. The UI supplies the version on every save. `GET /api/runs/<id>/audit` returns the latest 100 changes, including before/after values and request IDs. Owner is free text; the shared token does not establish actor identity.

Concurrent analysis requests return 409. Invalid payloads return 400; unsupported JSON media type returns 415; oversized requests return 413. Clients should retain input and retry after correcting the error.
