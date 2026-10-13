"""Rebuild verified sample sidecars from official source downloads.

Inputs: full LogPAI/loglizer anomaly_label.csv and extracted LogHub OpenStack
archive. No anomaly scores, severity or model predictions enter the labels.
"""
import argparse, collections, csv, hashlib, io, json, re, shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--hdfs-labels',required=True);p.add_argument('--openstack-directory',required=True);a=p.parse_args()
data=ROOT/'dist/data';osdir=Path(a.openstack_directory)
def canon(text):return text.lstrip('\ufeff').replace('\r\n','\n').replace('\r','\n')
def sha(text):return hashlib.sha256(canon(text).encode()).hexdigest()
def make_profile(key,dataset,rawname,labelname,title,kind,note,sources):
 raw=(data/rawname).read_text();labels=(data/labelname).read_text()
 return dict(id=key,dataset=dataset,raw_path=rawname,labels_path=labelname,title=title,
  input_sha256=sha(raw),labels_sha256=sha(labels),rows=len(raw.splitlines()),
  label_kind=kind,note=note,sources=sources)
profiles=[]
# Match only observed block IDs; never manufacture missing block labels.
text=(data/'HDFS_2k.log').read_text();ids=set(re.findall(r'blk_-?\d+',text))
source=list(csv.DictReader(Path(a.hdfs_labels).open()));mapping={r['BlockId']:r['Label'] for r in source if r['BlockId'] in ids}
assert set(mapping)==ids,'HDFS block IDs missing from the official annotation file'
out=io.StringIO();writer=csv.writer(out);writer.writerow(['BlockId','Label']);writer.writerows(sorted(mapping.items()));(data/'HDFS_2k_labels.csv').write_text(out.getvalue())
profiles.append(make_profile('HDFS','HDFS','HDFS_2k.log','HDFS_2k_labels.csv','HDFS · 2,000 official sample logs + block labels','official_block_labels','Official block-session labels mapped to sampled records, then any-positive time-window labels. Sparse time-window evaluation is not the paper’s full block-session benchmark.',[{'url':'https://github.com/logpai/loglizer/blob/master/data/HDFS/anomaly_label.csv','sha256':hashlib.sha256(Path(a.hdfs_labels).read_bytes()).hexdigest()}]))
# Exact membership establishes the original sample is from a normal collection.
normal=set(canon((osdir/'openstack_normal1.log').read_text()).splitlines());sample=(data/'OpenStack_2k.log').read_text().splitlines()
assert all(line in normal for line in sample),'OpenStack sample is not an exact normal1 subset'
(data/'OpenStack_2k_labels.csv').write_text('LineId,Label\n'+''.join(f'{i},0\n' for i in range(1,len(sample)+1)))
sources=[{'url':'https://zenodo.org/records/8196385/files/OpenStack.tar.gz?download=1','files':['anomaly_labels.txt','openstack_abnormal.log','openstack_normal1.log']}]
profiles.append(make_profile('OpenStack_2k','OpenStack','OpenStack_2k.log','OpenStack_2k_labels.csv','OpenStack · original 2k normal-only sample','normal_collection_membership','Every sampled line exactly matches the official normal1 collection. No positive examples: recall/F1 are unavailable; use the annotated failure-run preset for two-class evaluation.',sources))
# Explicitly derived VM-association endpoint, not fault labels invented per line.
annotation=(osdir/'anomaly_labels.txt').read_text();bad_ids=re.findall(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}',annotation);assert len(bad_ids)==4
raw=(osdir/'openstack_abnormal.log').read_text();lines=raw.splitlines()
(data/'OpenStack_labeled.log').write_text(raw)
(data/'OpenStack_anomaly_instances.txt').write_text(annotation)
(data/'OpenStack_labeled_labels.csv').write_text('LineId,Label\n'+''.join(f'{i},{int(any(v in line for v in bad_ids))}\n' for i,line in enumerate(lines,1)))
profiles.append(make_profile('OpenStack','OpenStack','OpenStack_labeled.log','OpenStack_labeled_labels.csv','OpenStack · 18,434 logs from annotated failure run','derived_vm_association','Association evaluation: a line is positive iff it contains one of the 4 officially annotated anomalous VM UUIDs; all other lines are negative for this association target. This is NOT independently verified line-level fault ground truth, and can miss faults without a VM identifier. Any-positive labels are aggregated per time window.',sources))
manifest={'version':1,'retrieved_on':'2026-10-12','profiles':profiles}
(data/'label_manifest.json').write_text(json.dumps(manifest,indent=2))
print(json.dumps([{'profile':x['id'],'rows':x['rows'],'label_kind':x['label_kind']} for x in profiles],indent=2))
