"""Alternate baseline/final fresh Blender processes; preserve every raw result."""
import argparse,json,os,subprocess,tempfile,statistics
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--baseline-root',type=Path,required=True)
parser.add_argument('--blender',default='/Applications/Blender.app/Contents/MacOS/Blender')
parser.add_argument('--report',type=Path,default=root/'docs/performance/paired-control.json')
args=parser.parse_args()
blender=args.blender
results=[]
with tempfile.TemporaryDirectory(prefix='op-paired-control-') as folder:
    for repeat in range(3):
        # Alternate order to avoid giving one variant all early or late samples.
        variants=['before','after'] if repeat%2==0 else ['after','before']
        for variant in variants:
            code_root=args.baseline_root.resolve() if variant=='before' else root
            report=Path(folder)/f'{repeat}-{variant}.json'
            command=[blender,'--background','--factory-startup','--python-exit-code','1','--python',str(code_root/'scripts/benchmark_textures.py'),'--','--worker','--fixture','worn','--resolution','2048','--report',str(report)]
            subprocess.run(command,cwd=code_root,env=dict(os.environ,BLENDER_USER_RESOURCES=folder+f'/user-{repeat}-{variant}'),check=True)
            row=json.loads(report.read_text(encoding="utf-8"));row.update(variant=variant,repeat=repeat+1,command=command)
            results.append(row)
            print(variant,repeat+1,row['final']['wall_s'],row['warm_roughness_edit']['wall_s'],flush=True)
summary={v:{p:statistics.median(r[p]['wall_s'] for r in results if r['variant']==v) for p in ['final','warm_roughness_edit']} for v in ['before','after']}
args.report.parent.mkdir(parents=True,exist_ok=True)
args.report.write_text(json.dumps(dict(context='Alternating-order fresh processes, reconstructed baseline instrumentation versus final task source; same 2K Worn inputs. No concurrent Codex Blender workload.',summary=summary,runs=results),indent=2)+'\n', encoding="utf-8")
