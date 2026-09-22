"""Prepare, run and package Weapon_Test_Guide.md. Live attempts are never overwritten.

python3 scripts/benchmark_weapons.py prepare
python3 scripts/benchmark_weapons.py run --use-codex-login
python3 scripts/benchmark_weapons.py report
"""
import argparse
import html
import json
import os
import shutil
import signal
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "tests/benchmarks/material_director"
sys.path.insert(0, str(HERE))
from runtime import isolated_login, redact, run_blender, sha256, source_fingerprint, write_json
from weapon_protocol import unattempted_stages

WEAPONS = ("assault_rifle", "smg")
VIEWS = ("left", "right", "front", "rear", "close")


def extract(pack, output):
    """Bounded extraction with traversal, symlink and downloaded-file checks."""
    with zipfile.ZipFile(pack) as archive:
        members = archive.infolist()
        if len(members) > 100 or sum(m.file_size for m in members) > 100 * 1024**2:
            raise ValueError("Unexpected source archive size")
        for member in members:
            path = PurePosixPath(member.filename.replace("\\", "/"))
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != "open_painter_weapon_test"
                    or any(":" in part for part in path.parts)
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe archive member")
        if archive.testzip():
            raise ValueError("Source archive CRC failure")
        archive.extractall(output)
    root = output / "open_painter_weapon_test"
    manifest = json.loads((root / "manifest.json").read_text())
    if {a["folder"] for a in manifest["assets"]} != set(WEAPONS):
        raise ValueError("Expected exactly the two weapon assets")
    for asset in manifest["assets"]:
        for record in asset["files"]:
            path = (root / asset["folder"] / record["path"]).resolve()
            if not path.is_relative_to(root.resolve()) or sha256(path) != record["sha256"]:
                raise ValueError("Source file checksum/path mismatch")
        original = root / "sources" / asset["url"].rsplit("/", 1)[1]
        if sha256(original) != asset["source_archive_sha256"]:
            raise ValueError("Downloaded archive checksum mismatch")
    return root


def prepare(args):
    root = extract(args.pack, args.output / "sources")
    fixtures = args.output / "fixtures"
    fixtures.mkdir(exist_ok=True)
    command = [args.blender, "--background", "--factory-startup", "--disable-autoexec",
               "--python-exit-code", "1", "--python", str(HERE / "prepare_weapons.py"),
               "--", str(root), str(fixtures), str(args.rig)]
    with tempfile.TemporaryDirectory(prefix="weapon-prepare-") as profile:
        code = run_blender(command, env=dict(os.environ, BLENDER_USER_RESOURCES=profile),
                           timeout=600, log=args.output / "preparation.log")
    if code:
        raise RuntimeError("Preparation failed; see preparation.log")


def run(args):
    if not args.use_codex_login:
        raise ValueError("Live inference requires --use-codex-login")
    binary = shutil.which(args.codex)
    if not binary:
        raise ValueError("Codex executable not found")
    build = source_fingerprint()
    write_json(args.output / "execution_status.json", dict(status="RUNNING", model=args.model,
                                                          generated_textures=True))
    for weapon in WEAPONS:
        out = args.output / weapon
        out.mkdir(exist_ok=True)
        fixture = args.output / "fixtures" / (weapon + ".blend")
        audit = json.loads(fixture.with_suffix(".json").read_text())
        if audit["status"] != "READY" or sha256(fixture) != audit["sha256"]:
            raise ValueError("Fixture is not frozen and verified")
        if (out / "attempt.json").exists():
            attempted = json.loads((out / "attempt.json").read_text())
            if (attempted["model"] != args.model or not attempted["generated_textures"]
                    or attempted.get("surface_sources", False) != args.surface_sources):
                raise ValueError("Cannot change provider settings between weapons")
            print("RETAINED_EXISTING_ATTEMPT", weapon, flush=True)
            continue
        with isolated_login(minimum_seconds=4000) as auth:
            job = dict(run_id=weapon, case=dict(id=weapon, create="", refinements=[]),
                       common_instruction="", mode="H", protocol="two_weapon_v1", surface_sources=args.surface_sources,
                       fixture=str(fixture), fixture_sha256=sha256(fixture), output=str(out),
                       model_id=args.model, build=build, evidence=dict(primary_evidence=True),
                       limits=dict(single_asset_turn_seconds=1200, multi_set_turn_seconds=1200,
                                   session_seconds=3900))
            with (out / "attempt.json").open("x") as handle:
                json.dump(dict(model=args.model, generated_textures=True, surface_sources=args.surface_sources,
                               fixture_sha256=job["fixture_sha256"]), handle)
            write_json(out / "job.json", job)
            with tempfile.TemporaryDirectory(prefix="weapon-live-") as profile:
                control = Path(profile) / "owned-process.json"
                env = dict(os.environ, BLENDER_USER_RESOURCES=profile,
                           OP_MD_BENCHMARK_JOB=str(out / "job.json"), OP_MD_BENCHMARK_AUTH=str(auth),
                           OP_MD_BENCHMARK_CODEX=binary, OP_MD_BENCHMARK_CONTROL=str(control))
                command = [args.blender, "--factory-startup", "--disable-autoexec", "--online-mode",
                           "--python-exit-code", "1", "--python", str(HERE / "run_weapon_session.py")]
                if sys.platform == "darwin":
                    command += ["--", "-ApplePersistenceIgnoreState", "YES"]
                try:
                    code = run_blender(command, env=env, timeout=4000, log=out / "blender.log")
                    if code:
                        write_json(out / "process_failure.json", dict(returncode=code, status="FAILED"))
                except Exception as exc:
                    write_json(out / "process_failure.json", dict(status="FAILED", error=redact(str(exc))))
                finally:
                    if control.exists() and os.name != "nt":
                        try:
                            os.killpg(json.loads(control.read_text())["companion_pid"], signal.SIGTERM)
                        except ProcessLookupError:
                            pass
            result_path = out / "result.json"
            result = json.loads(result_path.read_text()) if result_path.exists() else dict(stages=[])
            if result.get("status") in {None, "RUNNING"}:
                result["status"] = "FAILED"
                result.setdefault("failures", []).append(dict(category="process",
                    message="Blender exited without terminal evidence; missing captures are unavailable"))
                for stage in result["stages"]:
                    if stage.get("status") == "RUNNING":
                        stage.update(status="FAILED", evidence=dict(state="CAPTURE_FAILED"))
                names = {stage["name"] for stage in result["stages"]}
                result["stages"].extend(s for s in unattempted_stages("Skipped after process failure", args.surface_sources)
                                        if s["name"] not in names)
                write_json(result_path, redact(result))
            print("WEAPON_ATTEMPT_FINISHED", weapon, flush=True)
    write_json(args.output / "execution_status.json", dict(status="ATTEMPTS_FINISHED", model=args.model,
                                                          generated_textures=True))
    report(args)


def report(args):
    escape = html.escape
    review_path = args.output / "review.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else {}
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>Weapon material review</title>',
             '<style>body{font:16px system-ui;background:#17191c;color:#eee;margin:2rem}table{width:100%;table-layout:fixed}td{vertical-align:top;padding:1rem}img{width:100%}pre{white-space:pre-wrap}a{color:#9cd8ff}</style>',
             '<h1>Two-weapon Material Director test</h1><p>Development review. No independent human validation. Eevee only; source scale retained. Images and checkpoints are included in this ZIP.</p>']
    execution = args.output / "execution_status.json"
    if execution.exists():
        parts.append('<pre>' + escape(execution.read_text()) + '</pre>')
    if review:
        parts.append('<pre>' + escape(json.dumps(review, indent=2)) + '</pre>')
    else:
        parts.append('<p>Visual assessment pending. No artistic score or game-readiness verdict has been assigned.</p>')
    results = {}
    for weapon in WEAPONS:
        path = args.output / weapon / "result.json"
        results[weapon] = json.loads(path.read_text()) if path.exists() else dict(
            status="NOT_RUN", stages=unattempted_stages("Live session has not been started", args.surface_sources))
    parts.append('<table><tr><th>Assault rifle</th><th>SMG</th></tr>')
    for label in ("before", "after_create", "after_refine_1", "after_refine_2",
                  "failure_create", "failure_refine_1", "failure_refine_2"):
        if not any((args.output / w / (label + "_state.json")).exists() for w in WEAPONS) and label != "before":
            continue
        parts.append('<tr>')
        for weapon in WEAPONS:
            parts.append('<td><h2>' + escape(label) + '</h2>')
            blend = f"{weapon}/{label}.blend"
            if label == "before" and not (args.output / blend).exists():
                blend = f"fixtures/{weapon}.blend"
            if (args.output / blend).exists():
                parts.append(f'<a href="{blend}">Editable .blend checkpoint</a>')
            else:
                parts.append('<p>State not captured / not run.</p>')
            for view in VIEWS:
                path = f"{weapon}/{label}_{view}.png"
                if label == "before" and not (args.output / path).exists():
                    path = f"preflight/{weapon}/{label}_{view}.png"
                if (args.output / path).exists():
                    parts.append(f'<figure><figcaption>{view}</figcaption><a href="{path}"><img loading="lazy" src="{path}" alt="{weapon} {label} {view}"></a></figure>')
            parts.append('</td>')
        parts.append('</tr>')
    parts.append('</table>')
    for weapon, result in results.items():
        parts.append('<h2>' + weapon + ': ' + escape(result["status"]) + '</h2>')
        for stage in result["stages"]:
            parts.append('<h3>' + escape(stage["name"] + ': ' + stage["status"]) + '</h3><pre>' +
                         escape(stage.get("prompt", "") + '\n\n' + stage.get("response", "") + '\n' + stage.get("status_message", "")) + '</pre>')
        parts.append('<pre>' + escape(json.dumps(result.get("failures", []), indent=2)) + '</pre>')
    parts.append('</html>')
    (args.output / "index.html").write_text('\n'.join(parts))
    shutil.copyfile(ROOT / "Weapon_Test_Guide.md", args.output / "TEST_GUIDE.md")
    source = args.output / "sources/open_painter_weapon_test"
    shutil.copyfile(source / "manifest.json", args.output / "SOURCE_MANIFEST.json")
    shutil.copyfile(source / "SOURCES_AND_LICENSE.md", args.output / "SOURCES.md")
    allowed = {".png", ".blend", ".json", ".jsonl", ".md", ".html"}
    files = [p for p in sorted(args.output.rglob('*')) if p.is_file() and p.suffix in allowed
             and (len(p.relative_to(args.output).parts) == 1
                  or p.relative_to(args.output).parts[0] in {*WEAPONS, "fixtures", "preflight"})
             and p.name != "DELIVERY_MANIFEST.json"]
    write_json(args.output / "DELIVERY_MANIFEST.json", {str(p.relative_to(args.output)): sha256(p) for p in files})
    package = args.output / "Weapon_Test_Review.zip"
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in [*files, args.output / "DELIVERY_MANIFEST.json"]:
            archive.write(path, path.relative_to(args.output))
    with zipfile.ZipFile(package) as archive:
        if archive.testzip():
            raise ValueError("Delivery CRC failure")
    print(package, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "report"))
    parser.add_argument("--output", type=Path, default=ROOT / ".blender-test/weapon-test")
    parser.add_argument("--pack", type=Path, default=ROOT / "Open_Painter_Weapon_Test.zip")
    parser.add_argument("--rig", type=Path, default=ROOT / ".blender-test/material-director-benchmark/fixtures-eevee-v1/_infrastructure/benchmark_rig.blend")
    parser.add_argument("--blender", default="/Applications/Blender.app/Contents/MacOS/Blender")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", default="gpt-6-astra")
    parser.add_argument("--use-codex-login", action="store_true")
    parser.add_argument("--surface-sources", action="store_true", help="Use the revised two-prompt generated-surface protocol")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    globals()[args.action](args)


if __name__ == "__main__":
    main()
