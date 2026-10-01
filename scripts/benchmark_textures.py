"""Run measured CPU fixtures in fresh isolated Blender processes; never a frame/FPS test."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def source_digest():
    h = hashlib.sha256()
    for path in sorted((ROOT / 'src/open_painter').rglob('*.py')):
        h.update(str(path.relative_to(ROOT)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def worker(args):
    sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests/performance')]
    import bpy
    import numpy as np
    import fixtures
    import open_painter
    from open_painter.evaluation import metrics, raster_cache, memory
    from open_painter.evaluation.evaluator import LayerEvaluator
    from open_painter.core import image_manager as images
    open_painter.register()
    scene = bpy.context.scene
    sets = fixtures.create(scene, args.fixture, args.resolution)
    from open_painter.constants import CHANNELS
    for ts in sets:
        # This is the explicit CPU reference benchmark; native sets allocate no outputs.
        ts.preview_backend = 'CPU'
        for channel in CHANNELS:
            images.output(ts, channel)
    channels = [o.channel for o in sets[0].outputs]
    rows = []
    with metrics.Metrics() as cold:
        start, cpu = time.perf_counter(), time.process_time()
        for ts in sets:
            evaluator = LayerEvaluator(scene)
            for output in ts.outputs:
                channel_start = time.perf_counter()
                steps, cpu_steps = [], []
                job = evaluator.channel_steps(ts.id, output.channel)
                while True:
                    a, b = time.perf_counter(), time.process_time()
                    try:
                        next(job)
                    except StopIteration:
                        steps.append(time.perf_counter()-a)
                        cpu_steps.append(time.process_time()-b)
                        break
                    steps.append(time.perf_counter()-a)
                    cpu_steps.append(time.process_time()-b)
                rows.append(dict(channel=output.channel, set_id=ts.id, wall_s=time.perf_counter()-channel_start,
                                 max_step_wall_s=max(steps), max_step_cpu_s=max(cpu_steps),
                                 step_p95_wall_s=float(np.percentile(steps,95))))
        initial = dict(wall_s=time.perf_counter()-start, cpu_s=time.process_time()-cpu, metrics=cold.report())
    for ts in sets:
        for out in ts.outputs:
            out.dirty = False
        fixtures.edit(ts)
    with metrics.Metrics() as warm:
        start, cpu = time.perf_counter(), time.process_time()
        for ts in sets:
            LayerEvaluator(scene).evaluate_texture_set(ts.id)
        edit = dict(wall_s=time.perf_counter()-start, cpu_s=time.process_time()-cpu, metrics=warm.report())
    report = dict(
        measured_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        commit=command('git','rev-parse','HEAD'), source_sha256=source_digest(),
        os=platform.platform(), machine=platform.machine(), cpu=command('sysctl','-n','machdep.cpu.brand_string') or platform.processor(),
        ram_bytes=int(command('sysctl','-n','hw.memsize') or 0) or None, blender=bpy.app.version_string,
        blender_hash=bpy.app.build_hash.decode(), numpy=np.__version__,
        evaluator='CPU NumPy float32, authored resolution; no proxy', renderer=scene.render.engine,
        graphics_backend='no active viewport/GPU context in background fixture',
        resolution=args.resolution, channels=channels, fixture=args.fixture,
        material='Abandoned Machinery' if args.fixture=='abandoned' else 'Worn Painted Metal' if args.fixture in {'worn','three_sets'} else args.fixture,
        layer_records=sum(len(ts.layers) for ts in sets), texture_sets=len(sets),
        mesh=dict(objects=len(scene.objects), vertices=sum(len(o.data.vertices) for o in scene.objects if o.type=='MESH'),
                  assigned_objects=len({r.object for ts in sets for r in ts.assignments if r.object})),
        map_origin='synthetic 32x32 AO/curvature (position/world-normal additionally for abandoned); no mesh baking',
        viewport_rendering_included=False, first_usable_preview_wall_s=None,
        first_channel_publication_wall_s=rows[0]['wall_s'], final=initial, warm_roughness_edit=edit, channels_measured=rows,
        scalar_cache_bytes=sum(c.used for c in raster_cache._shared.values()),
        controlled_cache_bytes=memory.shared.used,
        cache_accounting='Legacy scalar_cache_bytes sums per-set ownership; controlled_cache_bytes deduplicates all admitted CPU arrays including geometry. Neither caps Blender process memory.',
        limitations=['CPU output publication is not displayed-frame latency.',
                     'Process RSS includes Blender and all previous work in this fresh process.',
                     'Triplanar uses shared evaluated mesh data; the Worn fixture has no assigned geometry or viewport rendering.']
    )
    if args.io:
        with metrics.Metrics() as io_metrics, tempfile.TemporaryDirectory(prefix='op-benchmark-io-') as folder:
            from open_painter.export.exporter import export
            fixtures.edit(sets[0])
            start=time.perf_counter()
            bpy.ops.wm.save_as_mainfile(filepath=folder+'/benchmark.blend')
            report['save_wall_s']=time.perf_counter()-start
            start=time.perf_counter()
            bpy.ops.wm.open_mainfile(filepath=folder+'/benchmark.blend')
            report['reopen_wall_s']=time.perf_counter()-start
            ts=bpy.context.scene.open_painter.texture_sets[0]
            fixtures.edit(ts)
            ts.export.directory=folder+'/textures'
            start=time.perf_counter()
            paths=export(bpy.context,ts,ts.export)
            report['export']=dict(wall_s=time.perf_counter()-start, files=len(paths))
            report['io_metrics']=io_metrics.report()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,indent=2)+'\n', encoding="utf-8")
    open_painter.unregister()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender',default='/Applications/Blender.app/Contents/MacOS/Blender')
    parser.add_argument('--resolutions',nargs='+',type=int,default=[512,2048],choices=[512,2048])
    parser.add_argument('--fixtures',nargs='+',default=['worn'])
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--report',type=Path,default=ROOT/'docs/performance/baseline.json')
    parser.add_argument('--stage',default='baseline')
    parser.add_argument('--io',action='store_true')
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--fixture',default='worn')
    parser.add_argument('--resolution',type=int,default=2048)
    argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    args=parser.parse_args(argv)
    if args.worker:
        worker(args)
        return
    if args.repeats<1:
        parser.error('--repeats must be positive')
    results=[]
    with tempfile.TemporaryDirectory(prefix='open-painter-benchmark-') as temp:
        env=dict(os.environ,BLENDER_USER_RESOURCES=temp+'/user')
        for fixture in args.fixtures:
            for n in args.resolutions:
                for index in range(args.repeats):
                    path=Path(temp)/'result.json'
                    cmd=[args.blender,'--background','--factory-startup','--python-exit-code','1','--python',str(Path(__file__).resolve()),'--',
                         '--worker','--fixture',fixture,'--resolution',str(n),'--report',str(path)]
                    if args.io: cmd+=['--io']
                    subprocess.run(cmd,cwd=ROOT,env=env,check=True)
                    result=json.loads(path.read_text(encoding="utf-8")); result['repeat']=index+1
                    results.append(result)
                    print(f'{fixture} {n} run {index+1}: {result["final"]["wall_s"]:.3f}s cold, {result["warm_roughness_edit"]["wall_s"]:.3f}s warm',flush=True)
    summary=[]
    for fixture in args.fixtures:
        for n in args.resolutions:
            samples=[r for r in results if r['fixture']==fixture and r['resolution']==n]
            summary.append(dict(fixture=fixture,resolution=n,samples=len(samples),
                cold_median_wall_s=statistics.median(r['final']['wall_s'] for r in samples),
                warm_median_wall_s=statistics.median(r['warm_roughness_edit']['wall_s'] for r in samples),
                timing_p95=None, note='No across-run p95 estimate with fewer than 20 independent runs.'))
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(dict(stage=args.stage,command=sys.argv,summary=summary,runs=results),indent=2)+'\n', encoding="utf-8")

if __name__=='__main__': main()
