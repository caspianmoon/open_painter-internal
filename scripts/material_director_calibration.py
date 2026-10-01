#!/usr/bin/env python3
"""Separate deterministic engine calibration; never runs a provider or grades art."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import base64
import hashlib
import html
import json

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('mode', choices=('prepare', 'inventory', 'author', 'gallery'))
p.add_argument('--fixtures', type=Path, default=ROOT/'.blender-test/material-director-benchmark/fixtures-eevee-v1')
p.add_argument('--output', type=Path, required=True)
p.add_argument('--case', choices=('T01','T02','T03','T04','T05'))
p.add_argument('--blender', default='/Applications/Blender.app/Contents/MacOS/Blender')
p.add_argument('--product-root', type=Path, default=Path(os.environ.get('OP_MD_PRODUCT_ROOT', ROOT)),
    help='Exact product checkout or frozen snapshot used by the comparison; defaults to the current development tree')
a = p.parse_args()
if a.mode == 'gallery':
    output = a.output.resolve()
    records = []
    sections = []
    for source in sorted(output.glob('attempt-*/T*/calibration.json')):
        data = json.loads(source.read_text(encoding="utf-8"))
        diagnostics=[json.loads(path.read_text(encoding="utf-8")) for path in (source.parent/'mapping_diagnostic.json',) if path.is_file()]
        frames = []
        figures = []
        for frame in sorted(source.parent.glob('*.png')):
            content = frame.read_bytes()
            frames.append(dict(path=str(frame.relative_to(output)),sha256=hashlib.sha256(content).hexdigest()))
            figures.append('<figure><img src="data:image/png;base64,'+base64.b64encode(content).decode()+
                '"><figcaption>'+html.escape(frame.name)+'</figcaption></figure>')
        record = dict(attempt=source.parent.parent.name, case=data['case_id'],status=data['status'],
            quality='NOT_SCORED',reviewer='PENDING',record=str(source.relative_to(output)),
            fixture_sha256=data['fixture_sha256'],error=data.get('error'),
            assumptions=data.get('assumptions',[]),frames=frames,
            physical_space=data.get('physical_space'),resolution_diagnostics=data.get('resolution_diagnostics'),
            diagnostics=diagnostics,
            source_build=data.get('source_build',dict(status='UNFROZEN_DEVELOPMENT_DIAGNOSTIC',
                note='Historical attempt predates product pinning; not a pinned benchmark comparison')))
        records.append(record)
        sections.append('<section><h2>'+html.escape(record['attempt']+' / '+record['case'])+'</h2><p>'+html.escape(record['status'])+
            ' · NOT SCORED · Reviewer pending</p><p>'+html.escape(record['error'] or '')+'</p><p>'+html.escape(' '.join(record['assumptions']))+
            '</p><p>'+html.escape(' '.join(item.get('finding','') for item in diagnostics))+
            '</p><div class="grid">'+''.join(figures)+'</div><details><summary>Fixture, scale, resolution and source limitations</summary><pre>'+
            html.escape(json.dumps({key:value for key,value in record.items() if key!='frames'},indent=2))+'</pre></details></section>')
    output.mkdir(parents=True,exist_ok=True)
    (output/'calibration_inventory.json').write_text(json.dumps(dict(track='DETERMINISTIC_AUTHORED_ENGINE_CALIBRATION',
        provider_calls=0,reviewer='PENDING',quality='NOT_SCORED',attempts=records),indent=2)+'\n', encoding="utf-8")
    (output/'review.html').write_text('<!doctype html><meta charset="utf-8"><title>Material engine calibration</title><style>'+
        'body{font:16px system-ui;background:#191b1e;color:#eee;max-width:1500px;margin:2rem auto;padding:1rem}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:1rem}figure{margin:0}img{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere}section{border-top:1px solid #555;padding:1.2rem 0}'
        '</style><h1>Deterministic material-engine calibration</h1><p>Authored through public material commands. Zero provider calls. '
        'These are authored engine diagnostics, not human-authored references, AI results, or artistic quality passes. '
        'Reviewer: PENDING. Rating: NOT SCORED. All preview images are embedded for offline review.</p>'+
        '<p>Neutral fixture sizes are explicit nominal assumptions, not source measurements. Cameras, lights, geometry, UVs and bake maps remain fixed. '
        'Rejected authoring attempts are retained below. Editable Blender states and float channel buffers remain in the accompanying local evidence folders.</p>'+''.join(sections), encoding="utf-8")
    print(output/'review.html')
    sys.exit(0)
args = [a.mode, '--fixtures',str(a.fixtures.resolve()),'--output',str(a.output.resolve())]
if a.case: args += ['--case',a.case]
env = os.environ.copy()
env['BLENDER_USER_RESOURCES'] = str(ROOT/'.blender-test/material-director-calibration/blender-profile')
sys.path.insert(0,str(ROOT/'tests/benchmarks/material_director'))
from runner import product_fingerprint
product_root=a.product_root.resolve()
fingerprint=product_fingerprint(product_root)
env['OP_MD_PRODUCT_ROOT']=str(product_root)
env['OP_MD_CALIBRATION_PRODUCT_SHA256']=fingerprint['source_sha256']
result = subprocess.run([a.blender,'--background','--factory-startup','--python-exit-code','1',
    '--python',str(ROOT/'tests/benchmarks/material_director/calibration_author.py'),'--',*args],cwd=ROOT,env=env)
sys.exit(result.returncode)
