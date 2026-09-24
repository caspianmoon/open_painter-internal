"""One instruction's progress, as the artist sees it.

A Codex turn can run for many minutes: the model reasons, generates textures on
OpenAI's servers and asks Blender for renders that hold the main thread. With only
a status line the Material Assistant looks stalled. This keeps a bounded record of
the running instruction: the steps Blender ran and how long they took, Codex's
reasoning summaries and its progress notes. It is presentation only and free of
bpy, so it is unit-tested without Blender; session.py feeds it, the panels draw it.
"""

import re
import time

MAX_STEPS = 80
MAX_THOUGHTS = 40
MAX_NOTES = 20
MAX_TEXT = 4000

# Tools that render or evaluate on Blender's main thread: the interface cannot
# redraw until they return, so the panel names them before they start.
BLOCKING_TOOLS = frozenset({"preview_proposal", "capture_surface_observations", "inspect_surface_area",
                            "inspect_material_channels", "inspect_mask_coverage", "get_material_context"})

TOOL_LABELS = dict(
    get_context="Reading the material and its layers",
    get_material_context="Capturing views of a scene material",
    search_library="Searching the material library",
    describe_asset="Reading a library recipe",
    list_generated_images="Collecting the generated textures",
    plan_surface="Planning the surface components",
    capture_surface_observations="Capturing surface views for texture generation",
    inspect_surface_observation="Studying a surface view",
    inspect_surface_coverage="Checking texture coverage",
    inspect_surface_area="Capturing a close view to correct",
    inspect_texture_correspondence="Tracing a visible defect to its texture",
    list_regions="Listing mesh regions",
    inspect_region="Looking at a mesh region",
    query_region="Selecting mesh regions",
    inspect_surface="Framing a surface for projection",
    inspect_surface_source="Inspecting a texture source",
    inspect_material_channels="Rendering the material channels",
    inspect_mask_coverage="Measuring mask coverage",
    preview_proposal="Rendering a candidate in Eevee",
    inspect_candidate="Reviewing the candidate renders",
    compare_candidates="Comparing with the previous candidate",
    critique_candidate="Critiquing the candidate",
    propose_changes="Checking the proposed changes",
    propose_scene_changes="Checking the coordinated scene changes",
    report_inability="Reporting what blocks this instruction")

# How an ended instruction reads in the panel: "Finished in 3:05", "Stopped after 0:42".
ENDINGS = dict(completed="Finished in", stopped="Stopped after", failed="Ended after")
STEP_KINDS = dict(completed="DONE", stopped="STOPPED")


def clock(seconds):
    """0:07, 12:45, 1:02:03."""
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def plain(text):
    """Blender labels show Markdown literally; keep only the words."""
    return re.sub(r"\*\*|__|`", "", str(text))


def one_line(text, limit=300):
    return " ".join(plain(text).split())[:limit]


def heading(text):
    """Each reasoning summary part opens with a bold heading: (heading, body)."""
    match = re.match(r"\s*\*\*([^*\n]+)\*\*", text)
    if match is None:
        return "", plain(text).strip()
    return match.group(1).strip(), plain(text[match.end():]).strip()


def tool_label(name):
    return TOOL_LABELS.get(name) or "Running " + str(name).replace("_", " ")


class Progress:
    def __init__(self, now=time.monotonic):
        self.now = now
        self.last_seconds = {}   # tool -> how long it last held Blender, across instructions
        self.reset()

    def reset(self):
        self.request_id = None
        self.started = self.finished = self.updated = 0.
        self.time_limit = 0
        self.outcome = ""
        self.steps = []      # dict(at, text, kind, busy, seconds, error)
        self.thoughts = []   # dict(key, text, step): reasoning summary parts
        self.notes = []      # dict(key, text, step): Codex's progress commentary

    # Lifecycle ---------------------------------------------------------------------
    def begin(self, request_id):
        self.reset()
        self.request_id = request_id
        self.started = self.updated = self.now()

    @property
    def running(self):
        return bool(self.started) and not self.finished

    def owns(self, request_id):
        return self.running and request_id == self.request_id

    def finish(self, outcome, text=""):
        if not self.running:
            return
        for entry in self.steps:
            entry["busy"] = False
        if text:
            self.step(text, STEP_KINDS.get(outcome, "FAILED"))
        self.outcome = outcome
        self.finished = self.now()

    # Time ----------------------------------------------------------------------------
    def elapsed(self):
        return (self.finished or self.now()) - self.started if self.started else 0.

    def quiet(self):
        """Seconds since Codex or Blender last reported anything."""
        return self.now() - self.updated if self.running else 0.

    def touch(self):
        if self.running:
            self.updated = self.now()

    # Records -------------------------------------------------------------------------
    def step(self, text, kind="STEP", *, busy=False, tool=None):
        """A timeline entry. Busy work lasts until the next entry, or for a tool until end()."""
        if not self.running:
            return None
        self.touch()
        for entry in self.steps:
            if entry["kind"] not in {"TOOL", "WAIT"}:
                entry["busy"] = False
        entry = dict(at=self.elapsed(), text=one_line(text), kind=kind, busy=busy, seconds=None, error="", tool=tool)
        self.steps.append(entry)
        del self.steps[:-MAX_STEPS]
        return entry

    def end(self, entry, seconds, error=""):
        """A tool returned: how long it held Blender, and why it was rejected."""
        if entry is None or not self.running:
            return  # A call answered after Stop only reports that the instruction ended.
        entry.update(busy=False, seconds=seconds, error=one_line(error))
        if entry.get("tool") and not error:
            self.last_seconds[entry["tool"]] = seconds
        self.touch()

    def _grow(self, records, key, delta, limit):
        if not records or records[-1]["key"] != key:
            records.append(dict(key=key, text="", step=None, updated=0.))
            del records[:-limit]
        record = records[-1]
        record["text"] = (record["text"] + delta)[-MAX_TEXT:]
        record["updated"] = self.elapsed()
        return record

    def think(self, item_id, part, delta):
        """A reasoning summary streams in; its heading joins the timeline once complete."""
        if not self.running:
            return
        thought = self._grow(self.thoughts, [item_id, part], delta, MAX_THOUGHTS)
        title, _ = heading(thought["text"])
        if title and thought["step"] is None:
            thought["step"] = self.step(title, "THOUGHT")
        self.touch()

    def note(self, item_id, delta):
        """A progress note streams in; its timeline entry grows with it."""
        if not self.running:
            return
        note = self._grow(self.notes, item_id, delta, MAX_NOTES)
        if note["step"] is None:
            note["step"] = self.step(note["text"], "NOTE")
        else:
            note["step"]["text"] = one_line(note["text"])
        self.touch()

    # What the panels show ------------------------------------------------------------
    def activity(self):
        """(text, kind, seconds): the work in progress, or Codex thinking between steps."""
        if not self.steps:
            return ("", "STEP", 0.)
        last = self.steps[-1]
        if not self.running:
            return (last["text"], last["kind"], 0.)
        if last["busy"]:
            return (last["text"], last["kind"], self.elapsed() - last["at"])
        ended = last["at"] + (last["seconds"] or 0.)
        return ("Codex is thinking", "THINK", max(0., self.elapsed() - ended))

    def hint(self):
        """Why a long wait is expected, or reassurance after a long silence."""
        if not self.running or not self.steps:
            return ""
        kind = self.activity()[1]
        if kind == "WAIT":
            last = self.last_seconds.get(self.steps[-1].get("tool"), 0)
            expected = f", about {clock(last)} last time" if last >= 2 else ""
            return f"Blender pauses while this runs{expected}; the panel catches up when it finishes"
        if kind == "IMAGE":
            return "Texture generation runs on OpenAI's servers and often takes a few minutes"
        if kind == "THINK" and self.quiet() >= 30:
            return "Still working: long reasoning can pass minutes without an update"
        return ""

    def latest(self):
        """The newest thing Codex said: ("THOUGHT", heading, body), ("NOTE", "", text) or None."""
        thought = self.thoughts[-1] if self.thoughts else None
        note = self.notes[-1] if self.notes else None
        if note is not None and (thought is None or note["updated"] >= thought["updated"]):
            return ("NOTE", "", plain(note["text"]).strip())
        if thought is not None:
            return ("THOUGHT",) + heading(thought["text"])
        return None

    def summary(self):
        """The box heading: "Working · 1:23" while running, "Finished in 3:05" once ended."""
        if self.running:
            return "Working · " + clock(self.elapsed())
        if not self.started:
            return ""
        return ENDINGS.get(self.outcome, "Ended after") + " " + clock(self.elapsed())

    def transcript(self):
        """The whole record, one line per entry, for the log dialog and the clipboard."""
        lines = [self.summary()] if self.started else []
        for entry in self.steps:
            line = clock(entry["at"]) + "  " + entry["text"]
            if entry["seconds"] is not None and entry["seconds"] >= 1:
                line += f" ({clock(entry['seconds'])})"
            lines.append(line)
            if entry["error"]:
                lines.append("      Rejected: " + entry["error"])
        if self.thoughts:
            lines += ["", "Reasoning summaries:"]
            for thought in self.thoughts:
                title, body = heading(thought["text"])
                lines.append("• " + (title or "Thinking"))
                lines += ["  " + paragraph.strip() for paragraph in body.splitlines() if paragraph.strip()]
        return lines
