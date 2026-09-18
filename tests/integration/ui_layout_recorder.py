"""A stand-in for ``bpy.types.UILayout`` that records what a draw function emits.

Blender's ``UILayout.introspect()`` truncates long layouts, and panels scrolled out of
a sidebar are never drawn, so tests call the inspector's drawing functions with this
recorder instead: every label, property, operator, menu, template and sub-panel lands
in ``items`` in drawing order, and ``text`` flattens them for containment checks.
"""


class _OperatorProps:
    def __init__(self, recorder, entry):
        self._recorder, self._entry = recorder, entry

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            self._entry["props"][name] = value


class Recorder:
    def __init__(self, items=None):
        self.items = items if items is not None else []
        self.use_property_split = False
        self.use_property_decorate = False
        self.enabled = True
        self.active = True
        self.alert = False
        self.alignment = "EXPAND"
        self.scale_x = self.scale_y = 1.0

    # -- containers -----------------------------------------------------------------
    def _child(self, kind, **kw):
        self.items.append((kind, kw))
        return Recorder(self.items)

    def row(self, **kw):
        return self._child("row", **kw)

    def column(self, **kw):
        return self._child("column", **kw)

    def box(self, **kw):
        return self._child("box", **kw)

    def split(self, **kw):
        return self._child("split", **kw)

    def grid_flow(self, **kw):
        return self._child("grid", **kw)

    def column_flow(self, **kw):
        return self._child("column_flow", **kw)

    def panel(self, idname, default_closed=False):
        self.items.append(("panel", dict(idname=idname, default_closed=default_closed)))
        # Both header and body draw: tests see everything a panel can show.
        return Recorder(self.items), Recorder(self.items)

    # -- leaves ------------------------------------------------------------------------
    def label(self, text="", icon="NONE", **kw):
        self.items.append(("label", dict(text=text, icon=icon)))

    def separator(self, **kw):
        self.items.append(("separator", {}))

    def prop(self, owner, name, text=None, **kw):
        self.items.append(("prop", dict(owner=type(owner).__name__, name=name,
                                        text=text if text is not None else name, **kw)))

    def prop_enum(self, owner, name, value, text=None, **kw):
        self.items.append(("prop_enum", dict(owner=type(owner).__name__, name=name, value=value,
                                             text=text if text is not None else value)))

    def prop_menu_enum(self, owner, name, text="", **kw):
        self.items.append(("prop_menu_enum", dict(owner=type(owner).__name__, name=name, text=text)))

    def prop_search(self, owner, name, *args, text=None, **kw):
        self.items.append(("prop_search", dict(owner=type(owner).__name__, name=name, text=text or name)))

    def operator(self, idname, text=None, icon="NONE", **kw):
        entry = dict(idname=idname, text=text if text is not None else idname, icon=icon, props={})
        self.items.append(("operator", entry))
        return _OperatorProps(self, entry)

    def menu(self, idname, text=None, icon="NONE", **kw):
        self.items.append(("menu", dict(idname=idname, text=text if text is not None else idname, icon=icon)))

    def popover(self, panel, text=None, icon="NONE", **kw):
        self.items.append(("popover", dict(panel=panel, text=text or panel, icon=icon)))

    def template_ID(self, owner, name, **kw):
        self.items.append(("template_ID", dict(owner=type(owner).__name__, name=name)))

    def template_list(self, list_type, list_id, data, propname, active_data, active_propname, **kw):
        self.items.append(("template_list", dict(list_type=list_type, propname=propname,
                                                 active=active_propname, **kw)))

    def template_icon(self, *args, **kw):
        self.items.append(("template_icon", {}))

    def template_preview(self, *args, **kw):
        self.items.append(("template_preview", {}))

    # -- inspection --------------------------------------------------------------------
    @property
    def text(self):
        parts = []
        for kind, data in self.items:
            if kind in {"label", "prop", "prop_menu_enum", "prop_search", "operator", "menu", "popover", "prop_enum"}:
                parts.append(str(data.get("text", "")))
            if kind in {"operator", "menu", "popover"}:
                parts.append(str(data.get("idname", data.get("panel", ""))))
            if kind == "panel":
                parts.append(data["idname"])
            if kind in {"prop", "prop_menu_enum", "template_ID"}:
                parts.append(data["name"])
            if kind == "template_list":
                parts.append(data["list_type"])
        return "\n".join(parts)

    def contains(self, needle):
        return needle in self.text

    def index(self, needle):
        return self.text.index(needle)

    def operators(self, idname=None):
        return [data for kind, data in self.items if kind == "operator" and (idname is None or data["idname"] == idname)]

    def props(self, name=None):
        return [data for kind, data in self.items if kind == "prop" and (name is None or data["name"] == name)]

    def labels(self):
        return [data["text"] for kind, data in self.items if kind == "label"]

    def panels(self):
        return [data["idname"] for kind, data in self.items if kind == "panel"]
