# Release 1.1.0 acceptance evidence

## Automated verification

- **32 Python tests passed:** parsing, adaptive/fixed windows, temporal boundaries, label-aware metrics, model round-trip, label provenance, API history/model retrieval, optional authentication, invalid payloads, concurrent-run rejection, incident save/conflict/audit, old-schema migration, and consistent SQLite WAL backup with overwrite protection.
- **Dashboard DOM workflow passed:** five pages render; HDFS evaluation shows measured counts; incident owner/notes/status survive reopening; resolved filter works; report escapes injected markup; CSV formula values are neutralized; settings lock during analysis; dataset sample selection works; OpenStack failure and normal-only reports behave correctly; history save/restore; fresh-run and failed-run UI paths. Runtime responses in the UI test are fixtures, not model execution.
- JavaScript syntax validation passed. The client report is self-contained HTML with print styles; it is not a pre-generated PDF.

Reproduce:

```bash
python -m pytest -q
npm ci
npm run test:ui
node --check dist/app.js
```

## Limits of verification

DOM tests use jsdom, not a visual browser. Actual browser layout/accessibility and PDF pagination have not been visually verified in this environment. Docker image execution and a remote Flask installation have not been verified. The Python tests execute the real scikit-learn pipeline; the hosted browser runtime uses the shared Python source. The release does not claim a full end-to-end browser runtime test for these UI changes.

## Client acceptance before operational use

1. Review the three dataset workflows with representative client logs and independently verified labels.
2. Agree anomaly recall, precision, latency, volume and retention targets. Current sample scores do not establish suitability for unattended alerting.
3. If shared server operation is required, deploy and test Flask behind TLS/organization identity controls, back up its persistent database, and test recovery in that target environment.
4. Confirm data licensing and client log handling requirements, then record formal acceptance of the agreed scope.

The delivered hosted application is the on-device client evaluation version. Continuous ingestion, notification delivery, user roles and multi-machine execution are not implemented features.
