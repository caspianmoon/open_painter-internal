"""Exact user-authored prompts; shared without importing Blender."""
PROMPTS = (
    "Texture this like it belongs in a realistic post-apocalyptic game.",
    "This doesn't look realistic enough. Improve it.",
    "Make it look like it was textured by a professional game artist. Remove anything that looks obviously procedural or artificial.",
)
STAGES = ("create", "refine_1", "refine_2")
MODES = ("CREATE", "REFINE", "REFINE")
SURFACE_PROMPTS = (PROMPTS[0],
    "This still looks too empty and procedural. Make it look like a professionally textured hero weapon from a AAA post-apocalyptic game.")


def unattempted_stages(reason, surface_sources=False):
    prompts = SURFACE_PROMPTS if surface_sources else PROMPTS
    return [dict(name=name, mode=mode, prompt=prompt, status="NOT_RUN", reason=reason,
                 submitted=False, applied=False) for name, mode, prompt in zip(STAGES, MODES, prompts)]
