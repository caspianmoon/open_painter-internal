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
CORRESPONDENCE_ISSUES = ('UV_SEAM', 'PATTERN_CONTINUITY', 'FEATURE_TERMINATION', 'DETAIL_SCALE',
    'DIRECTION', 'MIRRORING', 'STRETCHING', 'VIEW_DEPENDENT', 'ROUGHNESS_SEAM', 'RELIEF_SEAM', 'MATERIAL_LEAKAGE')
# Surface-space defects are not texture-source defects; they are corrected by observing
# the surface again or by changing which observations a field fuses.
SURFACE_ISSUES = ('WEAK_COVERAGE', 'MISSING_COVERAGE', 'VIEW_SYNTHESIS', 'SURFACE_REPRESENTATION',
                  'SURFACE_DIRECTION', 'OBSERVATION_DISAGREEMENT', 'FUSION_SEAM')
CORRECTIONS = ('TEXTURE_SOURCE', 'SURFACE_OBSERVATION', 'MAPPING_FUSION')
WEAKNESS = schema.object_schema(dict(component=schema.NAME, observation=TEXT, correction=TEXT,
    evidence_image_id=schema.IDENTIFIER,
    category=schema.enum('APPEARANCE', *CORRESPONDENCE_ISSUES, *SURFACE_ISSUES),
    correction_type=schema.enum(*CORRECTIONS),
    scope=schema.enum('LOCAL', 'GLOBAL'), correspondence_id=schema.IDENTIFIER),
    ("component", "observation", "correction", "evidence_image_id"))
CRITIQUE_SCHEMA = schema.object_schema(dict(candidate_digest=dict(type="string", minLength=64, maxLength=64),
    weaknesses=dict(type="array", items=WEAKNESS, minItems=0, maxItems=12),
    generated_contribution=TEXT, preserved_decisions=TEXT,
    reviewed_views=dict(type='array', items=schema.enum('front', 'back', 'left', 'right', 'top', 'bottom', 'close'),
                        minItems=7, maxItems=7)),
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
    packets = [p for p in captured.get('correspondences', {}).values() if p['candidate_digest'] == candidate['digest']]
    image_ids.update(image['id'] for p in packets for image in p['images'])
    if candidate['result'].get('correspondence_available'):
        if set(arguments.get('reviewed_views', [])) != set(candidate['result']['review_views']):
            raise schema.AuthoringError('Inspect and acknowledge front, back, left, right, top, bottom and close before critique')
        for weakness in arguments['weaknesses']:
            if not weakness.get('category') or not weakness.get('scope'):
                raise schema.AuthoringError('Classify each weakness by category and LOCAL/GLOBAL scope')
            if not weakness.get('correction_type'):
                raise schema.AuthoringError('Classify each weakness as TEXTURE_SOURCE, SURFACE_OBSERVATION or '
                                            'MAPPING_FUSION: wrong appearance is corrected in the source, weak '
                                            'coverage or a badly represented surface by observing it again, and '
                                            'disagreeing observations by changing the fusion')
            if weakness['category'] in SURFACE_ISSUES and weakness['correction_type'] == 'TEXTURE_SOURCE':
                raise schema.AuthoringError('A surface-observation defect cannot be corrected by editing a flat '
                                            'texture source; capture a corrective observation instead')
            if weakness['category'] in CORRESPONDENCE_ISSUES and not any(
                    p['correspondence_id'] == weakness.get('correspondence_id') for p in packets):
                raise schema.AuthoringError('Inspect the problematic pixel with inspect_texture_correspondence before recording a geometry/texture correction')
    if any(item["evidence_image_id"] not in image_ids for item in arguments["weaknesses"]):
        raise schema.AuthoringError("Each weakness must cite an image returned for this candidate")
    if enabled(captured) and captured["preview_count"] == 1 and not arguments["weaknesses"]:
        raise schema.AuthoringError("The initial generated-surface review needs a concrete visual improvement; inspect closeups, channels and generated-source bypass before correcting")
    candidate["critique"] = arguments
    for packet in packets:
        related = [item for item in arguments['weaknesses']
                   if item.get('correspondence_id') == packet['correspondence_id']]
        packet['review_scopes'] = sorted({item['scope'] for item in related if item.get('scope')})
        packet['review_corrections'] = sorted({item['correction_type'] for item in related
                                               if item.get('correction_type')})
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
