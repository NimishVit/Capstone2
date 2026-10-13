import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'dist'))
import pipeline

p=argparse.ArgumentParser(description='Reproducible LogScope analysis')
p.add_argument('input');p.add_argument('--dataset',choices=['HDFS','BGL','OpenStack'],required=True)
p.add_argument('--labels');p.add_argument('--mode',choices=['adaptive','fixed'],default='adaptive')
p.add_argument('--seconds',type=float,default=60);p.add_argument('--trees',type=int,default=100)
p.add_argument('--compare',action='store_true');p.add_argument('--output',default='results')
a=p.parse_args();out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
text=Path(a.input).read_text(encoding='utf-8');labels=Path(a.labels).read_text() if a.labels else ''
config=dict(dataset=a.dataset,mode=a.mode,fixed_seconds=a.seconds,trees=a.trees)
result=pipeline.run_analysis(text,config,labels)
(out/'result.json').write_text(json.dumps(result,indent=2));(out/'model.joblib').write_bytes(pipeline.model_bytes())
(out/'parsed.csv').write_text(pipeline.structured_csv(text,a.dataset,labels))
if a.compare:(out/'comparison.json').write_text(json.dumps(pipeline.compare_modes(text,config,labels),indent=2))
print(json.dumps({'summary':result['summary'],'metrics':result['metrics'],'timing':result['timing']},indent=2))
