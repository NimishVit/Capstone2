import csv
import io
import json
import sys
from pathlib import Path
import pytest
import numpy as np
sys.path.insert(0,str(Path(__file__).parents[1]/'dist'))
import pipeline as p

ROOT=Path(__file__).parents[1]

@pytest.mark.parametrize('name',['HDFS','BGL','OpenStack'])
def test_official_sample_parsers(name):
    rows,q=p.parse_logs((ROOT/'dist/data'/f'{name}_2k.log').read_text(),name)
    assert len(rows)==2000 and q['rejected_rows']==0
    assert all(a['ts']<=b['ts'] for a,b in zip(rows,rows[1:]))
    assert q['labels_available']==(2000 if name=='BGL' else 0)

def test_regex_and_rejects():
    assert p.normalize('Block blk_-123 from 10.0.0.1 size 400')=='Block <BLOCK> from <IP> size <NUM>'
    rows,q=p.parse_logs('bad\n081109 203615 1 INFO dfs: block blk_123\n','HDFS')
    assert q['rejected_rows']==1 and q['rejects'][0]['line']==1
    assert rows[0]['label'] is None

def test_timezone():
    assert p.timestamp('2025-01-01T01:00:00+01:00')==p.timestamp('2025-01-01T00:00:00Z')

def test_boundaries_are_half_open_and_gap_free_for_records():
    rows=[dict(ts=x,line_id=i) for i,x in enumerate([0,1,10,11,100,100,120])]
    c=p.Config(mode='fixed',fixed_seconds=10)
    windows,gaps=p.segment(rows,c)
    assert [r for w in windows for r in w['rows']]==rows
    assert [len(w['rows']) for w in windows]==[2,2,2,1]
    assert all(w['start']<=r['ts']<w['end'] for w in windows for r in w['rows'])
    assert gaps>0

def test_adaptive_is_causal_and_bounded():
    rows=[dict(ts=x,line_id=i) for i,x in enumerate(list(range(100))+list(range(150,550,10)))]
    c=p.Config(fixed_seconds=10,min_seconds=5,max_seconds=30,target_logs=5)
    ws,_=p.segment(rows,c)
    assert all(5-1e-6<=w['end']-w['start']<=30+1e-6 for w in ws)
    assert len({round(w['end']-w['start'],3) for w in ws})>1
    prefix,_=p.segment(rows[:50],c)
    assert [(w['start'],w['end']) for w in prefix[:-1]]==[(w['start'],w['end']) for w in ws[:len(prefix)-1]]

def test_window_metrics_and_missing_labels():
    assert p.metrics([None,0],[0,0]) is None
    m=p.metrics([0,0,1,1],[0,1,0,1])
    assert (m['tp'],m['tn'],m['fp'],m['fn'])==(1,1,1,1)
    assert m['precision']==m['recall']==m['f1']==.5

def test_labels_never_created_from_error_level():
    raw='081109 203615 1 ERROR dfs: failed blk_1\n081109 203616 1 INFO dfs: okay blk_2'
    rows,_=p.parse_logs(raw,'HDFS');assert all(r['label'] is None for r in rows)
    rows,_=p.parse_logs(raw,'HDFS','BlockId,Label\nblk_1,Anomaly\nblk_2,Normal')
    assert [r['label'] for r in rows]==[1,0]

def test_round_trip_and_formula_safety():
    text='081109 203615 1 INFO dfs: =SUM(A1:A9)'
    out=p.structured_csv(text,'HDFS');row=list(csv.DictReader(io.StringIO(out)))[0]
    assert row['Message'].startswith("'=")
    rows,q=p.parse_logs(out,'HDFS');assert len(rows)==1

def test_determinism_model_reload_and_heldout():
    import joblib
    text=(ROOT/'dist/data/HDFS_2k.log').read_text()
    config=dict(dataset='HDFS',trees=20)
    a=p.run_analysis(text,config)
    bundle=joblib.load(io.BytesIO(p.model_bytes()))
    scored=p.predict_saved(bundle,text)
    assert np.allclose([w['score'] for w in a['windows']],[w['score'] for w in scored['windows']])
    b=p.run_analysis(text,config)
    assert a['summary']==b['summary'] and a['metrics'] is not None
    assert all(w['split']=='train' for w in a['windows'][:a['summary']['training_windows']])
    assert a['summary']['anomalous_windows']==sum(w['anomaly'] for w in a['windows'] if w['split']=='test')
    json.dumps(a,allow_nan=False)

def test_config_and_short_input():
    with pytest.raises(ValueError):p.Config(contamination=0).validate()
    with pytest.raises(ValueError):p.run_analysis('081109 203615 1 INFO dfs: message')

def test_bundled_hdfs_labels_are_exactly_bound():
    text=(ROOT/'dist/data/HDFS_2k.log').read_text()
    labels,meta=p.resolve_labels(text,'HDFS')
    rows,q=p.parse_logs(text,'HDFS',labels)
    assert meta['label_kind']=='official_block_labels'
    assert q['labels_available']==2000
    assert sum(r['label']==1 for r in rows)==70
    assert p.resolve_labels(text.replace('PacketResponder','Changed',1),'HDFS')[0]==''
    assert p.resolve_labels(text,'OpenStack')[0]==''
    assert p.resolve_labels(text.replace('\n','\r\n'),'HDFS')[0]==labels
    assert p.resolve_labels(text,'HDFS','LineId,Label\n1,0')[1]['label_kind']=='user_supplied'

def test_openstack_normal_sample_has_no_fake_f1():
    text=(ROOT/'dist/data/OpenStack_2k.log').read_text()
    r=p.run_analysis(text,{'dataset':'OpenStack','trees':20})
    assert r['quality']['labels_available']==2000
    assert r['metrics']['positive_windows']==0
    assert r['metrics']['recall'] is None and r['metrics']['f1'] is None
    assert r['metrics']['fpr'] is not None
    assert r['evaluation']['provenance']['label_kind']=='normal_collection_membership'

def test_openstack_annotated_association_evaluation():
    text=(ROOT/'dist/data/OpenStack_labeled.log').read_text()
    r=p.run_analysis(text,{'dataset':'OpenStack','trees':20})
    assert r['quality']['labels_available']==18434
    assert r['evaluation']['positive_logs']==112
    assert r['evaluation']['provenance']['label_kind']=='derived_vm_association'
    assert r['metrics']['positive_windows']>0 and not r['metrics']['single_class']
    assert r['metrics']['f1'] is not None
    assert any('NOT independently verified' in w for w in r['warnings'])

def test_mismatched_annotation_checksum_is_rejected(tmp_path,monkeypatch):
    import json
    data=tmp_path/'data';data.mkdir()
    text=(ROOT/'dist/data/HDFS_2k.log').read_text()
    original=json.loads((ROOT/'dist/data/label_manifest.json').read_text())
    (data/'label_manifest.json').write_text(json.dumps(original))
    (data/'HDFS_2k_labels.csv').write_text('BlockId,Label\nblk_1,Normal')
    monkeypatch.setattr(p,'__file__',str(tmp_path/'pipeline.py'))
    with pytest.raises(ValueError,match='checksum mismatch'):p.resolve_labels(text,'HDFS')
