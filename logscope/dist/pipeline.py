"""LogScope: one analysis implementation for CPython/Flask and Pyodide.

Window-level chronological evaluation. Never infer ground truth from severity.
Partition execution is a local simulation, not a distributed cluster.
"""
import csv
import hashlib
import io
import json
import math
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

FEATURES = ['total_logs', 'info_count', 'warning_count', 'error_count',
            'error_ratio', 'unique_templates', 'rare_template_ratio',
            'template_diversity', 'avg_time_gap', 'max_time_gap', 'log_rate', 'burst_score']
LAST_MODEL = None


def canonical_sha256(text):
    return hashlib.sha256(text.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n').encode()).hexdigest()


def resolve_labels(text, dataset, labels_text=''):
    """Bind bundled labels only to exact known input content, never by filename."""
    if labels_text.strip():
        return labels_text, {'label_kind': 'user_supplied', 'note': 'User-provided labels; provenance has not been independently verified by this application.'}
    data = Path(__file__).parent / 'data'
    manifest_path = data / 'label_manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        digest = canonical_sha256(text)
        for profile in manifest['profiles']:
            if profile['dataset'] == dataset and profile['input_sha256'] == digest:
                label_path = data / profile['labels_path']
                if not label_path.exists():
                    raise ValueError('Bundled annotation file is missing. Reload the application or restore the data directory.')
                labels = label_path.read_text()
                if canonical_sha256(labels) != profile['labels_sha256']:
                    raise ValueError('Bundled annotation checksum mismatch; evaluation stopped.')
                return labels, profile
    kind = 'embedded_bgl' if dataset == 'BGL' else 'unavailable'
    return '', {'label_kind': kind, 'note': 'Native BGL labels: dash = normal, other label tokens = anomalous.' if dataset == 'BGL' else 'No verified labels for this input. Supply a matching label CSV.'}


@dataclass
class Config:
    dataset: str = 'HDFS'
    mode: str = 'adaptive'
    fixed_seconds: float = 60
    min_seconds: float = 5
    max_seconds: float = 300
    target_logs: int = 50
    train_fraction: float = .6
    contamination: float = .05
    trees: int = 100
    partitions: int = 3
    seed: int = 42

    def validate(self):
        for name in ('fixed_seconds','min_seconds','max_seconds','target_logs','train_fraction','contamination','trees','partitions','seed'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number.')
        for name in ('target_logs','trees','partitions','seed'):
            if not isinstance(getattr(self,name),int):
                raise ValueError(f'{name} must be a whole number.')
        if not 0 <= self.seed <= 4294967287:
            raise ValueError('Seed must be between 0 and 4294967287.')
        if self.dataset not in ('HDFS', 'BGL', 'OpenStack'):
            raise ValueError('Select HDFS, BGL or OpenStack.')
        if self.mode not in ('adaptive', 'fixed'):
            raise ValueError('Window mode must be adaptive or fixed.')
        if not (1 <= self.min_seconds <= self.max_seconds <= 86400):
            raise ValueError('Window bounds must satisfy 1 ≤ minimum ≤ maximum ≤ 86400 seconds.')
        if not (1 <= self.fixed_seconds <= 86400 and 5 <= self.target_logs <= 5000):
            raise ValueError('Fixed duration must be 1–86400 seconds; target size must be 5–5000.')
        if not (.3 <= self.train_fraction <= .8 and .001 <= self.contamination <= .3):
            raise ValueError('Train fraction must be 0.3–0.8 and contamination 0.001–0.3.')
        if not (20 <= self.trees <= 300 and 1 <= self.partitions <= 8):
            raise ValueError('Use 20–300 trees and 1–8 partitions.')


def normalize(message):
    message = re.sub(r'blk_-?\d+', '<BLOCK>', message)
    message = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b', '<UUID>', message)
    message = re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', '<IP>', message)
    message = re.sub(r'\b(?:0x)?[0-9a-fA-F]{8,}\b', '<HEX>', message)
    message = re.sub(r'(?<!\w)-?\d+(?:\.\d+)?(?!\w)', '<NUM>', message)
    return ' '.join(message.split())


def timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=timezone.utc).timestamp() if not re.search(r'[+-]\d\d:\d\d$', value) else datetime.fromisoformat(value).timestamp()
    except ValueError:
        for fmt in ('%y%m%d %H%M%S', '%Y-%m-%d-%H.%M.%S.%f'):
            try:
                return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        raise ValueError('Invalid timestamp: ' + value[:80])


def label_value(value):
    if value is None or str(value).strip() == '':
        return None
    v = str(value).strip().lower()
    if v in ('1', 'anomaly', 'anomalous', 'abnormal', 'true'):
        return 1
    if v in ('0', 'normal', 'false', '-'):
        return 0
    raise ValueError('Labels must be 0/1 or Normal/Anomaly; unknown label: ' + v[:50])


def parse_logs(text, dataset, labels_text=''):
    rows, rejects = [], []
    lines = text.lstrip('\ufeff').splitlines()
    is_csv = bool(lines and ',' in lines[0] and re.search(r'Timestamp|Message|EventTemplate', lines[0], re.I))
    inputs = enumerate(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))), 2) if is_csv else enumerate(lines, 1)
    total = 0
    for line_number, line in inputs:
        if not is_csv and not line.strip():
            continue
        total += 1
        try:
            label, node, block_ids = None, '', []
            if is_csv:
                d = {k.lower(): v for k, v in line.items() if k}
                ts = timestamp(d.get('timestamp') or d['date'] + ' ' + d['time'])
                level, component = d.get('level', 'UNKNOWN'), d.get('component', 'unknown')
                message = d.get('message') or d.get('content') or d.get('eventtemplate')
                if not message:
                    raise ValueError('Missing Message/Content/EventTemplate')
                label = label_value(d.get('label'))
                node = d.get('node', component)
                line_number = int(d.get('lineid') or line_number)
            elif dataset == 'HDFS':
                date, clock, pid, level, component, message = line.split(maxsplit=5)
                ts = timestamp(date + ' ' + clock)
                component = component.rstrip(':')
                node = component
            elif dataset == 'BGL':
                raw_label, epoch, date, node, clock, repeat, kind, component, level, message = line.split(maxsplit=9)
                ts = float(epoch)
                label = 0 if raw_label == '-' else 1
            else:
                m = re.match(r'^(?:(\S+)\s+)?(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d:\d\d(?:\.\d+)?)\s+(\d+)\s+(\w+)\s+(\S+)\s+(.*)$', line)
                if not m:
                    raise ValueError('OpenStack format not recognized')
                logfile, date, clock, pid, level, component, message = m.groups()
                ts = timestamp(date + ' ' + clock)
                node = logfile or component
            if not math.isfinite(ts):
                raise ValueError('Timestamp is not finite')
            block_ids = re.findall(r'blk_-?\d+', message)
            rows.append(dict(line_id=line_number, ts=ts, timestamp=iso(ts), level=level.upper(),
                             component=component, node=node, message=message,
                             template=normalize(message), label=label, blocks=block_ids))
        except (ValueError, KeyError, TypeError) as exc:
            rejects.append({'line': line_number, 'reason': str(exc)})
    if labels_text.strip():
        external = list(csv.DictReader(io.StringIO(labels_text.lstrip('\ufeff'))))
        mapping = {}
        block_mode = False
        for item in external:
            item = {k.lower(): v for k, v in item.items() if k}
            block_mode = 'blockid' in item
            key = item.get('blockid') if block_mode else item.get('lineid')
            if key is None or 'label' not in item:
                raise ValueError('Label CSV needs LineId,Label or BlockId,Label columns.')
            if key in mapping:
                raise ValueError('Duplicate label key: ' + key)
            mapping[key] = label_value(item['label'])
        for row in rows:
            if block_mode:
                found = [mapping.get(b) for b in row['blocks']]
                row['label'] = max(found) if found and all(x is not None for x in found) else None
            else:
                row['label'] = mapping.get(str(row['line_id']))
    rows.sort(key=lambda r: (r['ts'], r['line_id']))
    if not rows:
        raise ValueError('No valid logs parsed. Check dataset selection and input format.')
    return rows, {'input_rows': total, 'parsed_rows': len(rows), 'rejected_rows': len(rejects),
                  'rejects': rejects[:100], 'labels_available': sum(r['label'] is not None for r in rows),
                  'duplicates_retained': True, 'timezone': 'UTC assumed for timezone-free timestamps'}


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def segment(rows, c):
    """Nonoverlapping [start,end) windows; duration uses only previous closed windows.

    Empty gaps are skipped. EWMA arrival rate alpha=.3; target/rate clipped to
    bounds. First window uses fixed_seconds. An event at end goes in next window.
    """
    windows = []
    rate = c.target_logs / c.fixed_seconds
    current = []
    start, end = 0, 0
    skipped_seconds = 0.
    for row in rows:
        if current and row['ts'] >= end:
            windows.append({'start': start, 'end': end, 'rows': current})
            rate = .3 * len(current) / (end-start) + .7 * rate
            skipped_seconds += max(0, row['ts'] - end)
            current = []
        if not current:
            duration = c.fixed_seconds if c.mode == 'fixed' else float(np.clip(c.target_logs/max(rate, 1e-9), c.min_seconds, c.max_seconds))
            start = row['ts']
            end = start + duration
        current.append(row)
    if current:
        windows.append({'start': start, 'end': end, 'rows': current})
    return windows, skipped_seconds


def partition(row, count):
    return int(hashlib.sha256(row['node'].encode()).hexdigest()[:8], 16) % count


def features(rows, duration, frequency, baseline_rate):
    n = len(rows)
    levels = Counter(r['level'] for r in rows)
    templates = Counter(r['template'] for r in rows)
    gaps = np.diff([r['ts'] for r in rows])
    errors = sum(levels[k] for k in ('ERROR', 'FATAL', 'CRITICAL', 'SEVERE', 'FAILURE'))
    rate = n / duration
    return [n, levels['INFO'], levels['WARN']+levels['WARNING'], errors, errors/max(n, 1),
            len(templates), sum(v for k, v in templates.items() if frequency.get(k, 0) <= 2)/max(n, 1),
            len(templates)/max(n, 1), float(np.mean(gaps)) if len(gaps) else 0.,
            float(np.max(gaps)) if len(gaps) else 0., rate, rate/max(baseline_rate, 1e-9)]


def metrics(truth, predictions):
    if not truth or any(x is None for x in truth):
        return None
    tn, fp, fn, tp = confusion_matrix(truth, predictions, labels=[0, 1]).ravel()
    p, r, f, _ = precision_recall_fscore_support(truth, predictions, average='binary', zero_division=0)
    has_positive = bool(sum(truth))
    return dict(precision=float(p) if has_positive else None, recall=float(r) if has_positive else None, f1=float(f) if has_positive else None, accuracy=float(np.mean(np.array(truth)==predictions)),
                fpr=float(fp/(fp+tn)) if fp+tn else None, tp=int(tp), tn=int(tn), fp=int(fp), fn=int(fn),
                positive_windows=int(sum(truth)), evaluated_windows=len(truth),
                single_class=len(set(truth)) < 2)


def run_analysis(text, options=None, labels_text=''):
    global LAST_MODEL
    started = time.perf_counter()
    c = Config(**(options or {})); c.validate()
    labels_text, label_provenance = resolve_labels(text, c.dataset, labels_text)
    rows, quality = parse_logs(text, c.dataset, labels_text)
    if len(rows) > 100000:
        raise ValueError('Interactive analysis supports up to 100,000 parsed rows. Split larger inputs.')
    windows, skipped = segment(rows, c)
    ntrain = int(len(windows)*c.train_fraction)
    if ntrain < 5 or len(windows)-ntrain < 3:
        raise ValueError(f'Only {len(windows)} windows. Need at least 5 training and 3 test windows; reduce duration/target or use more logs.')
    train_rows = [r for w in windows[:ntrain] for r in w['rows']]
    freq = Counter(r['template'] for r in train_rows)
    base_rate = len(train_rows)/sum(w['end']-w['start'] for w in windows[:ntrain])
    all_x = np.array([features(w['rows'], w['end']-w['start'], freq, base_rate) for w in windows])
    models, scores, raw_scores, weights, partition_details = [], [], [], [], []
    train_time = infer_time = 0.
    for p in range(c.partitions):
        pr = [[r for r in w['rows'] if partition(r, c.partitions)==p] for w in windows]
        train_count = sum(len(x) for x in pr[:ntrain])
        if train_count < 5:
            partition_details.append(dict(partition=p, status='skipped: fewer than 5 training logs', training_logs=train_count))
            continue
        pfreq = Counter(r['template'] for group in pr[:ntrain] for r in group)
        prate = train_count/sum(w['end']-w['start'] for w in windows[:ntrain])
        x = np.array([features(r, w['end']-w['start'], pfreq, prate) for r,w in zip(pr,windows)])
        t = time.perf_counter()
        model = IsolationForest(n_estimators=c.trees, max_samples=min(256,ntrain), contamination=c.contamination,
                                random_state=c.seed+p, n_jobs=1).fit(x[:ntrain])
        train_time += time.perf_counter()-t
        t = time.perf_counter()
        raw = -model.score_samples(x)
        calibration = np.sort(raw[:ntrain])
        # Training empirical percentile, with midpoint ranks to handle tied scores.
        calibrated = (np.searchsorted(calibration,raw,side='left')+np.searchsorted(calibration,raw,side='right'))/(2*ntrain)
        infer_time += time.perf_counter()-t
        scores.append(calibrated); raw_scores.append(raw); weights.append(train_count)
        models.append({'partition':p,'model':model,'calibration':calibration,'frequency':pfreq,'baseline_rate':prate})
        partition_details.append(dict(partition=p,status='active',training_logs=train_count, test_logs=sum(len(x) for x in pr[ntrain:])))
    if not models:
        raise ValueError('No partition has enough training logs. Reduce partition count.')
    score = np.average(np.array(scores), axis=0, weights=weights)
    threshold = float(np.quantile(score[:ntrain], 1-c.contamination))
    # Strict greater-than avoids calling a constant-score dataset anomalous.
    pred = (score > threshold).astype(int)
    median = np.median(all_x[:ntrain],axis=0)
    spread = np.std(all_x[:ntrain],axis=0)
    output=[]
    truth=[]
    for i,w in enumerate(windows):
        labels=[r['label'] for r in w['rows']]
        label=max(labels) if all(x is not None for x in labels) else None
        if i>=ntrain: truth.append(label)
        deviations=(all_x[i]-median)/np.maximum(spread,1e-9)
        reasons=[]
        for j in np.argsort(-np.abs(deviations))[:3]:
            if abs(deviations[j])>=1:
                reasons.append(f'{FEATURES[j]} is {"above" if deviations[j]>0 else "below"} the training baseline ({all_x[i,j]:.3g} vs median {median[j]:.3g})')
        if not reasons: reasons=['Combination of features differs from the learned training distribution.']
        excess=(score[i]-threshold)/max(1-threshold,1e-9)
        severity='normal' if not pred[i] else ('critical' if excess>=.75 else 'high' if excess>=.4 else 'medium' if excess>=.15 else 'low')
        output.append(dict(id=i+1,start=iso(w['start']),end=iso(w['end']),duration=w['end']-w['start'],
                           split='train' if i<ntrain else 'test',score=float(score[i]),anomaly=bool(pred[i]),
                           severity=severity,label=label,features=dict(zip(FEATURES,all_x[i].tolist())),
                           partition_scores=[dict(partition=m['partition'],score=float(s[i]),raw_score=float(r[i])) for m,s,r in zip(models,scores,raw_scores)],
                           reasons=reasons,examples=[{'message':r['message'][:400],'template':r['template'],'level':r['level']} for r in w['rows'][:3]]))
    test=output[ntrain:]
    evaluated=metrics(truth,pred[ntrain:].tolist())
    warnings=['Local partition simulation; this run is not a multi-machine distributed benchmark.',
              'Severity and feature deviations are project heuristics, not proven incident causes.']
    if label_provenance.get('id') != 'OpenStack':
        warnings.append('Sparse or sampled logs can distort time-gap/rate features; full-dataset claims require complete streams.')
    if label_provenance['label_kind'] == 'derived_vm_association':
        warnings.append(label_provenance['note'])
    if evaluated is None:warnings.append('Precision, recall and F1 are unavailable: some test logs lack verified labels. No labels are inferred from ERROR severity.')
    elif evaluated['single_class']:warnings.append('Test labels contain only one class; precision/recall/F1 are not a useful complete benchmark.')
    if ntrain<30:warnings.append('Fewer than 30 training windows: scores and thresholds may be unstable.')
    LAST_MODEL=dict(schema_version=1,config=asdict(c),models=models,weights=weights,threshold=threshold,features=FEATURES)
    return dict(schema_version=1, dataset=c.dataset, config=asdict(c),quality=quality,windows=output,
                summary=dict(total_logs=len(rows),total_windows=len(windows),training_windows=ntrain,test_windows=len(test),
                             anomalous_windows=sum(w['anomaly'] for w in test),normal_windows=sum(not w['anomaly'] for w in test),
                             unique_templates=len(set(r['template'] for r in rows)),threshold=threshold,
                             start=iso(rows[0]['ts']),end=iso(rows[-1]['ts']),skipped_empty_seconds=skipped),
                timing=dict(training_seconds=train_time,inference_seconds=infer_time,total_seconds=time.perf_counter()-started),
                metrics=evaluated,partitions=partition_details,warnings=warnings,
                evaluation=dict(provenance=label_provenance, labeled_logs=quality['labels_available'],total_logs=len(rows),
                                positive_logs=sum(r['label']==1 for r in rows),negative_logs=sum(r['label']==0 for r in rows),
                                label_coverage=quality['labels_available']/len(rows)),
                eda=dict(levels=dict(Counter(r['level'] for r in rows)),templates=[dict(template=k,count=v) for k,v in Counter(r['template'] for r in rows).most_common(20)]),
                versions=versions())


def versions():
    import sklearn,sys
    return dict(python=sys.version.split()[0],numpy=np.__version__,sklearn=sklearn.__version__)


def compare_modes(text, options=None, labels_text=''):
    options=dict(options or {})
    results=[]
    for mode,seconds in [('adaptive',60),('fixed',60),('fixed',300),('fixed',600)]:
        cfg={**options,'mode':mode}
        if mode=='fixed':cfg['fixed_seconds']=seconds
        try:
            r=run_analysis(text,cfg,labels_text)
            results.append(dict(mode=mode,seconds=seconds if mode=='fixed' else None,summary=r['summary'],metrics=r['metrics'],timing=r['timing']))
        except ValueError as e:
            results.append(dict(mode=mode,seconds=seconds,error=str(e)))
    return {'results':results,'note':'Each method uses a chronological 60/40 split by its own windows (or selected train fraction). Window boundaries and label units differ, so this is an exploratory ablation, not a matched paper benchmark.'}


def structured_csv(text,dataset,labels_text=''):
    labels_text,_=resolve_labels(text,dataset,labels_text)
    rows,_=parse_logs(text,dataset,labels_text)
    f=io.StringIO();writer=csv.writer(f)
    writer.writerow(['LineId','Timestamp','Level','Component','Node','Message','EventTemplate','Label'])
    for r in rows:
        values=[r['line_id'],r['timestamp'],r['level'],r['component'],r['node'],r['message'],r['template'],'' if r['label'] is None else r['label']]
        writer.writerow(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in values])
    return f.getvalue()


def model_bytes():
    import joblib
    if LAST_MODEL is None:raise ValueError('Run analysis first.')
    buffer=io.BytesIO();joblib.dump(LAST_MODEL,buffer);return buffer.getvalue()


def predict_saved(bundle, text, labels_text=''):
    """Score a new batch without fitting or recalibrating. Load trusted bundles only.

    Window adaptation restarts for this independent batch. This is not stateful
    streaming inference. Node assignment and training normalization are preserved.
    """
    if bundle.get('schema_version')!=1:raise ValueError('Unsupported model schema')
    c=Config(**bundle['config']);c.validate()
    rows,quality=parse_logs(text,c.dataset,labels_text)
    windows,_=segment(rows,c);scores=[]
    for item in bundle['models']:
        groups=[[r for r in w['rows'] if partition(r,c.partitions)==item['partition']] for w in windows]
        x=np.array([features(group,w['end']-w['start'],item['frequency'],item['baseline_rate']) for group,w in zip(groups,windows)])
        raw=-item['model'].score_samples(x);cal=item['calibration']
        scores.append((np.searchsorted(cal,raw,side='left')+np.searchsorted(cal,raw,side='right'))/(2*len(cal)))
    global_scores=np.average(scores,axis=0,weights=bundle['weights'])
    return {'quality':quality,'threshold':bundle['threshold'],'windows':[
        dict(id=i+1,start=iso(w['start']),end=iso(w['end']),score=float(s),anomaly=bool(s>bundle['threshold']))
        for i,(w,s) in enumerate(zip(windows,global_scores))]}


def json_request(payload):
    p=json.loads(payload)
    fn=compare_modes if p.get('action')=='compare' else run_analysis
    return json.dumps(fn(p['text'],p.get('options'),p.get('labels','')),allow_nan=False)
