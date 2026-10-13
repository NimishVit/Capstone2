# LogScope — Adaptive Log Anomaly Detection

**Client evaluation release 1.1.0.** See [client handover](docs/CLIENT_HANDOVER.md) and [acceptance evidence](docs/ACCEPTANCE.md).

A capstone implementation for HDFS, BGL and OpenStack logs. It includes a real scikit-learn Isolation Forest pipeline, causal adaptive windowing, feature extraction, simulated partition scoring, labeled evaluation, Flask APIs, SQLite run history, an interactive dashboard, tests and Docker files.

## What is delivered

- **Hosted workbench:** the dashboard runs the same Python pipeline inside a browser worker using Pyodide. The first analysis downloads Python/scientific packages; it can take a minute. Uploaded logs stay on the device. Run history uses the browser's IndexedDB. A network connection is needed for initial runtime/package loading.
- **Flask source:** the dashboard automatically detects the Flask API. Runs, model bundles and incident states are stored in SQLite. Flask is tested locally through its test client; the published site is **not** a hosted Flask server.
- **Research scope:** batch analysis, chronological evaluation and local simulated partitions. This is **not** a multi-machine production cluster or a stateful live-ingestion service.
- **Actual sample results:** the included 2k LogHub samples are parsed and evaluated, not synthetic or hand-assigned dashboard figures. HDFS now includes matching official block labels. OpenStack defaults to the official 18,434-line failure run with derived VM-association labels; these evaluate association with the four annotated anomalous VMs, not independently verified per-line faults. The original OpenStack 2k normal-only sample remains selectable; its recall/F1 are intentionally unavailable.

## Quick start — Windows PowerShell

Install Python 3.12, unzip the project and open a terminal inside its folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000 . Use **Load sample**, choose the dataset, then **Run analysis**. Configure allows labels, fixed/adaptive windows and model settings. Window explorer includes evidence and CSV/JSON exports. Evaluation runs the fixed/adaptive ablation. Methodology includes model export after a fresh analysis.

Tests:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## Linux/macOS

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
.venv/bin/python -m pytest -q
```

## Docker

```bash
docker compose up --build -d
```

Open http://127.0.0.1:8000 . The compose file binds only to localhost. Data persists in the named `logscope_data` volume. Do not use `down -v` unless intentionally deleting that volume. Docker execution itself was not available for verification in the build environment; Flask and its APIs were tested directly.

To host Flask on your own Python-capable service, build the Dockerfile, expose container port 8000 behind TLS/authentication and mount persistent storage at `/app/runtime`. Set `LOGSCOPE_DB` if changing its database location. Gunicorn uses a single worker with two threads and a lock around model runs. This is an academic deployment, not a high-throughput production service.

`LOGSCOPE_API_TOKEN` optionally protects API routes (except health) using `Authorization: Bearer <token>`. Put public deployments behind identity-aware access control; do not expose an unprotected analysis API. The dashboard supports an optional bearer token through the **Server access** button; ordinary local use needs no token. Never commit tokens.

## CLI and saved model

```bash
python cli.py dist/data/BGL_2k.log --dataset BGL --compare --output results/bgl
```

This writes `result.json`, `model.joblib`, `parsed.csv` and optional `comparison.json`. CLI defaults match the UI: 100 trees, seed 42, 60% chronological training, 5% contamination, 3 simulated partitions, initial window 60s, adaptive limits 5–300s and target 50 logs.

Score without retraining:

```python
import sys, joblib
from pathlib import Path
sys.path.insert(0, 'dist')
from pipeline import predict_saved
bundle = joblib.load('results/bgl/model.joblib')  # only files you trust
scored = predict_saved(bundle, Path('new_bgl.log').read_text())
```

Joblib can execute code when loading; the web app never accepts uploaded model files. Saved inference uses the original models, calibration and node hashing. Adaptation restarts for each independent batch; it is not a streaming-state continuation.

## Repository map

| File | Responsibility |
|---|---|
| `dist/pipeline.py` | Parsers, normalization, adaptive windows, features, model, scoring, evaluation |
| `dist/worker.js` | Python execution in a browser worker |
| `dist/index.html`, `style.css`, `app.js` | Dashboard, charts, uploads, exports, local history |
| `dist/data/` | Official samples, OpenStack failure run, matching labels, provenance manifest, license and measured results |
| `app.py` | Flask APIs, SQLite history/models, incident lifecycle |
| `cli.py` | Repeatable experiments and artifacts |
| `tests/` | Parser, temporal, metrics, model and API tests |
| `docs/METHODOLOGY.md` | Exact algorithms, decisions, limitations and review notes |
| `docs/RESULTS.md` | Measured sample experiment and interpretation |
| `docs/API.md` | API requests and responses |
| `Dockerfile`, `compose.yaml` | Container deployment configuration |

## Data input

Raw formats: HDFS `<Date> <Time> <PID> <Level> <Component>: <Message>`; BGL `<Label> <Epoch> <Date> <Node> <Time> <RepeatNode> <Type> <Component> <Level> <Message>`; OpenStack optional log filename followed by `<Date> <Time> <PID> <Level> <Component> <Message>`.

CSV: `Timestamp,Level,Component,Message`, with optional `LineId,Node,Label`; `EventTemplate` or `Content` can substitute for Message. Date and Time can substitute for Timestamp. Unknown log levels are retained. Exact duplicates remain because repetition is signal. Input is sorted by timestamp. Invalid rows are reported, not silently dropped. UI limit is 15 MB; pipeline limit is 100,000 parsed rows. Full datasets should use a separately engineered batch/streaming pipeline, not claim this workbench supports arbitrary scale.

Built-in labels are auto-loaded only when the canonical content SHA-256 matches an entry in `dist/data/label_manifest.json`. Renaming an unrelated file never attaches sample labels. External label CSVs override auto-matching. The label sidecar checksum is also verified.

Optional labels CSV:

```csv
LineId,Label
1,0
2,1
```

HDFS block labels:

```csv
BlockId,Label
blk_-123,Anomaly
blk_456,Normal
```

HDFS logs with unknown/no block labels stay unknown. If multiple blocks appear, all must be labeled; any positive makes that log positive. Any positive log makes a window positive **only when all logs have known labels**. If any test-window label is unknown, classification metrics are withheld. Severity is never used as ground truth.

## Data provenance

Downloaded from the official LogPAI LogHub repository on 2026-10-09:

- https://github.com/logpai/loghub/blob/master/HDFS/HDFS_2k.log
- https://github.com/logpai/loghub/blob/master/BGL/BGL_2k.log
- https://github.com/logpai/loghub/blob/master/OpenStack/OpenStack_2k.log

These are research/academic datasets; the original notice is included in `dist/data/LOGHUB_LICENSE.txt`. Cite: Jieming Zhu, Shilin He, Pinjia He, Jinyang Liu, Michael R. Lyu. *Loghub: A Large Collection of System Log Datasets for AI-driven Log Analytics*. ISSRE, 2023. Repository: https://github.com/logpai/loghub .

Technical references: [IsolationForest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html), [Pyodide 0.27.7](https://pyodide.org/en/0.27.7/usage/index.html).

## Completion boundary

Implemented and tested: three parsers, regex templates, data quality, EDA, adaptive/fixed windows, 12 features, real Isolation Forest, score calibration/aggregation, label-aware evaluation, source/model export, history, incident triage and dashboard deployment.

Not claimed as complete: full-dataset paper replication, strong detection performance, matched session-level evaluation, multi-machine distribution, real-time ingestion agents, external notifications, browser visual QA, or live Python/Flask hosting. The source and Docker setup are ready for the latter, but an external hosting account/environment is required. No benchmark values or labels are invented.

## Evaluation update — 2026-10-12

Select HDFS or OpenStack and click **Load sample → Evaluation**. HDFS now shows official block-label-derived window metrics. OpenStack defaults to **Annotated failure run · 18,434 logs**; choose **Original 2k · normal only** in the new input selector to inspect its false-positive rate instead. Fresh browser and Flask runs auto-load the matching annotations.

OpenStack association rule: a log is positive if it contains one of the four VM UUIDs in the archive's `anomaly_labels.txt`; every other log is negative *for that association target*. This should not be described as independent line-level incident ground truth. An untagged fault can be missed by the label mapping. The dataset and rule are displayed directly above the metrics.

HDFS annotations: https://github.com/logpai/loglizer/blob/master/data/HDFS/anomaly_label.csv . OpenStack logs/annotations: https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1 . The original 2k OpenStack lines were all matched exactly to `openstack_normal1.log`.

Rebuild sidecars from downloaded official sources:

```bash
python scripts/build_labeled_samples.py --hdfs-labels /path/anomaly_label.csv --openstack-directory /path/extracted_OpenStack
```

No labels were derived from model predictions, ERROR severity or desired scores. Detection quality remains low and is not tuned or claimed to match the paper.
