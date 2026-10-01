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
    lines = ["# Material Director: five-asset Eevee benchmark", "",
        f"Comparison: **{summary['comparison_id']}**. Protocol: `{summary['protocol']}`.", "",
        f"**{summary['core_scheduled']} scheduled texturing sessions**, with "
        f"{summary.get('ablation_scheduled', 0)} correction-off ablation sessions and "
        f"{summary['robustness_scheduled']} optional robustness scenarios. Every attempt is retained.", "",
        "Only material appearance, spatial placement, prompt compliance, editability and revision fidelity receive points. "
        "Eevee setup, importing, lighting and camera configuration are prerequisites.", "",
        "## Frozen environment", "",
        f"- Renderer: **{config['render_engine']}**, 1024 × 1024 renders; material maps: {config['output_resolution']}².",
        f"- Model: `{config['provider']['model_id']}`; returned: `{config['provider']['returned_model_id']}`.",
        f"- Runtime: `{config['provider']['runtime_version']}`; Blender: {_text(config['environment']['blender_version'])}.",
        f"- Source SHA-256: `{config['build']['source_sha256']}`; library: `{config['environment']['material_library_sha256']}`.",
        f"- Hardware: {_text(config['environment']['hardware'])}.", "",
        f"- Execution policy: {config.get('execution', {}).get('policy', 'SERIAL')}, "
        f"at most {config.get('execution', {}).get('jobs', 1)} simultaneous sessions. Shared-device timings include contention.", "",
        f"- Harness caps: {_text(config.get('limits'))}; ordinary product deadlines: {_text(config.get('product_deadlines', 'Historical policy'))}.",
        "Native/hybrid SET requests do not have equal effective budgets: the ordinary native deadline is 180 seconds, "
        "while hybrid's 900-second deadline is bounded by the frozen harness cap. Baseline/improved budgets match within each mode and scope.", "",
        "## Results by mode", "",
        "| Mode | Confirmed autonomous successes / scheduled | Reviewed final mean | Generated-input sessions | Intervention classes |",
        "|---|---:|---:|---:|---|"]
    for mode, data in summary["modes"].items():
        lines.append(f"| {mode} | {data['autonomous_successes']} / {data['scheduled']} | "
                     f"{_score(data['conditional_final_quality']['mean'])} | "
                     f"{data['actual_generated_input_subset']['count']} | {_text(data['interventions'])} |")
    if summary.get("comparison_groups"):
        lines.extend(["", "## Paired product builds and correction ablation", "",
            "Human ratings remain pending until supplied by an identified independent reviewer. "
            "Deterministic calibration is a separate engineering track, not a human-authored aesthetic reference.", "",
            "| Build / mode / correction | Planned | Attempted | Assessable appearance | Workflow passes | Initial quality | Final quality |",
            "|---|---:|---:|---:|---:|---:|---:|"])
        for name, group in summary["comparison_groups"].items():
            lines.append(f"| {name} | {group['planned']} | {group['attempted']} | {group['assessable_appearance']} | "
                         f"{group['workflow_passes']} | {_score(group['initial_quality']['mean'])} | {_score(group['final_quality']['mean'])} |")
    lines.extend(["", "Zero corrections is a success only when the actual material requirements pass. "
                  "One correction means minor assistance; two significant assistance; three or more a failed autonomous workflow. "
                  "Unknown counts remain unknown. Predefined Create/Refine prompts are test instructions, not extra artist corrections.", "",
                  "## Capability matrix", "",
                  "| Case | Mode | Autonomous successes / scheduled | Individual final scores | Median (range) | Interventions |",
                  "|---|---|---:|---|---|---|"])
    for mode, data in summary["modes"].items():
        for case_id, case in data["cases"].items():
            stats = case["final_quality"]
            values = ", ".join(_score(item["score"]) for item in case["individual"])
            range_text = "NOT SCORED" if stats["range"] is None else "–".join(_score(value) for value in stats["range"])
            counts = ", ".join("UNKNOWN" if item["interventions"] is None else str(item["interventions"]) for item in case["individual"])
            lines.append(f"| {case_id} | {mode} | {case['autonomous_successes']} / {case['scheduled']} | "
                         f"{values} | {_score(stats['median'])} ({range_text}) | {counts} |")
    entries = {entry["run_id"]: entry for entry in manifest["schedule"]}
    lines.extend(["", "## Every texturing session", "",
                  "| Order | Session / evidence | Execution | Create | Refine 1 | Final | Corrections | Decision |",
                  "|---:|---|---|---:|---:|---:|---:|---|"])
    for record in summary["core_results"] + summary.get("ablation_results", []):
        path = entries[record["run_id"]]["relative_directory"] + "/result.json"
        stages = record["stages"]
        count = record["artist_intervention_count"]
        lines.append(f"| {record['order']} | [{record['run_id']}]({path}) | {record['status']} | "
                     f"{_score(stages['create']['review']['score'])} | {_score(stages['refine_1']['review']['score'])} | "
                     f"{_score(record['final_score'])} | {'UNKNOWN' if count is None else count} | {record['verdict']} |")
    lines.extend(["", "## Failure and availability records", ""])
    if not summary["failures"]:
        lines.append("No recorded failures. Unrun/unreviewed sessions are not passes.")
    for record in summary["failures"]:
        lines.extend([f"### {record['run_id']}: {record['verdict']}", "", record.get("reason") or "See the recorded evidence.", ""])
        for failure in record.get("hard_failures", []) + record.get("failures", []):
            lines.append("- " + _text(json.dumps(failure, ensure_ascii=False) if isinstance(failure, dict) else failure))
        lines.append("")
    lines.extend(["## Optional supplemental checks", "", _text(summary["supplements"]), ""])
    for record in summary["robustness_results"]:
        path = entries[record["run_id"]]["relative_directory"] + "/result.json"
        lines.append(f"- [{record['run_id']}]({path}): {record['verdict']}.")
    lines.extend(["", "## Limits of the evidence", ""])
    lines.extend("- " + item for item in summary["limitations"])
    limitations = {f"{record['run_id']} / {name}: {item}"
                   for record in summary["core_results"] + summary.get("ablation_results", []) for name, stage in record["stages"].items()
                   for item in stage["review"].get("limitations", [])}
    lines.extend("- " + item + "." for item in sorted(limitations))
    lines.extend(["", "The JSON report retains all stage scores, checks, generated-input counts and measured timing/cost fields. "
                  "Do not publish the private run key with anonymous reviewer packets.", ""])
    return "\n".join(lines)


def write_report(directory):
    directory = Path(directory)
    if _read(directory / "manifest.json", {}).get("schema_version") == 1:
        from .legacy.report import write_report as write_legacy
        return write_legacy(directory)
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
    if _read(directory / "manifest.json", {}).get("schema_version") == 1:
        from .legacy.report import export_review_packets as export_legacy
        return export_legacy(directory)
    manifest, results, _controls, _reviews = collect(directory)
    cases = {case["id"]: case for case in manifest["specification"]["cases"]}
    entries = sorted((entry for entry in manifest["schedule"] if entry["kind"] == "core"),
                     key=lambda entry: entry["review_id"])
    destination = directory / "review_packets"
    destination.mkdir(exist_ok=True)
    packets = []
    for entry in entries:
        result = results.get(entry["run_id"], {})
        from .outcomes import adapt_result
        result = adapt_result(result)
        run_directory = directory / entry["relative_directory"]
        copied, missing, stage_outcomes = [], [], {}
        for name, stage in stage_map(result).items():
            if name not in {"create", "refine_1", "refine_2"}:
                continue
            stage_outcomes[name] = {key: stage.get(key) for key in ("execution", "evidence", "quality")}
            if stage.get("execution", {}).get("state") == "SKIPPED_DEPENDENCY":
                continue
            checkpoint = stage.get("checkpoint") or stage.get("evidence", {}).get("checkpoint")
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
        packet = {"review_id": entry["review_id"], "views": copied, "missing_stages": missing, "stage_outcomes": stage_outcomes,
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
            "scheduled": len(entries), "missing_packets": len(entries) - len(packets)}
