# LogScope 1.1.0 — client handover

## Delivered scope
Batch log analysis for HDFS, BGL and OpenStack; adaptive/fixed windows; real Isolation Forest; held-out evaluation; evidence inspection; incident owners, notes and status; printable HTML reports; CSV/JSON/model exports; run history; Flask API and Docker deployment source.

The published application executes Python on the user's device. Logs are not uploaded to a LogScope backend in this mode. Runtime packages are fetched from external CDNs. Browser history and triage are device-local and can be deleted by browser cleanup. Export important results and incident CSVs. There is no cross-device sync or individual user identity.

The included Flask alternative stores runs, model bundles and triage centrally in SQLite. It is not running on the published static site. Use a single Gunicorn worker (the included default) because the analysis lock is process-local. The API rejects simultaneous analysis with HTTP 409. Long-running jobs are synchronous; there is no durable task queue.

## Operator deployment
1. Install Docker with Compose or Python 3.12 and the pinned requirements.
2. Copy `.env.example` to `.env`, set `LOGSCOPE_API_TOKEN` for a shared deployment, and keep this file private.
3. Run `docker compose up --build -d`. The service binds to localhost port 8000 with a persistent named database volume.
4. Put a TLS reverse proxy and organization access control in front of a shared service. The supplied bearer token is a shared credential, not per-user authentication or role-based access control.
5. Open the dashboard and use **Server access** to enter the token. Verify `/api/health`, then analyze a sample, restore history, save an incident and download a report.
6. Set monitoring, backup retention and storage quotas appropriate to the client. Do not scale to multiple workers without replacing the process-local model lock and benchmarking concurrency.

## Backup and recovery
For a native installation, create a consistent online SQLite snapshot:

```bash
python scripts/backup_database.py runtime/runs.sqlite3 backups/runs-2026-10-13.sqlite3
```

The helper includes committed WAL transactions, checks database integrity, and refuses to overwrite an existing backup. For Docker, run the helper inside the container to a mounted backup location or copy its completed snapshot out with `docker compose cp`. Do not copy only a live database file while WAL writes are active.

To restore: stop the service, retain a rollback copy of the entire runtime directory, replace the database with the verified snapshot, remove stale WAL/SHM files belonging to the stopped database, preserve ownership, and restart. Confirm a saved run and incident before reopening access. Never delete the Docker volume during routine upgrades.

## Release and rollback
The distribution includes the source ZIP, requirements, tests, data provenance, API reference and this guide. Keep the previous source package and a pre-upgrade database backup. Version 1.1 adds incident columns and an audit table; existing incident statuses are retained. Rollback must use the matching source and database snapshot. Secrets, raw client inputs and runtime databases are excluded from source distributions.

## Acceptance boundary
This is a client evaluation release, not an approved production incident detector. Detection scores are weak on the included samples; inspect `docs/RESULTS.md`. OpenStack labels describe association with annotated VMs, not verified per-line faults. HDFS metrics use time windows derived from block labels, not a full session benchmark. Full-data validation, agreed accuracy/latency targets, client infrastructure, identity integration, streaming ingestion and external notifications remain outside this delivery. The owner and client must agree these requirements before an operational rollout.

See `docs/ACCEPTANCE.md` for verification evidence and limitations.
