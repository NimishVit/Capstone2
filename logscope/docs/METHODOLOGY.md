# Methodology and capstone review notes

## 1. Design

The shared project specification proposes an Isolation Forest simplification inspired by a distributed Drain/HDBSCAN paper. This implementation preserves that distinction. It is a working batch prototype with simulated partitions. Multiple datasets or a dashboard alone do not prove distributed or real-time operation.

## 2. Parsing and normalization

Each dataset has its own header parser. HDFS timestamps are `%y%m%d %H%M%S`. BGL uses the explicit Unix epoch and retains native anomaly labels (`-` means normal; other tokens mean anomalous). OpenStack accepts the filename-prefixed sample format and the same format without filename. BGL's epoch loses the subsecond precision present in its second timestamp field; the implementation consistently uses the epoch.

Normalization order: block IDs → UUIDs → IPv4-like tokens → long hex strings → standalone numbers → whitespace. These patterns are deterministic regex templates, **not Drain**. Regex can over-merge distinct numeric messages or under-merge variable words and paths; manual inspection remains necessary. Exact duplicates are retained. Timezone-free timestamps are treated as UTC consistently, not claimed to be the original system timezone.

## 3. Adaptive window algorithm

Windows are nonoverlapping, half-open `[start,end)`. The next observed event starts a new window when the prior one closes. Empty gaps are skipped and their duration recorded. The initial rate is `target_logs / initial_seconds`. After closing a nonempty window:

```
observed_rate = log_count / window_duration
rate = 0.3 * observed_rate + 0.7 * previous_rate
next_duration = clip(target_logs / max(rate, 1e-9), min_seconds, max_seconds)
```

Only the closed window informs the next window's duration. The final incomplete window retains its planned duration. This biases the last rate downward when the observation interval ends early; it is documented rather than hidden. Long idle gaps do not advance the EWMA through empty windows. Fixed baselines are also event-anchored nonempty windows, not wall-clock-aligned bins. These choices must be disclosed in experiments.

## 4. Features

| Feature | Exact definition |
|---|---|
| total_logs | Count in the window/partition |
| info_count | INFO count |
| warning_count | WARN + WARNING |
| error_count | ERROR + FATAL + CRITICAL + SEVERE + FAILURE |
| error_ratio | error_count / max(total_logs,1) |
| unique_templates | Distinct normalized messages |
| rare_template_ratio | Fraction of logs whose template training count ≤2, including unseen templates |
| template_diversity | unique_templates / max(total_logs,1) |
| avg_time_gap | Mean adjacent timestamp gap, or 0 with fewer than 2 logs |
| max_time_gap | Maximum adjacent gap, or 0 |
| log_rate | total_logs / planned duration |
| burst_score | log_rate / max(training mean rate,1e-9) |

Model features use per-partition training frequencies/rates. Dashboard feature vectors use the aggregate training baseline. They are explanatory summaries and not exact feature attributions of the partition models. All data-quality/template-count EDA is explicitly descriptive across the input, not fed into test normalization.

## 5. Isolation Forest and fusion

Chronologically earliest 60% of windows train the models; the rest are held out. At least 5 training and 3 test windows are required. Node keys: HDFS component; BGL node; OpenStack filename or component. SHA-256 first 8 hex digits modulo the selected partition count assign nodes stably. Sparse partitions with fewer than 5 training logs are reported and excluded. Active partition feature matrices include zero-log rows for aligned empty partitions.

Each active partition fits `IsolationForest(n_estimators=100, max_samples=min(256,n_train), contamination=.05, random_state=42+partition, n_jobs=1)`. Raw anomaly magnitude is `-score_samples`, so larger is more unusual. This project does not use sklearn's `predict` as its final aggregated decision.

Training empirical percentile calibration uses midpoint ranks: `(count(train_raw < raw) + count(train_raw <= raw)) / (2*n_train)`. Weighted global score is the mean of active partition percentiles weighted by their training log counts. The threshold is the `(1-contamination)` quantile of global training scores. A strict `score > threshold` comparison avoids marking a tied constant distribution anomalous. These are not calibrated probabilities. Calibration uses in-sample training scores and may be optimistic; a separate calibration interval is future work.

## 6. Severity and explanations

Normal means score does not exceed threshold. For anomalies, excess = `(score-threshold)/(1-threshold)`. Low <.15, medium <.4, high <.75, otherwise critical. These are project thresholds, not labels produced natively by Isolation Forest.

Explanations show up to 3 aggregate features with largest absolute standardized deviations from the training median, using training standard deviation. Deviations at least 1 are shown; a generic distribution message is used otherwise. Zero variance uses a small denominator. This is descriptive evidence, not causal diagnosis, SHAP or exact model contribution.

## 7. Evaluation

Only test windows count. Metrics include precision, recall, F1, accuracy, FPR and TP/TN/FP/FN. All test logs need valid labels. Any-positive window aggregation is used only after verifying completeness. No error-level heuristics are permitted as labels. Single-class evaluation is flagged. Undefined FPR has a null value. If no positive test labels exist, precision/recall/F1 are withheld (null) while accuracy, FPR and confusion counts remain. Otherwise zero-division precision/recall/F1 follow sklearn's `zero_division=0` behavior.

The exploratory ablation evaluates adaptive, fixed 60s, fixed 300s and fixed 600s. Each uses its own chronological window split; boundaries and label units differ. Therefore it is **not** a controlled comparison on identical evaluation objects. Sparse 2k samples distort interarrival rates and do not represent full contiguous streams.

## 8. Five-member handoff

| Member | Owns | Key files |
|---|---|---|
| 1 | Parsing, normalization, data quality, EDA | pipeline.py: parse_logs, normalize, structured_csv |
| 2 | Adaptive windows and features | segment, features |
| 3 | Training and model persistence | run_analysis, model_bytes, predict_saved |
| 4 | Partition fusion, metrics and ablations | partition, metrics, compare_modes |
| 5 | Flask, SQLite, dashboard and deployment | app.py, dist/app.js, worker.js, Dockerfile |

## 9. Hinglish review explanation

Pehle raw logs ke timestamp, level, component aur message alag karte hain. Variable values ko regex placeholders se replace karke templates banate hain. Pichhle closed window ki log rate se next window duration decide hoti hai. High activity par window chhota aur low activity par bada ho sakta hai, configured limits ke andar.

Har window/partition se numeric features nikalte hain. Early windows training ke liye aur later windows testing ke liye hain. Isolation Forest unusual feature patterns ko score deta hai. Partition scores ko training percentiles par normalize karke weighted average lete hain. Training threshold cross hone par anomaly flag lagta hai. Dashboard ke reasons statistical differences hain; actual incident cause prove nahi karte.

## 10. Next work requiring infrastructure/data

Full labeled datasets; controlled chronological cutoff and common evaluation units; HDFS block-session baseline; calibration holdout; scalability profiling; persistent streaming state; authenticated log agents; real distributed workers; alert delivery; hosted Flask with persistent volume. Detection quality requires systematic experiments, not changing displayed numbers.

## 11. Bundled annotation provenance (2026-10-12)

HDFS: every block ID observed in the original 2k sample matches the LogPAI/loglizer annotation table. 1,930 sample logs map to normal blocks and 70 to anomalous blocks. The benchmark remains time-window based, not an exact block-session replication.

OpenStack: the original 2k sample contains only records found exactly in the normal1 collection. The new default uses all 18,434 records from `openstack_abnormal.log`. The four anomalous VM IDs are supplied by the archive. Exact UUID membership gives 112 positive association records and 18,322 negative association records, then any-positive labels per window. This is a declared derived association target; nonmatching logs are not independently verified fault-free, and unrelated effects of a fault may lack the VM ID. All 18,434 records are retained and remain chronologically ordered for the 60/40 split. No timestamp shifting or balancing is applied.

`label_manifest.json` binds input and sidecars with canonical SHA-256 digests (UTF-8 BOM and newline normalization only). Source URLs, source hashes where available, mapping rule and label coverage travel with exported results. External annotations take precedence and are marked user-supplied.
