#!/usr/bin/env python3
"""Build a local, dependency-free gallery from an existing frozen comparison.

Reads only manifest/result/checkpoint JSON and the existing PNG/.blend artifacts.
Writes one gallery.html; never edits an image or invents scores/completed stages.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
from urllib.parse import quote

VIEWS = {
    "before": {"front": "00_before.png"},
    "create": {"front": "01_create_front.png", "back": "02_create_back.png", "close": "03_create_close.png"},
    "refine_1": {"front": "04_refine1_front.png", "back": "05_refine1_back.png", "close": "06_refine1_close.png"},
    "refine_2": {"front": "07_refine2_front.png", "back": "08_refine2_back.png", "close": "09_refine2_close.png"},
}
CHECKPOINT = {stage: "before" if stage == "before" else "after_" + stage for stage in VIEWS}


def read_json(path, warnings):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value
    except (ValueError, OSError) as exc:
        warnings.append(f"{path.name}: {type(exc).__name__}: {exc}")
        return None


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def relative_link(path, output):
    return quote(Path(os.path.relpath(path, output.parent)).as_posix(), safe="/")


def stage_rows(result):
    stages = result.get("stages", {})
    if isinstance(stages, list):
        return {row.get("name"): row for row in stages if isinstance(row, dict)}
    return stages if isinstance(stages, dict) else {}


def describe_failure(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        prefix = ": ".join(str(value[key]) for key in ("stage", "category") if value.get(key))
        detail = value.get("message", value.get("reason", value.get("status", "")))
        return (prefix + ": " if prefix else "") + str(detail or json.dumps(value, ensure_ascii=False))
    return str(value)


def collect_run(root, entry, output):
    relative = Path(entry["relative_directory"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Scheduled run directory must be relative to the comparison")
    directory = root / relative
    warnings = []
    result_path = directory / "result.json"
    result = read_json(result_path, warnings) or {}
    rows = stage_rows(result)
    recorded_files = result.get("finalization", {}).get("primary_evidence", {}).get("files", {})
    frames = {}
    for stage, planned in VIEWS.items():
        checkpoint_path = directory / (CHECKPOINT[stage] + "_state.json")
        checkpoint = read_json(checkpoint_path, warnings) or {}
        capture = checkpoint.get("views", {})
        captures = {Path(row.get("path", "")).name: row for row in capture.get("views", []) if isinstance(row, dict)}
        frames[stage] = {}
        for view in ("front", "back", "close"):
            filename = planned.get(view)
            frame = {"status": "NOT_SCHEDULED", "message": "Before texturing, the protocol records the front view only.",
                     "url": None, "capture_status": capture.get("status", "NO_CHECKPOINT")}
            if filename:
                path = directory / filename
                frame.update(filename=filename, status="MISSING", message="No image file was recorded for this stage and view.")
                if path.is_file():
                    expected = captures.get(filename, {}).get("sha256") or recorded_files.get(filename)
                    actual = digest(path)
                    with path.open("rb") as stream:
                        png = stream.read(8) == b"\x89PNG\r\n\x1a\n"
                    frame.update(url=relative_link(path, output), sha256=actual,
                                 status="CAPTURED" if expected == actual and png else "UNVERIFIED",
                                 message="Recorded image; checksum matches its evidence record." if expected == actual and png
                                         else "Image exists, but its capture checksum is not available.")
                    if expected and expected != actual:
                        frame.update(status="HASH_MISMATCH", message="Image differs from its recorded checksum. Keep this output visible for investigation.")
                    if not png:
                        frame.update(status="INVALID_IMAGE", message="The recorded file does not have a PNG header.", url=None)
                if capture.get("errors"):
                    frame["capture_errors"] = [describe_failure(error) for error in capture["errors"]]
            frames[stage][view] = frame
    failures = [describe_failure(failure) for key in ("failures", "hard_failures") for failure in result.get(key, [])]
    if result.get("reason"):
        failures.append(str(result["reason"]))
    elapsed = result.get("elapsed_seconds", result.get("metrics", {}).get("elapsed_seconds"))
    stages = {}
    for name in ("create", "refine_1", "refine_2"):
        row = rows.get(name, {})
        stages[name] = {
            "status": row.get("status", "NOT_RUN"), "actual_prompt": row.get("prompt"),
            "visible_complete": row.get("visible_complete"), "applied": row.get("applied"),
            "no_op_review": row.get("no_op_review"),
        }
    available = [name for name in VIEWS if frames[name]["front"].get("url")]
    return {
        "run_id": result.get("run_id", entry.get("run_id", relative.name)),
        "mode": entry.get("mode", result.get("mode", "")), "repeat": entry.get("repeat", result.get("repeat", 1)),
        "status": result.get("status", "RESULT_MISSING"), "failures": failures, "warnings": warnings,
        "elapsed_seconds": elapsed, "stages": stages, "frames": frames,
        "default_stage": available[-1] if available else "before",
        "result_url": relative_link(result_path, output) if result_path.is_file() else None,
        "blend_url": relative_link(directory / "final.blend", output) if (directory / "final.blend").is_file() else None,
        "directory": relative.as_posix(),
        "captured_frames": sum(frame["status"] == "CAPTURED" for views in frames.values() for frame in views.values()),
        "existing_frames": sum(bool(frame.get("url")) for views in frames.values() for frame in views.values()),
    }


def collect(root, output):
    warnings = []
    manifest = read_json(root / "manifest.json", warnings)
    if manifest is None:
        raise ValueError("An existing frozen manifest.json is required: " + "; ".join(warnings))
    specification = manifest.get("specification", {})
    cases = specification.get("cases", [])
    if len(cases) != 5 or {case.get("id") for case in cases} != {"T01", "T02", "T03", "T04", "T05"}:
        raise ValueError("This gallery requires the five actual T01–T05 cases in the frozen manifest")
    schedule = manifest.get("schedule", [])
    cards = []
    for case in cases:
        runs = [collect_run(root, entry, output) for entry in schedule
                if entry.get("kind") == "core" and entry.get("case_id") == case["id"]]
        cards.append({"id": case["id"], "title": case["title"], "fixture": case.get("fixture"),
                      "prompts": {"create": case.get("create", ""),
                                  "refine_1": (case.get("refinements", []) + [""] * 2)[0],
                                  "refine_2": (case.get("refinements", []) + [""] * 2)[1]},
                      "runs": runs, "requirements": case.get("requirements", [])})
    config = manifest.get("configuration", {})
    return {"comparison": config.get("comparison_id", root.name),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "manifest_url": relative_link(root / "manifest.json", output),
            "common_instruction": specification.get("common_instruction", ""),
            "protocol": manifest.get("protocol", specification.get("protocol", "")),
            "cards": cards}


TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Material Director · Five-model evidence gallery</title>
<style>
:root{color-scheme:dark;--bg:#111418;--panel:#1a2026;--line:#35404a;--ink:#edf2f6;--muted:#abb8c3;--accent:#a6d7f9;--warn:#ffd68b;--bad:#ffb1ab}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}a{color:var(--accent)}button,select{font:inherit;color:inherit;background:#26313b;border:1px solid #536574;border-radius:7px;padding:7px 11px;cursor:pointer}button:hover{background:#354653}button[aria-pressed=true]{background:#c2e3fb;color:#132735;border-color:#c2e3fb}button:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--warn);outline-offset:3px}h1,h2,h3,p{margin-top:0}h1{font-size:clamp(24px,4vw,38px);line-height:1.15;margin-bottom:12px}h2{font-size:21px;margin-bottom:4px}h3{font-size:15px}.wrap{max-width:1500px;margin:auto;padding:24px}.eyebrow{font-size:12px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}.notice{border:1px solid #947544;background:#352d20;color:#ffe4b5;border-radius:10px;padding:14px 18px;margin:20px 0}.notice strong{display:block;font-size:18px}.muted{color:var(--muted)}.tools{position:sticky;top:0;z-index:5;background:#111418f5;border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}.tools .wrap{padding-top:12px;padding-bottom:12px;display:flex;gap:16px;flex-wrap:wrap;align-items:center}.buttons{display:flex;gap:6px;flex-wrap:wrap}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:22px}.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}.head,.body{padding:18px}.head{border-bottom:1px solid var(--line)}.status-row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:10px}.badge{display:inline-block;font-size:12px;letter-spacing:.04em;border:1px solid #607382;background:#233440;border-radius:5px;padding:3px 8px}.bad{color:var(--bad);border-color:#8d5957;background:#352526}.warn{color:var(--warn);border-color:#8c754e;background:#332f23}.stage-controls{display:flex;gap:10px;flex-wrap:wrap;justify-content:space-between;margin-bottom:12px}.stage-controls button{font-size:13px;padding:5px 9px}.image-wrap{position:relative;aspect-ratio:1;background:#0c1014;border:1px solid var(--line);border-radius:8px;overflow:hidden}.image-wrap img{width:100%;height:100%;object-fit:contain;display:block}.empty{height:100%;display:grid;place-content:center;text-align:center;padding:32px;color:var(--muted)}.empty strong{font-size:19px;color:var(--ink);margin-bottom:10px}figure{margin:0}figcaption{font-size:13px;color:var(--muted);padding:9px 0 0;min-height:48px}.prompt{white-space:pre-wrap;background:#101820;padding:12px;border-left:3px solid var(--accent);margin:10px 0}.prompt-label{margin:16px 0 4px;font-weight:650}.links{display:flex;gap:14px;flex-wrap:wrap;border-top:1px solid var(--line);padding-top:14px;margin-top:18px}.unavailable{color:#84929c}details{margin-top:14px}summary{cursor:pointer;color:var(--accent)}details pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:13px}ul{padding-left:22px}.failures{color:var(--bad);font-size:13px}.run-choice{max-width:100%;margin-top:10px}.stage-meta{font-size:13px;color:var(--muted);min-height:22px}footer{font-size:13px;color:var(--muted);padding:24px 0}noscript{display:block;border:1px solid var(--warn);padding:18px;margin:24px}.stage-change{position:absolute;bottom:12px;left:12px;pointer-events:none;background:#101820d9;padding:4px 9px;border-radius:5px;font-size:12px}.hidden{display:none!important}@media(max-width:840px){.grid{grid-template-columns:1fr}.wrap{padding:16px}.tools .wrap{gap:10px}.head,.body{padding:14px}}
</style></head><body>
<header class="wrap"><p class="eyebrow">Open Painter · Material Director · Recorded benchmark outputs</p>
<h1>Five models. Three material instructions each.</h1><p id="comparison" class="muted"></p>
<div class="notice" role="note"><strong>Human scores pending</strong>Independent review of appearance, placement, prompt fulfillment, editability and revision quality is still required. Failed and incomplete runs remain visible. Run completion is an execution status.</div>
<p id="summary" class="muted"></p></header>
<nav class="tools" aria-label="Set every card's view"><div class="wrap"><strong>All cards</strong><div class="buttons" id="global-stages"></div><div class="buttons" id="global-views"></div></div></nav>
<main class="wrap"><div class="grid" id="cards"></div>
<details><summary>Common texturing instruction sent with each prompt</summary><pre id="common"></pre></details>
<footer><a id="manifest">Frozen manifest</a> · <span id="generated"></span><br>Existing PNG files are displayed unchanged. No images are generated, retouched or graded by this gallery. Rerun the generator after more evidence arrives.</footer></main>
<noscript>This local page uses inline JavaScript for stage/view switches. Enable JavaScript to display the five case cards. The frozen manifest and per-run result files remain available in this comparison directory.</noscript>
<script id="gallery-data" type="application/json">__DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('gallery-data').textContent);
const stageNames={before:'Before',create:'Create',refine_1:'Refine 1',refine_2:'Refine 2'};
const viewNames={front:'Front',back:'Back',close:'Close'};
const controllers=[];
const text=(tag,value,cls)=>{const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;};
const button=(label,handler)=>{const el=text('button',label);el.type='button';el.addEventListener('click',handler);return el;};
const badStatus=status=>/FAIL|ERROR|BLOCK|MISMATCH|INVALID/.test(status||'');
function link(parent,url,label,missing){if(url){const a=text('a',label);a.href=url;parent.append(a);}else parent.append(text('span',missing||`${label} unavailable`,'unavailable'));}
function details(parent,label,content){const d=document.createElement('details');d.append(text('summary',label),text('pre',content));parent.append(d);return d;}
function card(spec){
 const node=document.createElement('article');node.className='card';node.setAttribute('aria-label',spec.title);
 const head=text('div','','head'),body=text('div','','body');head.append(text('p',spec.id,'eyebrow'),text('h2',spec.title));
 const statusRow=text('div','','status-row'),badge=text('span','','badge'),runtime=text('span','','muted');statusRow.append(badge,runtime);head.append(statusRow);
 let selected=0,stage=spec.runs[0]?.default_stage||'before',view='front';
 const selector=document.createElement('select');selector.className='run-choice';selector.setAttribute('aria-label',`${spec.title}: recorded run`);
 spec.runs.forEach((run,i)=>{const option=text('option',`${run.run_id} · ${run.status}`);option.value=String(i);selector.append(option);});
 if(spec.runs.length>1){head.append(selector);selector.addEventListener('change',()=>{selected=Number(selector.value);stage=spec.runs[selected].default_stage;render();});}
 const controls=text('div','','stage-controls'),stageButtons=text('div','','buttons'),viewButtons=text('div','','buttons');controls.append(stageButtons,viewButtons);body.append(controls);
 const sb={},vb={};Object.entries(stageNames).forEach(([key,name])=>{sb[key]=button(name,()=>{stage=key;render();});stageButtons.append(sb[key]);});Object.entries(viewNames).forEach(([key,name])=>{vb[key]=button(name,()=>{view=key;render();});viewButtons.append(vb[key]);});
 const figure=document.createElement('figure'),wrap=text('div','','image-wrap'),image=document.createElement('img'),empty=text('div','','empty'),stamp=text('span','','stage-change'),caption=document.createElement('figcaption');image.loading='lazy';wrap.append(image,empty,stamp);figure.append(wrap,caption);body.append(figure);
 const stageMeta=text('div','','stage-meta'),promptLabel=text('p','','prompt-label'),prompt=text('div','','prompt');body.append(stageMeta,promptLabel,prompt);
 const failureBox=text('div','','failures');body.append(failureBox);
 const allPrompts=document.createElement('details');allPrompts.append(text('summary','All three exact benchmark prompts'));Object.entries(spec.prompts).forEach(([key,value])=>{allPrompts.append(text('p',stageNames[key],'prompt-label'),text('div',value,'prompt'));});body.append(allPrompts);
 const sent=document.createElement('details'),sentText=document.createElement('pre');sent.append(text('summary','Exact archived outgoing prompt for selected stage'),sentText);body.append(sent);
 if(spec.requirements.length){const req=document.createElement('details'),list=document.createElement('ul');req.append(text('summary','Requirements awaiting independent review'));spec.requirements.forEach(value=>list.append(text('li',value)));req.append(list);body.append(req);}
 const links=text('div','','links');body.append(links);node.append(head,body);
 function render(){
  const run=spec.runs[selected];Object.keys(sb).forEach(key=>sb[key].setAttribute('aria-pressed',String(stage===key)));Object.keys(vb).forEach(key=>vb[key].setAttribute('aria-pressed',String(view===key)));
  badge.textContent=run?.status||'NOT_SCHEDULED';badge.className='badge'+(badStatus(run?.status)?' bad':run?.status==='COMPLETED'?'':' warn');
  runtime.textContent=run?`${run.run_id}${Number.isFinite(run.elapsed_seconds)?' · '+Math.round(run.elapsed_seconds)+' s':''} · ${run.existing_frames}/10 images present`:'No core run exists in the frozen schedule.';
  const frame=run?.frames[stage]?.[view];image.classList.toggle('hidden',!frame?.url);empty.classList.toggle('hidden',!!frame?.url);empty.replaceChildren();stamp.textContent=`${stageNames[stage]} · ${viewNames[view]}`;
  if(frame?.url){image.src=frame.url;image.alt=`${spec.title}, ${stageNames[stage]}, ${viewNames[view]} — actual recorded output`;image.onerror=()=>{image.classList.add('hidden');empty.classList.remove('hidden');empty.replaceChildren(text('strong','Image could not be loaded'),text('p','The gallery links to the original evidence file; it may have moved after generation.'));};}
  else{image.removeAttribute('src');empty.append(text('strong',frame?.status==='NOT_SCHEDULED'?'View not scheduled':'Evidence missing'),text('p',frame?.message||'No run evidence is available yet.'));}
  caption.replaceChildren();caption.append(text('span',frame?`${frame.status} · ${frame.message}`:'No recorded checkpoint.'));if(frame?.url){caption.append(document.createTextNode(' '));link(caption,frame.url,'Open original PNG');}if(frame?.capture_errors?.length)caption.append(text('p',frame.capture_errors.join('\n'),'failures'));
  const row=run?.stages[stage];stageMeta.textContent=stage==='before'?'Neutral fixture before texturing.':`Stage execution: ${row?.status||'NOT_RUN'}${row?.visible_complete===false?' · visible completion not established':''}`;
  promptLabel.textContent=stage==='before'?'Starting condition':`${stageNames[stage]} · exact benchmark prompt`;prompt.textContent=stage==='before'?'Fresh neutral materials and geometry maps in the fixed Eevee fixture. No texturing prompt has been applied at this stage.':spec.prompts[stage];
  sent.classList.toggle('hidden',!row?.actual_prompt);sentText.textContent=row?.actual_prompt||'';
  failureBox.replaceChildren();const failures=[...(run?.failures||[]),...(run?.warnings||[])];if(failures.length){failureBox.append(text('strong','Recorded failures / evidence issues'));const ul=document.createElement('ul');failures.forEach(value=>ul.append(text('li',value)));failureBox.append(ul);}if(row?.no_op_review)failureBox.append(text('p','This stage requires an independent no-op review; an unchanged material is not automatically a successful revision.'));
  links.replaceChildren();link(links,run?.blend_url,'Open final.blend','final.blend not recorded');link(links,run?.result_url,'Run result.json','result.json unavailable');
 }
 render();controllers.push({setStage(value){stage=value;render();},setView(value){view=value;render();}});return node;
}
data.cards.forEach(spec=>document.getElementById('cards').append(card(spec)));
Object.entries(stageNames).forEach(([key,name])=>document.getElementById('global-stages').append(button(name,()=>controllers.forEach(c=>c.setStage(key)))));
Object.entries(viewNames).forEach(([key,name])=>document.getElementById('global-views').append(button(name,()=>controllers.forEach(c=>c.setView(key)))));
const runs=data.cards.flatMap(c=>c.runs);document.getElementById('comparison').textContent=`${data.comparison} · Fixed Eevee studio · Original 1024 × 1024 evidence`;
document.getElementById('summary').textContent=`${data.cards.length} cases · ${runs.length} scheduled core runs · ${runs.filter(r=>r.status==='COMPLETED').length} completed executions · ${runs.filter(r=>badStatus(r.status)).length} failed/blocked executions · Quality unscored`;
document.getElementById('common').textContent=data.common_instruction;document.getElementById('manifest').href=data.manifest_url;document.getElementById('generated').textContent=`Gallery snapshot generated ${data.generated_at}`;
</script></body></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("comparison", type=Path, help="Existing frozen comparison directory")
    parser.add_argument("--output", type=Path, help="Default: <comparison>/gallery.html")
    args = parser.parse_args()
    root = args.comparison.resolve()
    output = (args.output or root / "gallery.html").resolve()
    if output.suffix.lower() != ".html":
        parser.error("Gallery output must be an .html file")
    data = collect(root, output)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
    print(output)
    print(f"{len(data['cards'])} actual case cards; human scores pending; original image bytes unchanged")


if __name__ == "__main__":
    main()
