# Material modules — 0.4.1 / 0.4.2

A module is a folder subtree with an identity and a version. Save any folder as
a module, insert instances of it in any texture set, edit the instances freely,
publish a new version of the module, and update the instances in place: your
edits survive, the module's changes apply, and the few places where both changed
keep your value and are listed.



## Workflow

- **Save Folder as Module** (folder properties → *Module* box, or *Save Version*
  on an existing module folder): writes `<Name> v<n>.openpaintmaterial` to the
  smart-material library. The folder becomes the module's *origin* instance
  (version n). Saving again from that folder writes v n+1.
- **Insert Module** (Layers tool strip, package icon): search the library's
  modules (newest version each) and insert an instance above the selected layer,
  in the same folder. Instances show a package badge and their version in the
  layer list; an alert refresh badge means a newer version exists.
- **Update** / **Update All** (folder properties): update this instance, or every
  non-detached instance of the module in the set, to the library's newest version.
  The status line reports `Damage → v2` and any conflicts (`3 conflicts kept your
  values: Scratches.channels.ROUGHNESS.value, …`).
- **Replace with Library Version** discards local edits (needed when an instance's
  base snapshot is missing, e.g. from a file saved by an older add-on).
- **Detach** keeps the provenance badge but excludes the folder from updates.

## What is kept, applied and reported

Updates are three-way merges (`core/material_inheritance.preview`) between the
*base* (a snapshot of the version the instance was inserted from or last updated
to, stored as `OP Module Base · <id> · v<n>` in `bpy.data.texts`), the *parent*
(the new document) and the *local* instance:

| Situation | Result |
| --- | --- |
| Parent changed a value the instance did not touch | applied |
| Instance changed a value the parent did not | kept |
| Both changed the same value | kept (local wins), reported as a conflict |
| Parent added a layer, mask or filter | added (ids merge by stable id) |
| Parent removed something the instance did not edit | removed |
| Parent removed something the instance edited | kept, reported |
| Instance added its own layers inside the module | kept, silently |
| Painted images (masks, paint layers) | always local: images are compared by name, so an instance's own paint is never replaced unless the instance never painted it |

Everything is applied in one transaction: the subtree is replaced by the merged
layers at the same position; any failure (including a reference cycle in the
result) restores the previous subtree exactly.

## Identity and ids

- A module document (`format_version` 4) stores the subtree in the module's own
  id space with a `module` block `{id, version, name, root, description,
  created}`. Whole-set saves stay format 3 and load in 0.3.
- Inserting gives the instance a namespace token; every owned id (layers, masks,
  filter items, the derived `:out`/`:group` anchor ids) becomes `<token>.<id>`.
  Two instances therefore never collide, and because re-inserting v2 yields the
  same instance ids as v1, references *from outside* into an instance (a mask
  reading an anchor inside Damage) keep working across updates. References from
  inside the module to anchors outside it are kept verbatim and simply read as
  missing when the module is used elsewhere.
- Filter items get ids when a folder is saved as a module (they merge by id).
- The origin folder keeps the plain module ids (empty token).

Files: `smart_materials/modules.py` (namespacing, comparable snapshots, base
snapshots, library index, `save_module`, `update`, `detach`),
`smart_materials/serializer.save(root_id=…, module=…)`, `loader.apply(parent_id=…)`,
`ui/modules.py`. Tests: `tests/integration/module_documents.py`,
`module_update.py`, `tests/unit/test_module_merge.py`.

## Catalog materials and catalog modules (0.4.2)

- Every catalog material (Smart Materials shelf) now inserts as a module instance
  of its recipe: `module_source = catalog:<recipe>`, deterministic ids derived from
  the recipe's module id and each item's name (`composer._stamp_module`), a fresh
  instance token, and a base snapshot. Pixels are unchanged — ids never enter
  evaluation. When a later add-on release bumps a recipe's `version`, its
  instances show *update available* and **Update** merges the recipe change into
  them, keeping the artist's edits (`modules.update_catalog` builds the current
  definition into the set with the instance's token, snapshots it, removes it and
  applies the same three-way merge).
- `composer.MODULES` holds shared catalog modules — first *Damage* (Scratches,
  Edge Wear, Dirt, Rust). Insert Module lists them next to library modules.
- A recipe can nest modules: `recipe(..., modules=[("Damage", {"Rust": {"opacity":
  .35}, "Dirt": {"mask.amount": .6}})])` inserts a Damage instance inside the
  material with those per-recipe overrides applied as local edits, so updating
  Damage keeps them. The nested instance's token derives from the outer instance's
  (`composer.nested_token`), so an outer update matches nested ids and two
  materials never collide. The existing 50 recipes are unchanged (they keep their
  inlined, individually tuned wear layers); new recipes can share Damage.

Tests: `tests/integration/catalog_modules.py`.
