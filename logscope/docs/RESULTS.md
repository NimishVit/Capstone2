# Measured sample results — 2026-10-12

Defaults: seed 42, 100 trees, 3 simulated partitions, adaptive 5–300-second windows, 60% chronological training, 5% contamination.

| Input | Logs | Test windows | Precision | Recall | F1 | FPR |
|---|---:|---:|---:|---:|---:|---:|
| HDFS | 2000 | 108 | 0.5000 | 0.0870 | 0.1481 | 0.0235 |
| BGL | 2000 | 293 | 0.0833 | 0.0303 | 0.0444 | 0.0423 |
| OpenStack | 18434 | 146 | 0.0964 | 0.6667 | 0.1684 | 0.5597 |
| OpenStack_2k | 2000 | 15 | N/A | N/A | N/A | 0.0667 |

HDFS: official block annotations map to all 2,000 records. BGL: embedded native annotations. OpenStack: explicitly derived VM-association labels from the official failure-run annotations (not independently verified per-line fault ground truth). OpenStack_2k: exact subset of the normal1 collection, no positive examples; F1/recall/precision are withheld. All eligible records are retained; no test-set tuning was performed.

Scores are weak measured results, not fabricated benchmark values. The OpenStack failure scenario differs from the previous normal-only sample and must not be compared as if it were the same experiment. The full corpus/paper's protocol has not been replicated. HDFS time windows also differ from the paper's block-session unit.

The two-class sample F1 values remain far below the paper's reported contextual results, but different datasets/protocols prevent a direct algorithm superiority claim. Comparison JSON files contain all four window strategies.

Annotation mapping and canonical content checksums are recorded in `dist/data/label_manifest.json`. Source labels are distributed for the included data only. Rebuild script: `scripts/build_labeled_samples.py`.

Validation: 19 native tests passed; DOM checks cover all pages and both OpenStack presets. Browser visual QA remains unavailable.
