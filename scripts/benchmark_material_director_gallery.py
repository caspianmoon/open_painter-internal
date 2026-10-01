#!/usr/bin/env python3
"""Export faithful review HTML or a verified full-evidence archive; never call AI."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import quote, unquote, urlparse
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests/benchmarks"))
from material_director.outcomes import adapt_result
from material_director.png_validation import decode_png
from material_director.view_inventory import inventory as view_inventory

VIEWS = {
    "before": {"front": "00_before.png"},
    "create": {"front": "01_create_front.png", "back": "02_create_back.png", "close": "03_create_close.png"},
    "refine_1": {"front": "04_refine1_front.png", "back": "05_refine1_back.png", "close": "06_refine1_close.png"},
    "refine_2": {"front": "07_refine2_front.png", "back": "08_refine2_back.png", "close": "09_refine2_close.png"},
}

def read_json(path, warnings):
    if not path.is_file(): return None
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        warnings.append(f"{path.name}: {exc}"); return None

def digest(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()

def relative_link(path, output):
    return quote(Path(os.path.relpath(path, output.parent)).as_posix(), safe="/")

def collect_run(root, entry, output, *, portable=False, expected_size=(1024,1024), protocol_version=None):
    relative=Path(entry["relative_directory"])
    if relative.is_absolute() or ".." in relative.parts: raise ValueError("Unsafe scheduled directory")
    directory=root/relative; warnings=[]
    original=read_json(directory/"result.json",warnings) or {}
    result=adapt_result(original)
    rows={r["name"]:r for r in result["stages"]}
    recorded=result.get("finalization",{}).get("primary_evidence",{}).get("files",{})
    frames={}; stages={}
    version = protocol_version or original.get('record_version',2)
    plans = {label.removeprefix('after_'):dict(views) for label,views in view_inventory(
        dict(protocol_version=version,case_id=entry.get('case_id'))).items()}
    for stage,planned in plans.items():
        row=rows.get(stage,{})
        execution=row.get("execution",{"state":"NOT_RUN","cause_code":"NO_ATTEMPT"})
        label="before" if stage=="before" else "after_"+stage
        if row.get("evidence",{}).get("role") in {"FAILURE_STATE","UNCHANGED_AFTER_ATTEMPT"}:
            label=Path(row["evidence"].get("checkpoint","failure_"+stage+"_state.json")).name.removesuffix("_state.json")
        checkpoint=read_json(directory/(label+"_state.json"),warnings) or {}
        capture=checkpoint.get("views",{})
        captures={r.get("pose",Path(r.get("path","")).stem):r for r in capture.get("views",[])}
        captures_by_name={Path(r.get("path","")).name:r for r in capture.get("views",[])}
        frames[stage]={}
        for view in dict.fromkeys(["front","back","close",*planned,*captures]):
            filename=planned.get(view); actual=captures.get(view) or captures_by_name.get(filename,{})
            declared=actual.get("path")
            if declared:
                raw=Path(declared)
                if raw.is_absolute():
                    # Records may come from the original machine. A same-name
                    # capture under this run is relocatable; arbitrary external paths are not.
                    candidates=[directory/raw.name,*directory.glob("supplemental_views/*/"+raw.name)]
                    path=next((p for p in candidates if p.is_file()),directory/raw.name)
                else: path=directory/raw
            else: path=directory/filename if filename else None
            if path is not None and not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("Evidence path escapes its run directory")
            frame={"status":"NOT_SCHEDULED","url":None,"message":"View not scheduled", "role":row.get("evidence",{}).get("role","NEUTRAL" if stage=="before" else "APPLIED_STATE")}
            if execution["state"]=="SKIPPED_DEPENDENCY":
                frame.update(status="NOT_APPLICABLE",message="SKIPPED_DEPENDENCY: "+execution.get("dependency_stage","create"))
            elif stage!="before" and execution["state"] in {"NOT_RUN","RUNNING"}:
                frame.update(status="NOT_SCHEDULED",message=execution["state"])
            elif path is not None:
                frame.update(filename=path.name,status="ARTIFACT_MISSING",message="Recorded artifact is absent" if checkpoint else "No checkpoint exists for this attempt")
                if capture.get("status") in {"FAILED","PARTIAL","BLOCKED"}:
                    frame.update(status="CAPTURE_FAILED",message="; ".join(map(str,capture.get("errors",[]))) or "Capture failed")
                if path.is_file():
                    expected=actual.get("sha256") or recorded.get(path.name)
                    measured=digest(path); frame.update(sha256=measured,expected_sha256=expected)
                    try:
                        raw=path.read_bytes(); frame["decoding"]=decode_png(raw,expected_size)
                        frame.update(status="CAPTURED_VERIFIED" if expected==measured else "HASH_MISMATCH",
                                     message="Decoded PNG and recorded checksum verified" if expected==measured else "Missing or mismatched original checksum",
                                     url="data:image/png;base64,"+base64.b64encode(raw).decode() if portable else relative_link(path,output))
                    except (ValueError,zlib.error) as exc:
                        frame.update(status="CAPTURE_FAILED",message="PNG decode failed: "+str(exc),cause_code="PNG_DECODE_FAILED")
            frames[stage][view]=frame
        attempts=row.get("proposal_attempts",[])
        def targets(attempt):
            return len(attempt.get("targets",[])) if isinstance(attempt,dict) and "targets" in attempt else 1
        stages[stage]={"execution":execution,"quality":row.get("quality",{"state":"NOT_REVIEWED"}),
            "response":row.get("response",""),"first_validation_error":next(iter(row.get("validation_errors",[])),None),
            "proposed_target_count":sum(targets(a) for a in attempts),"committed_target_count":row.get("receipt_count",0),
            "actual_prompt":row.get("prompt"),"failure_evidence":row.get("evidence",{})}
    links={}
    for key,name in (("result","result.json"),("trace","provider_and_tool_trace.jsonl"),("blend","final.blend"),("failure_blend","failure.blend")):
        if (directory/name).is_file(): links[key] = None if portable else relative_link(directory/name,output)
    execution_states=[stages[name]['execution']['state'] for name in ('create','refine_1','refine_2')]
    execution_summary='APPLIED' if all(value=='APPLIED' for value in execution_states) else next(
        (value for value in execution_states if value not in {'APPLIED','SKIPPED_DEPENDENCY'}),'NOT_RUN')
    # H permits generation; only recorded generation events establish a request.
    generation_requests=sum(row.get('generation_call_count',0) for row in rows.values())
    return {"run_id":result.get("run_id",entry["run_id"]),"status":result.get("status","NOT_RUN"),
        "execution_summary":execution_summary,
        "build_id":entry.get("build_id"),"mode":entry.get("mode"),"repeat":entry.get("repeat"),
        "quality":"NOT_REVIEWED", "stages":stages,"frames":frames,"warnings":warnings,
        "failures":result.get("failures",[]),"links":links,"fixture_sha256":result.get("fixture",{}).get("sha256"),
        "provider":result.get("provider",{}),"generated_inputs":result.get("generated_inputs",{}),
        "actual_generated_images":len(result.get("actual_generated_inputs",[])),
        "generation_allowed":entry.get("mode")=="H", "generation_requests":generation_requests,
        "requested_generation":generation_requests>0, "directory":relative.as_posix()}

# zlib errors are evidence failures, not fatal gallery construction errors.
import zlib

def collect(root, output, *, portable=False, case_ids=None):
    root,output=Path(root),Path(output); warnings=[]
    manifest=read_json(root/"manifest.json",warnings)
    if not manifest: raise ValueError("Frozen manifest required")
    cards=[]
    report=read_json(root/'report.json',warnings) or {}
    reviewed={row.get('run_id'):row for row in report.get('core_results',[])+report.get('ablation_results',[])}
    if case_ids and not set(case_ids).issubset({case['id'] for case in manifest['specification']['cases']}):
        raise ValueError('Unknown case requested for review export')
    for case in manifest["specification"]["cases"]:
        if case_ids and case['id'] not in case_ids: continue
        runs=[collect_run(root,e,output,portable=portable,protocol_version=manifest.get('schema_version',2)) for e in manifest["schedule"] if e["kind"]=="core" and e["case_id"]==case["id"]]
        for run in runs:
            score=reviewed.get(run['run_id'],{})
            run['quality']=score.get('quality_status','NOT_REVIEWED')
            run['independent_review']=score
            for stage,row in run['stages'].items():
                rating=score.get('stages',{}).get(stage,{})
                if rating:
                    row['quality']={'state':rating.get('quality_status','NOT_REVIEWED'),
                                    'review':rating.get('review',{}), 'failures':rating.get('quality_failures',[])}
        cards.append(dict(id=case["id"],title=case["title"],runs=runs,prompts=[case["create"],*case["refinements"]]))
    config=manifest["configuration"]
    return dict(comparison=config["comparison_id"],protocol=manifest["protocol"],build=config.get("build"),builds=config.get("builds"),
        model=config.get("provider",{}).get("model_id"),fixtures=manifest.get("fixtures"),cards=cards,portable=portable,
        generated_at=datetime.now(timezone.utc).isoformat(),quality="NOT_REVIEWED",
        review_summary=dict(reviewed_sessions=sum(r.get('quality_status') in {'PASS','FAIL'} for r in reviewed.values()),
                            pending_sessions=sum(r.get('quality_status','NOT_REVIEWED')=='NOT_REVIEWED' for r in reviewed.values())))

def render(data):
    esc=lambda x:html.escape(str(x))
    output=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Material Director evidence</title><style>body{font:16px system-ui;background:#12171c;color:#edf1f5;margin:24px}a{color:#b4dbff}article,section{border:1px solid #506070;padding:16px;margin:16px 0}figure{margin:0;min-width:0}img{width:100%;height:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}.pair,.views{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.views{grid-template-columns:repeat(3,minmax(0,1fr))}.meta{color:#b7c5d1}summary{cursor:pointer}small{overflow-wrap:anywhere}@media(max-width:700px){.pair,.views{grid-template-columns:1fr}}</style><h1>Material Director evidence</h1>']
    output += ['<p>'+esc(data['comparison'])+' · '+esc(data['protocol'])+' · Model '+esc(data['model'])+'</p>', '<p>Execution and artistic quality are independent. Missing human reviews remain NOT REVIEWED. Failure-state images never establish successful texturing.</p>', '<details><summary>Frozen build and fixture metadata</summary><pre>'+esc(json.dumps({k:data[k] for k in ('build','builds','fixtures')},indent=2))+'</pre></details>']
    def frame(value):
        title=esc(value.get('role',''))+' / '+esc(value['status'])
        image='<img loading="lazy" src="'+esc(value['url'])+'" alt="Recorded material evidence">' if value.get('url') else '<p>'+esc(value.get('message',''))+'</p>'
        return '<figure>'+image+'<figcaption>'+title+'<br><small>'+esc(value.get('sha256',''))+'</small></figcaption></figure>'
    for card in data['cards']:
        output.append('<article><h2>'+esc(card['id']+' '+card['title'])+'</h2>')
        for run in card['runs']:
            output += ['<section><h3>'+esc(run['run_id'])+'</h3><p>'+esc(run['execution_summary'])+' / '+esc(run['quality'])+'</p><p>Run delivery: '+esc(run['status'])+'</p>', '<p class="meta">Fixture '+esc(run['fixture_sha256'])+' · Generation allowed '+esc(run['generation_allowed'])+' · Observed generation requests '+str(run['generation_requests'])+' · Actual generated '+str(run['actual_generated_images'])+' · Source assignments '+esc(run['generated_inputs'])+'</p>']
            output.append('<div class="pair"><div><h4>Create</h4>'+frame(run['frames']['create']['front'])+'</div><div><h4>Final revision</h4>'+frame(run['frames']['refine_2']['front'])+'</div></div>')
            for name in VIEWS:
                row=run['stages'][name]
                output.append('<details><summary>'+esc(name)+' · '+esc(row['execution']['state'])+' / '+esc(row['quality']['state'])+'</summary><pre>'+esc(json.dumps(row,indent=2))+'</pre><div class="views">')
                output.extend(frame(v) for v in run['frames'][name].values())
                output.append('</div></details>')
            output.append('<pre>'+esc(json.dumps(run['failures'],indent=2))+'</pre>')
            for name,url in run['links'].items():
                if url: output.append('<a href="'+esc(url)+'">'+esc(name)+'</a> · ')
            output.append('</section>')
        output.append('</article>')
    # PNG bytes already live in the figures. Repeating the entire base64 image
    # inventory inside inert metadata doubles full-campaign browser memory.
    def metadata(value):
        if isinstance(value,dict): return {k:metadata(v) for k,v in value.items()}
        if isinstance(value,list): return [metadata(v) for v in value]
        if isinstance(value,str) and value.startswith('data:image/png;base64,'): return 'EMBEDDED_IN_FIGURE'
        return value
    payload=json.dumps(metadata(data),ensure_ascii=False,allow_nan=False).replace('<',r'\u003c')
    output.append('<script id="gallery-data" type="application/json">'+payload+'</script></html>')
    return '\n'.join(output)

class Resources(HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]; self.images=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        for key in ('href','src'):
            if key in a: self.urls.append(a[key])
        if tag=='img': self.images.append(a.get('src',''))

def validate_html(path, *, portable=False):
    parser=Resources(); parser.feed(Path(path).read_text(encoding="utf-8")); errors=[]; decoded={}
    for url in parser.urls:
        if url.startswith('data:image/png;base64,'):
            if url not in decoded:
                try: decoded[url]=decode_png(base64.b64decode(url.split(',',1)[1],validate=True),(1024,1024))
                except Exception as exc: errors.append('Embedded image: '+str(exc))
        elif url.startswith('#'): continue
        elif portable: errors.append('Portable review depends on external/local resource: '+url)
        elif urlparse(url).scheme: errors.append('Unexpected remote resource: '+url)
        elif not (Path(path).parent/unquote(url)).is_file(): errors.append('Broken local resource: '+url)
    return dict(status='COMPLETE' if not errors else 'INCOMPLETE',errors=errors,decoded_images=len(decoded),image_elements=len(parser.images))

def export_html(root, output, *, portable=False, case_ids=None):
    data=collect(root,output,portable=portable,case_ids=case_ids); output.parent.mkdir(parents=True,exist_ok=True); output.write_text(render(data), encoding="utf-8")
    checked=validate_html(output,portable=portable)
    checked['html_status']=checked['status']
    checked['evidence_errors']=[run['run_id']+': '+stage+'/'+view+': '+frame['status']
        for card in data['cards'] for run in card['runs'] for stage,views in run['frames'].items()
        for view,frame in views.items() if frame['status'] in {'ARTIFACT_MISSING','HASH_MISMATCH','CAPTURE_FAILED'}
        and (stage=='before' and run['status'] not in {'NOT_RUN','UNAVAILABLE'} or run['stages'][stage]['execution']['state']=='APPLIED')]
    checked['evidence_status']='INCOMPLETE' if checked['evidence_errors'] else 'COMPLETE'
    if portable:
        with tempfile.TemporaryDirectory(prefix='director-offline-review-') as empty:
            copied=Path(empty)/'review.html';shutil.copyfile(output,copied)
            checked['offline_empty_directory']=validate_html(copied,portable=True)
    if checked['evidence_status']!='COMPLETE' or checked.get('offline_empty_directory',{}).get('status','COMPLETE')!='COMPLETE':
        checked['status']='INCOMPLETE'
    return checked

def export_portable_cases(root, output):
    """Bound browser memory; every case HTML remains independently portable."""
    root,output=Path(root),Path(output)
    if output.exists() and any(output.iterdir()): raise FileExistsError('Use an empty portable-case export directory')
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((root/'manifest.json').read_text(encoding="utf-8"));records=[]
    for case in manifest['specification']['cases']:
        path=output/(case['id']+'.html')
        checked=export_html(root,path,portable=True,case_ids={case['id']})
        records.append(dict(case_id=case['id'],file=path.name,sha256=digest(path),bytes=path.stat().st_size,validation=checked))
    links=''.join('<li><a href="'+html.escape(r['file'])+'">'+html.escape(r['case_id'])+'</a> · '+str(r['bytes'])+' bytes</li>' for r in records)
    (output/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Material Director review</title><h1>Material Director review</h1><p>Each case file contains its own images and can be copied and opened alone offline. This index links the five files. Human ratings remain pending until independently reviewed.</p><ul>'+links+'</ul></html>', encoding="utf-8")
    index_validation=validate_html(output/'index.html')
    checked=dict(status='COMPLETE' if index_validation['status']=='COMPLETE' and all(
        r['validation']['status']=='COMPLETE' and r['validation']['evidence_status']=='COMPLETE' for r in records) else 'INCOMPLETE',
        format='portable_cases_v1',cases=records,index_validation=index_validation)
    (output/'validation.json').write_text(json.dumps(checked,indent=2), encoding="utf-8")
    return checked

def export_archive(root, output, *, separate_blends=False):
    root,output=Path(root).resolve(),Path(output).resolve()
    if output.exists(): raise FileExistsError('Never overwrite a published evidence archive')
    if output.is_relative_to(root): raise ValueError('Evidence ZIP must be outside its source directory')
    with tempfile.TemporaryDirectory(prefix='director-evidence-') as staging:
        package=Path(staging)/'evidence';package.mkdir()
        for path in root.rglob('*'):
            if path.is_file() and path.suffix not in {'.zip'}:
                target=package/path.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
        check=export_html(package,package/'gallery.html')
        if check['status']!='COMPLETE' or check['evidence_status']!='COMPLETE':
            raise ValueError('Incomplete advertised archive: '+repr(check['errors']+check['evidence_errors']))
        companion={}
        if separate_blends:
            companion_path=output.with_name(output.stem+'-blends.zip')
            if companion_path.exists():raise FileExistsError(companion_path)
            with zipfile.ZipFile(companion_path,'x',compression=zipfile.ZIP_DEFLATED) as archive:
                for path in package.rglob('*.blend'):
                    companion[path.relative_to(package).as_posix()]=digest(path)
                    archive.write(path,path.relative_to(package).as_posix())
            # The primary HTML keeps state links valid only in a complete archive.
            # Companion export therefore includes a plaintext declaration and
            # removes state anchors, never advertises silently broken links.
            text=(package/'gallery.html').read_text(encoding="utf-8")
            import re
            text=re.sub(r'<a href="[^"]+\.blend">([^<]+)</a>',r'<span>\1: see declared companion archive</span>',text)
            (package/'gallery.html').write_text(text, encoding="utf-8")
            for path in package.rglob('*.blend'):path.unlink()
        records={p.relative_to(package).as_posix():digest(p) for p in package.rglob('*') if p.is_file()}
        inventory={'format':'director_evidence_zip_v1','files':records,'companion_blends':companion,
                   'companion_archive':companion_path.name if separate_blends else None,'portable_review':False}
        (package/'evidence_inventory.json').write_text(json.dumps(inventory,indent=2), encoding="utf-8")
        output.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            for p in package.rglob('*'):
                if p.is_file():archive.write(p,p.relative_to(package).as_posix())
    checked=validate_archive(output)
    if checked['status']!='COMPLETE': raise ValueError(checked)
    return checked

def validate_archive(path):
    errors=[]
    with zipfile.ZipFile(path) as archive:
        if archive.testzip(): errors.append('ZIP CRC failure')
        try: inventory=json.loads(archive.read('evidence_inventory.json'))
        except (KeyError,ValueError):return {'status':'INCOMPLETE','errors':['Evidence inventory missing']}
        for name,expected in inventory['files'].items():
            try:data=archive.read(name)
            except KeyError:errors.append('Missing '+name);continue
            if hashlib.sha256(data).hexdigest()!=expected:errors.append('Hash mismatch '+name)
            if name.endswith('.png'):
                try:decode_png(data)
                except Exception as exc:errors.append(name+': '+str(exc))
        if inventory.get('companion_archive'):
            companion=Path(path).with_name(inventory['companion_archive'])
            if not companion.is_file():errors.append('Declared blend companion missing')
            else:
                with zipfile.ZipFile(companion) as extra:
                    for name,expected in inventory['companion_blends'].items():
                        try:data=extra.read(name)
                        except KeyError:errors.append('Companion missing '+name);continue
                        if hashlib.sha256(data).hexdigest()!=expected:errors.append('Companion hash mismatch '+name)
    return {'status':'COMPLETE' if not errors else 'INCOMPLETE','errors':errors,'files':len(inventory['files'])}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('comparison',type=Path);parser.add_argument('--output',type=Path)
    parser.add_argument('--format',choices=('html','portable','portable-cases','zip'),default='html');parser.add_argument('--separate-blends',action='store_true')
    parser.add_argument('--case',action='append',dest='case_ids',help='Export only this case; may be repeated for HTML formats')
    args=parser.parse_args()
    if args.case_ids and args.format not in {'html','portable'}:parser.error('--case is available for html/portable exports')
    root=args.comparison.resolve();output=(args.output or root/('portable_cases' if args.format=='portable-cases' else 'portable_review.html' if args.format=='portable' else 'gallery.html')).resolve()
    if args.format=='zip':checked=export_archive(root,output,separate_blends=args.separate_blends)
    elif args.format=='portable-cases':checked=export_portable_cases(root,output)
    else:checked=export_html(root,output,portable=args.format=='portable',case_ids=args.case_ids)
    print(json.dumps(dict(output=str(output),validation=checked),indent=2))
    return 0 if checked['status']=='COMPLETE' else 1
if __name__=='__main__':raise SystemExit(main())
