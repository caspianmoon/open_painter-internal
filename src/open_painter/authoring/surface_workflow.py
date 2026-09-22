"""Structured planning and visual critique gates; never automatic artistic scoring."""
from . import schema

TEXT = dict(type="string", minLength=8, maxLength=1200)
COMPONENT = schema.object_schema(dict(
    name=schema.NAME, texture_set_id=schema.IDENTIFIER, region_id=schema.IDENTIFIER,
    substrate=TEXT, history=TEXT, macro=TEXT, meso=TEXT, micro=TEXT,
    generated_source=TEXT, mapping=TEXT, authored_detail=TEXT),
    ("name", "texture_set_id", "substrate", "history", "macro", "meso", "micro",
     "generated_source", "mapping", "authored_detail"))
PLAN_SCHEMA = schema.object_schema(dict(object_interpretation=TEXT, art_direction=TEXT,
    components=dict(type="array", items=COMPONENT, minItems=1, maxItems=12)),
    ("object_interpretation", "art_direction", "components"))
WEAKNESS = schema.object_schema(dict(component=schema.NAME, observation=TEXT, correction=TEXT,
    evidence_image_id=schema.IDENTIFIER), ("component", "observation", "correction", "evidence_image_id"))
CRITIQUE_SCHEMA = schema.object_schema(dict(candidate_digest=dict(type="string", minLength=64, maxLength=64),
    weaknesses=dict(type="array", items=WEAKNESS, minItems=0, maxItems=12),
    generated_contribution=TEXT, preserved_decisions=TEXT),
    ("candidate_digest", "weaknesses", "generated_contribution", "preserved_decisions"))


def enabled(captured):
    return bool(captured.get("context", {}).get("image_generation", {}).get("enabled")) and captured.get("mode") != "EXPLAIN"


def store_plan(captured, plan):
    identifiers = set(captured["target_digests"])
    if any(component["texture_set_id"] not in identifiers for component in plan["components"]):
        raise schema.AuthoringError("Surface plan contains a material outside this instruction")
    captured["surface_plan"] = plan
    # A changed plan must not inherit a critique of a different intent.
    if captured.get("candidate"):
        captured["candidate"].pop("critique", None)
    return dict(recorded=True, components=len(plan["components"]),
                next_step="Generate/assign the planned surface sources, then preview a complete editable candidate. Markings must be fictional by default.")


def critique(captured, arguments):
    candidate = captured.get("candidate")
    if not candidate or arguments["candidate_digest"] != candidate["digest"]:
        raise schema.AuthoringError("Critique the current candidate digest; earlier images cannot approve a new material")
    image_ids = {image["id"] for image in candidate["result"]["images"]}
    if any(item["evidence_image_id"] not in image_ids for item in arguments["weaknesses"]):
        raise schema.AuthoringError("Each weakness must cite an image returned for this candidate")
    if enabled(captured) and captured["preview_count"] == 1 and not arguments["weaknesses"]:
        raise schema.AuthoringError("The initial generated-surface review needs a concrete visual improvement; inspect closeups, channels and generated-source bypass before correcting")
    candidate["critique"] = arguments
    captured.setdefault("visual_critiques", []).append(arguments)
    remaining = candidate["result"]["remaining_corrections"]
    return dict(recorded=True, remaining_corrections=remaining,
                next_step="Correct these weaknesses in a complete replacement proposal and preview again"
                if arguments["weaknesses"] and remaining else "Submit the reviewed candidate and disclose any unresolved weaknesses",
                artistic_verdict="Not assigned; numerical diagnostics and this critique are not independent human validation")


def require_review(captured):
    if not enabled(captured):
        return
    candidate = captured.get("candidate") or {}
    critique = candidate.get("critique")
    if not critique:
        raise schema.AuthoringError("Call critique_candidate with concrete image findings before committing generated surfaces")
    if critique["weaknesses"] and candidate["result"]["remaining_corrections"]:
        raise schema.AuthoringError("This candidate has unaddressed visual weaknesses; correct it and preview again before submitting")
