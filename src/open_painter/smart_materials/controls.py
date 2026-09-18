"""Master controls of smart materials (UI plan phase 2).

A catalog recipe can author a handful of controls that reach into its own layers:
*Paint Color* is the paint coat's base colour, *Chip Amount* the amount of its chips
mask, *Dirt Amount* the amount of the recess-dirt mask. Each control names one
recipe layer (by the label ``composer._stamp_module`` used when it minted the
instance's deterministic ids), optionally a channel or a mask index, and the RNA
field it edits. Nothing is manufactured: a control that is not authored here does
not exist, and a control whose layer was deleted reports a broken binding rather
than editing something with a similar name.

Bindings resolve through the instance's ids, so the inspector draws the *actual*
property (``layout.prop(owner, field)``): editing the layer itself moves the master
control, and one edit is one undo step.
"""
from dataclasses import dataclass
from uuid import UUID, uuid5


@dataclass(frozen=True)
class Control:
    label: str
    layer: str                 # recipe layer name (the stamp label)
    field: str                 # RNA property on the owner
    channel: str = ""          # owner is this channel of the layer
    mask: int = -1             # owner is this mask of the layer (index in the recipe)
    kind: str = "float"        # 'float', 'color' or 'bool' (how the inspector draws it)
    description: str = ""


@dataclass
class Binding:
    control: Control
    owner: object = None       # the RNA struct carrying ``field``
    layer: object = None
    field: str = ""
    reason: str = ""           # why the binding is broken (owner is None)

    @property
    def ok(self):
        return self.owner is not None

    @property
    def value(self):
        return getattr(self.owner, self.field) if self.ok else None


def _paint_controls(coat, chips_mask_layer=None, scratches=None, dirt=None, rust=None, coat_mask=0):
    """The common painted-metal vocabulary: colour and roughness of the coat, then the wear amounts."""
    result = [
        Control("Paint Color", coat, "color", channel="BASE_COLOR", kind="color",
                description="Base colour of the paint coat"),
        Control("Paint Roughness", coat, "value", channel="ROUGHNESS",
                description="Roughness of the paint coat"),
    ]
    if chips_mask_layer:
        result.append(Control("Chip Amount", chips_mask_layer, "amount", mask=coat_mask,
                              description="Amount of the chips mask that exposes what lies under the paint"))
    if scratches:
        result.append(Control("Scratch Amount", scratches, "opacity",
                              description="Opacity of the scratched-steel layer"))
    if dirt:
        result.append(Control("Dirt Amount", dirt, "amount", mask=0,
                              description="Amount of the cavity-dirt mask"))
    if rust:
        result.append(Control("Rust Amount", rust, "amount", mask=0,
                              description="Amount of the rust mask under the paint"))
    return result


# Authored per catalog recipe. Layer names are the recipe's own (catalog.py);
# mask indices count the recipe's masks_for(spec) order.
CONTROLS = {
    "Worn Painted Metal": _paint_controls("Blue paint coat", chips_mask_layer="Blue paint coat",
                                          scratches="Scratched exposed steel", dirt="Recess dirt",
                                          rust="Oxide beneath paint"),
    "Heavy Chipped Paint": [
        Control("Paint Color", "Flaking yellow paint", "color", channel="BASE_COLOR", kind="color"),
        Control("Paint Roughness", "Flaking yellow paint", "value", channel="ROUGHNESS"),
        Control("Paint Loss Black", "Flaking yellow paint", "black", mask=0,
                description="Black level of the paint-loss mask: higher keeps more paint"),
        Control("Paint Loss White", "Flaking yellow paint", "white", mask=0,
                description="White level of the paint-loss mask: lower removes more paint"),
        Control("Scoring Amount", "Deep scoring", "opacity"),
        Control("Dust Amount", "Trapped dust", "amount", mask=0),
    ],
    "Factory Powder-Coated Metal": [
        Control("Coat Color", "Powder coat", "color", channel="BASE_COLOR", kind="color"),
        Control("Coat Roughness", "Powder coat", "value", channel="ROUGHNESS"),
        Control("Edge Wear Amount", "Powder coat", "amount", mask=0),
        Control("Scratch Amount", "Light coating scratches", "opacity"),
    ],
}


def catalog_name(folder):
    from .modules import catalog_name as name_of
    return name_of(folder) if folder is not None and folder.type == "FOLDER" else ""


def available(folder):
    """The authored controls of this folder's recipe, or [] (an ordinary folder has none)."""
    return list(CONTROLS.get(catalog_name(folder), ()))


def has_controls(folder):
    return bool(available(folder))


def _instance_id(folder, label):
    from .composer import catalog_module_id
    module_id = catalog_module_id(catalog_name(folder))
    return f"{folder.module_instance}.{uuid5(UUID(module_id), 'layer:' + label).hex}"


def _instance_mask_id(folder, label, index):
    from .composer import catalog_module_id
    module_id = catalog_module_id(catalog_name(folder))
    return f"{folder.module_instance}.{uuid5(UUID(module_id), f'mask:{label}:{index}').hex}"


def resolve(ts, folder, control):
    """Bind a control to the actual RNA owner and field of this instance, or explain why not."""
    binding = Binding(control)
    layer_id = _instance_id(folder, control.layer)
    layer = next((item for item in ts.layers if item.id == layer_id), None)
    if layer is None:
        binding.reason = f"layer '{control.layer}' was deleted"
        return binding
    if layer.parent_id != folder.id:
        # Moved out of the material: still the same authored layer, still bound.
        pass
    binding.layer = layer
    owner = layer
    if control.channel:
        owner = next((prop for prop in layer.channels if prop.channel == control.channel), None)
        if owner is None:
            binding.reason = f"{control.layer} has no {control.channel.replace('_', ' ').title()} channel"
            return binding
    elif control.mask >= 0:
        mask_id = _instance_mask_id(folder, control.layer, control.mask)
        owner = next((mask for mask in layer.masks if mask.id == mask_id), None)
        if owner is None:
            binding.reason = f"the mask of '{control.layer}' was deleted"
            return binding
    if not hasattr(owner, control.field):
        binding.reason = f"{control.field} is not a property of {control.layer}"
        return binding
    binding.owner, binding.field = owner, control.field
    return binding


def bindings(ts, folder):
    return [resolve(ts, folder, control) for control in available(folder)]


def components(ts, folder):
    """The material's direct child layers, top of the stack first."""
    return [layer for layer in reversed(list(ts.layers)) if layer.parent_id == folder.id]
