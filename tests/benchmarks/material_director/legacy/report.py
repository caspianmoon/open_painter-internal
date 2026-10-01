"""Assemble all scheduled outcomes; this is not an AI aesthetic reviewer."""

import json
from pathlib import Path
import shutil

from .manifest import file_sha256, load_manifest, review_template
from .scoring import stage_map, summarize


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def collect(directory):
    directory = Path(directory)
    manifest = load_manifest(directory)
    results, reviews, controls = {}, {}, {}
    for entry in manifest["schedule"]:
        path = directory / entry["relative_directory"] / "result.json"
        if path.exists():
            results[entry["run_id"]] = _read(path, {})
        if entry["kind"] == "core":
            path = directory / "reviews" / f"{entry['review_id']}.json"
            if path.exists():
                reviews[entry["review_id"]] = _read(path, {})
    for case in manifest["specification"]["cases"]:
        for kind in ("preset", "artist"):
            key = f"{case['id']}-{kind}"
            path = directory / "controls" / f"{key}.json"
            if path.exists():
                controls[key] = _read(path, {})
    return manifest, results, controls, reviews


def _text(value):
    if value is None:
        return "NOT SCORED"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _score(value):
    return f"{value:.1f}" if value is not None else "NOT SCORED"


def markdown(summary, manifest):
    config = summary["configuration"]
    lines = [
        "# Material Director benchmark report", "",
        f"Comparison: **{_text(summary['comparison_id'])}**. "
        "Every scheduled result is listed, including failed, blocked, unavailable and unrun sessions.", "",
        f"Core sessions: **{summary['core_scheduled']}**. "
        f"Separate robustness scenarios: **{summary['robustness_scheduled']}**. "
        "Resolution checks at 4K are not independent generative samples.", "",
        "## Frozen comparison", "",
        f"- Open Painter commit: `{config['build']['commit']}`; source SHA-256: `{config['build']['source_sha256']}`.",
        f"- Provider/runtime: {_text(config['provider']['name'])} / {_text(config['provider']['runtime_version'])}.",
        f"- Requested and returned model: `{config['provider']['model_id']}` / `{config['provider']['returned_model_id']}`.",
        f"- Provider seed setting: {_text(config['provider']['seed_setting'])}; "
        f"pilot image generation available: {_text(config['provider']['image_generation_available'])}.",
        f"- Blender/hardware: {_text(config['environment']['blender_version'])} / {_text(config['environment']['hardware'])}.",
        f"- Render/evaluator backend: {_text(config['environment']['render_backend'])} / {_text(config['environment']['evaluator_backend'])}.",
        f"- Material library SHA-256: `{config['environment']['material_library_sha256']}`.",
        f"- Predeclared value claim: **{config['value_claim']}**. "
        "Quality requires +10 points; efficiency requires no worse than −5 points with at least 50% less active operator time.",
        f"- Qualified artist baseline: **{config['artist_baseline']['status']}**. "
        f"{_text(config['artist_baseline'].get('reason', ''))}", "",
        "## Decision by mode", "",
        "| Mode | Core passes / scheduled | Robustness passes / scheduled | Delivery gate | Dependable workflow | Value gate | Useful beta |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for mode, data in summary["modes"].items():
        gates = data["gates"]
        lines.append(f"| {mode} | {data['passed']} / 24 | {data['robustness']['passed']} / 8 | "
                     f"{gates['delivery']} | {gates['dependable_workflow']} | "
                     f"{gates['predeclared_value']} | {gates['useful_beta']} |")
    lines.extend(["", "A pending gate is not a pass. Blocked/unavailable runs remain in the delivery denominator; "
                  "their missing images receive no invented aesthetic score.", "",
                  "## Capability matrix", "",
                  "| Case | Mode | Passes / 3 | Three individual scores | Final median (range) | First-pass mean | Matched preset comparison |",
                  "|---|---|---:|---|---|---:|---|"])
    for mode, data in summary["modes"].items():
        for case_id, case in data["cases"].items():
            stats = case["conditional_final_quality"]
            scores = ", ".join(_score(record["score"]) for record in case["individual"])
            range_text = "—" if stats["range"] is None else "–".join(_score(value) for value in stats["range"])
            comparison = case["value_comparison"]
            lines.append(f"| {case_id} | {mode} | {case['passed']} / 3 | {scores} | "
                         f"{_score(stats['median'])} ({range_text}) | {_score(case['first_pass_quality']['mean'])} | "
                         f"{comparison['status']}: {_text(comparison.get('reason', ''))} |")
    lines.extend(["", "## Generated-input subset", ""])
    for mode, data in summary["modes"].items():
        subset = data["actual_generated_input_subset"]
        lines.append(f"- {mode}: {subset['count']} sessions actually assigned generated inputs; "
                     f"{subset['passed']} passed. Conditional mean score: "
                     f"{_score(subset['conditional_final_quality']['mean'])}. "
                     f"Generative capability blocked in {data['generation_blocked']} sessions.")
    lines.extend(["", "## Delivery and workflow evidence", "",
                  "| Mode | Check | Passed | Failed | Pending | Scheduled |",
                  "|---|---|---:|---:|---:|---:|"])
    for mode, data in summary["modes"].items():
        for name, outcome in data["workflow_outcomes"].items():
            lines.append(f"| {mode} | {name} | {outcome['passed']} | {outcome['failed']} | "
                         f"{outcome['pending']} | {outcome['scheduled']} |")
    lines.extend(["", "| Mode | Stage | Visible deliveries / scheduled | Numerically acceptable / independently scored |",
                  "|---|---|---:|---:|"])
    for mode, data in summary["modes"].items():
        for name, outcome in data["stage_outcomes"].items():
            lines.append(f"| {mode} | {name} | {outcome['delivered']} / {outcome['scheduled']} | "
                         f"{outcome['numeric_acceptance']} / {outcome['independently_scored']} |")
    lines.extend(["", "Numeric acceptance is separate from binary acceptance and handoff evidence. "
                  "A visible delivery alone is not a quality pass.", "",
                  "## Timing measurements", "",
                  "| Mode | Measurement | Measured sessions | Mean seconds | Range seconds |",
                  "|---|---|---:|---:|---|"])
    for mode, data in summary["modes"].items():
        for name, metric in data["timings"].items():
            lines.append(f"| {mode} | {name} | {metric['count']} | {_score(metric['mean'])} | "
                         f"{_text(metric['range'])} |")
    entries = {entry["run_id"]: entry for entry in manifest["schedule"]}
    lines.extend(["", "## Every core session in frozen order", "",
                  "| Order | Session / evidence | Status | Create score | Refine 1 score | Final score | Verdict |",
                  "|---:|---|---|---:|---:|---:|---|"])
    for record in summary["core_results"]:
        entry = entries[record["run_id"]]
        path = entry["relative_directory"] + "/result.json"
        stages = record["stages"]
        lines.append(f"| {record['order']} | [{record['run_id']}]({path}) | {record['status']} | "
                     f"{_score(stages['create']['review']['score'])} | {_score(stages['refine_1']['review']['score'])} | "
                     f"{_score(record['final_score'])} | {record['verdict']} |")
    lines.extend(["", "## Every robustness scenario", "",
                  "| Order | Scenario / evidence | Status | Safety verdict | Reason |",
                  "|---:|---|---|---|---|"])
    for record in summary["robustness_results"]:
        path = entries[record["run_id"]]["relative_directory"] + "/result.json"
        lines.append(f"| {record['order']} | [{record['run_id']}]({path}) | {record['status']} | "
                     f"{record['verdict']} | {_text(record['reason'])} |")
    lines.extend(["", "## Fixed repeat-1 resolution checks", "",
                  "These reuse the final stack at 4K without new AI instructions.", "",
                  "| Session | Status | Evidence / limitation |", "|---|---|---|"])
    for record in summary["resolution_checks"]:
        result = record["result"]
        lines.append(f"| {record['run_id']} | {_text(result.get('status', 'NOT_RUN'))} | "
                     f"{_text(result.get('evidence', result.get('reason', '')))} |")
    lines.extend(["", "## All failures and unavailable results", ""])
    if not summary["failures"]:
        lines.append("No recorded failures. This does not establish success for unrun or unreviewed sessions.")
    for record in summary["failures"]:
        lines.extend([f"### {record['run_id']}: {record['verdict']}", "", _text(record["reason"]) or "No reason recorded.", ""])
        for failure in record["hard_failures"] + record["failures"]:
            lines.append("- " + _text(json.dumps(failure, ensure_ascii=False) if isinstance(failure, dict) else failure))
        if record["hard_failures"] or record["failures"]:
            lines.append("")
    lines.extend(["## Interpretation and untested work", ""])
    lines.extend("- " + limitation for limitation in summary["limitations"])
    for engine, status in summary["engine_validation"].items():
        lines.append(f"- {engine}: {status}.")
    reviewer_limitations = {limitation for record in summary["core_results"]
                           for stage in record["stages"].values()
                           for limitation in stage["review"].get("limitations", [])}
    for limitation in sorted(reviewer_limitations):
        lines.append("- " + limitation + ".")
    lines.extend(["", "Independent reviewers must use the anonymized review packets. "
                  "Do not publish `review_key.private.json` with those packets. "
                  "The machine-readable report retains stage scores, preservation/handoff checks, all individual failures, "
                  "availability, timing, measured costs and generated-input counts separately.", ""])
    return "\n".join(lines)


def write_report(directory):
    directory = Path(directory)
    manifest, results, controls, reviews = collect(directory)
    summary = summarize(manifest, results, controls, reviews)
    (directory / "report.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    (directory / "report.md").write_text(markdown(summary, manifest), encoding="utf-8")
    return summary


def export_review_packets(directory):
    """Copy captured PNG views behind anonymous IDs, excluding mode/model/tool metadata.

    Review forms stay separate from private run keys. This does not score images
    or imply a missing view was captured. Existing reviewer answers are not replaced.
    """
    directory = Path(directory).resolve()
    manifest, results, _controls, _reviews = collect(directory)
    cases = {case["id"]: case for case in manifest["specification"]["cases"]}
    entries = sorted((entry for entry in manifest["schedule"] if entry["kind"] == "core"),
                     key=lambda entry: entry["review_id"])
    destination = directory / "review_packets"
    destination.mkdir(exist_ok=True)
    packets = []
    for entry in entries:
        result = results.get(entry["run_id"], {})
        run_directory = directory / entry["relative_directory"]
        copied, missing = [], []
        for name, stage in stage_map(result).items():
            if name not in {"create", "refine_1", "refine_2"}:
                continue
            checkpoint = stage.get("checkpoint")
            if not checkpoint:
                missing.append(name)
                continue
            checkpoint_path = (run_directory / checkpoint).resolve()
            if not checkpoint_path.is_relative_to(run_directory.resolve()):
                raise ValueError("Review checkpoint must be inside its scheduled run directory")
            document = _read(checkpoint_path, {})
            views = document.get("views", {}).get("views", [])
            if not views:
                missing.append(name)
            for index, view in enumerate(views, 1):
                source = Path(view["path"])
                source = source.resolve() if source.is_absolute() else (run_directory / source).resolve()
                if not source.is_relative_to(run_directory.resolve()) or source.suffix.lower() != ".png":
                    raise ValueError("Only captured PNGs inside their scheduled run directory enter a review packet")
                if not source.is_file() or file_sha256(source) != view.get("sha256"):
                    raise ValueError("Review image is missing or changed since evidence capture")
                relative = Path(entry["review_id"]) / name / f"view-{index:02d}.png"
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and file_sha256(target) != view["sha256"]:
                    raise ValueError("An anonymous packet already contains different evidence")
                if not target.exists():
                    shutil.copyfile(source, target)
                copied.append({"stage": name, "path": relative.as_posix(),
                               "camera": view.get("camera"), "distance": view.get("distance"),
                               "sha256": view["sha256"]})
        if not copied:
            continue
        form_path = destination / entry["review_id"] / "review.json"
        if not form_path.exists():
            form = review_template(entry, cases[entry["case_id"]], manifest["specification"])
            form_path.write_text(json.dumps(form, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        packet = {"review_id": entry["review_id"], "views": copied, "missing_stages": missing,
                  "review_form": f"{entry['review_id']}/review.json"}
        packets.append(packet)
    (destination / "index.json").write_text(json.dumps({"packets": packets}, indent=2) + "\n", encoding="utf-8")
    (destination / "README.md").write_text(
        "# Anonymous material review\n\n"
        "Review packets are in randomized order and omit mode, model and provider traces. "
        "Rate each stage using its supplied brief, normal and close views, then record the evidence "
        "for binary checks. Visual renders alone cannot establish editable structure or handoff; "
        "leave those judgments pending until the evaluator supplies the corresponding blind evidence.\n\n"
        "Record your reviewer ID, independent status, role and evidence in review.json. "
        "Return the file to the evaluator for merging into reviews/<review_id>.json. "
        "The generating AI cannot be the final judge. Do not obtain or distribute the private run key.\n", encoding="utf-8")
    return {"directory": str(destination), "packets": len(packets),
            "scheduled": 48, "missing_packets": 48 - len(packets)}
