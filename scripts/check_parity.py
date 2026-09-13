"""Validate the PDF feature inventory; a strict gate rejects incomplete parity."""

import argparse
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = {
    "implemented",
    "partial",
    "missing",
    "host_equivalent",
    "external_dependency",
    "reference_only",
}


def validate(data):
    seen = set()
    errors = []
    for item in data["features"]:
        identifier = item["id"]
        if identifier in seen:
            errors.append(f"Duplicate feature: {identifier}")
        seen.add(identifier)
        if item["status"] not in STATUS:
            errors.append(f"Invalid status: {identifier}")
        if not item["pages"] or any(
            not isinstance(p, int) or not 1 <= p <= data["target"]["pages"] for p in item["pages"]
        ):
            errors.append(f"Invalid PDF pages: {identifier}")
        if not item["acceptance"].strip():
            errors.append(f"Missing acceptance criteria: {identifier}")
        if item["status"] == "implemented" and not any(
            p.startswith("tests/") for p in item["evidence"]
        ):
            errors.append(f"Implemented feature lacks test evidence: {identifier}")
        for path in item["evidence"]:
            resolved = (ROOT / path).resolve()
            if not resolved.is_relative_to(ROOT) or not resolved.exists():
                errors.append(f"Missing or invalid evidence: {identifier}: {path}")
    return errors


def render(data):
    target = data["target"]
    text = [
        "# Substance Painter PDF parity",
        "",
        "**Full parity is not achieved.** This inventory targets the user-supplied June 26, 2018 documentation (2018.1), not the latest Adobe release.",
        "",
        f"Reference: `{target['filename']}` ({target['pages']} pages). Page references below use **1-based PDF pages**, which may differ from printed footers.",
        "",
        f"SHA-256: `{target['sha256']}`.",
        "",
        data["interpretation"],
        "",
        "## Reading the status",
        "",
        "- **Implemented**: the stated behavior has implementation and test evidence; this does not certify pixel-identical output from the proprietary Substance engine.",
        "- **Partial**: a usable subset exists, with remaining acceptance requirements stated explicitly.",
        "- **Missing**: no completed plugin workflow meets the requirement.",
        "- **External dependency**: requires an engine, format or service integration; no host substitute is silently counted as parity.",
        "- **Reference only**: tutorials, historical notes or application operations; these are not feature credits.",
        "",
        "Rows group related behaviors. Counts of rows are not a completion percentage. The inventory is a requirements audit, not evidence that every scenario has been executed in Substance Painter.",
        "",
        "## Verification gate",
        "",
        "`python3 scripts/check_parity.py` validates inventory references. `python3 scripts/check_parity.py --require-parity` deliberately exits nonzero until every functional row is implemented with test evidence. It does not execute those tests or certify compatibility. Run the Blender acceptance suite separately.",
        "",
        "## Current implementation increment",
        "",
        "Version 0.2.0 adds the composable texture foundation and 50-material library. Current scope and validation are recorded in [Texture workflow v1](docs/texture-workflow-v1.md). Other rows retain historical requirements and should not be treated as a current feature count. The editor menu uses a subtype adapter, not a compiled SpaceType.",
        "",
        "The standard separable blend formulas use [W3C Compositing Level 1](https://www.w3.org/TR/compositing-1/#blendingseparable). Additional arithmetic formulas are defined and tested in our code; the manual does not establish numerical equivalence. The current evaluator includes nonseparable color modes and four normal blend modes; exact Adobe numerical equivalence is not claimed.",
        "",
        "## Dependency order for remaining work",
        "",
        "1. Dynamic channels/precision and correct signed height/normal/coverage composition.",
        "2. Layer and mask effect stacks, anchors and dependency invalidation, then linked instances and smart masks.",
        "3. Recorded multi-channel strokes, material picker, polygon fill, projection/stencil, smudge/clone and reprojection.",
        "4. Mesh-derived world normal/position/ID/thickness/signed-curvature maps, batch baking and UDIM sets.",
        "5. Seam padding, batch/preset/HDR/PSD export, resource lifecycle and advanced viewport controls.",
        "6. Runtime/format/service compatibility for SBSAR/SPSM, particles, shaders/rendering and live links.",
        "",
    ]
    area = None
    for item in data["features"]:
        if area != item["area"]:
            area = item["area"]
            text += [
                f"## {area}",
                "",
                "| ID / PDF pages | Feature / status | Acceptance and evidence |",
                "| --- | --- | --- |",
            ]
        pages = ", ".join(map(str, item["pages"]))
        evidence = ", ".join(f"[{Path(p).name}]({p})" for p in item["evidence"])
        text += [
            f"| `{item['id']}`<br>pp. {pages} | **{item['status'].replace('_', ' ').title()}** — {item['feature']} | {item['acceptance']}"
            + (f"<br>Evidence: {evidence}" if evidence else "")
            + " |"
        ]
        # Keep tables separate from subsequent headings.
        if (
            item is data["features"][-1]
            or data["features"][data["features"].index(item) + 1]["area"] != area
        ):
            text.append("")
    return "\n".join(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-parity", action="store_true")
    parser.add_argument("--write-doc", action="store_true")
    args = parser.parse_args()
    data = json.loads((ROOT / "docs/substance_parity.json").read_text())
    errors = validate(data)
    if errors:
        raise SystemExit("\n".join(errors))
    document = render(data)
    if args.write_doc:
        (ROOT / "PARITY.md").write_text(document)
    elif not (ROOT / "PARITY.md").exists() or (ROOT / "PARITY.md").read_text() != document:
        raise SystemExit("PARITY.md is stale; run scripts/check_parity.py --write-doc")
    counts = Counter(item["status"] for item in data["features"])
    print("Inventory valid: " + ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
    gaps = [
        item for item in data["features"] if item["status"] not in {"implemented", "reference_only"}
    ]
    if args.require_parity and gaps:
        raise SystemExit(
            f"PARITY NOT ACHIEVED: {len(gaps)} feature groups still have acceptance gaps. See PARITY.md."
        )


if __name__ == "__main__":
    main()
