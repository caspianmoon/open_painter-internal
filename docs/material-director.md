# Material Director

Material Director turns an instruction into an editable Open Painter material change. It runs
Codex in a separate local process and applies validated proposals through the normal layer system.
Codex can generate surface textures and grayscale masks with its native image tool, then assign them
to editable layers. The complete material remains a normal Open Painter stack.

## Set up

1. Install Open Painter **0.8.0** and open its editor. Select an existing texture set with an assigned,
   UV-unwrapped mesh. Use **Scene Materials** scope to coordinate all assigned texture sets in the scene.
2. Open the sidebar with **N**, then choose the **Director** tab.
3. In the add-on preferences (Edit › Preferences › Add-ons › Open Painter › Material Director),
   select a supported **Codex Executable**, or leave it empty to find `codex` on `PATH`. Blender launched from a desktop shortcut may have a different `PATH`
   from your terminal, so choosing the executable explicitly is often useful.
4. Enable Blender's **Preferences → System → Allow Online Access** and **Share Material Context
   with Codex**. Choose **OS Keyring** to retain login, or **This Connection Only** to keep
   credentials in memory. Open Painter never imports another editor's credential files.
5. Click **Connect Codex**, then **Sign in with ChatGPT**. Complete the browser sign-in. The panel
   shows the account's available models and reported allowance usage. Account eligibility and
   service limits are controlled by Codex; the add-on does not grant subscription access.

Disconnect and reconnect after changing the executable or account-storage setting.

The adapter currently accepts Codex **0.154.x**. Its protocol and tool inventory were tested with
**`codex-cli 0.154.0-alpha.6.2`**. Other minors fail with a version message. It uses the app-server's
experimental dynamic-tool API, so extending this range requires rerunning the capability probe,
not just loosening the version check. The add-on does not silently use a binary from VS Code or
another editor's private installation, and it does not bundle or download Codex.

The integration follows the documented [Codex app-server protocol](https://learn.chatgpt.com/docs/app-server)
and [Codex authentication flow](https://learn.chatgpt.com/docs/auth). It delegates authentication to
Codex instead of implementing identity OAuth or attempting to reuse ChatGPT session cookies.

## Direct a material

| Control | Behavior |
| --- | --- |
| **Create** | Construct material content in the active texture set. Existing content remains editable; new content is inserted below protected overlays. |
| **Refine** | Adjust existing controls, with current material state, recent changes and recent conversation. |
| **Plan** | Prepare a validated draft. Review **Show Changes**, then **Apply Draft** or **Discard**. |
| **Explain** | Discuss the current material. Mutation proposals are rejected in this mode. |
| **Texture Set** scope | Permit changes within this texture set. |
| **Scene Materials** scope | Inspect and coordinate up to 16 assigned scene materials. Create covers all of them; Refine and Plan can address a subset. Apply and AI undo operate on the complete group. |
| **Selected Layer / Folder** scope | Permit edits to that layer or folder's descendants. New layers require a folder or texture-set scope. |
| **Send Current 3D View** | Enabled by default. Send the color-managed whole view and an isolated view of the target, up to 768 px. Scene inspection can request a framed view of each captured part. UI panels and overlays are excluded. |
| **Send Mesh Maps** | Enabled by default. Send bounded previews of available AO, curvature, normal, world-normal, position and thickness maps, with provenance and missing-map information. |
| **Reference Image** | Send an optional appearance reference. It is labeled separately from geometry maps and available to Codex's native image generation. |
| **Generate Surface Textures** | Enabled by default. Allow native Codex image generation for new materials and requested texture changes. Explain disables it; small control edits retain existing textures. |
| **Stop** | Cancel the instruction and discard its uncommitted draft. Late replies cannot commit it. |
| **Show Changes / Copy Changes** | Read the draft's predicted changes, or the latest committed receipt's actual changes. |
| **Undo Last AI Change** | Reverse the latest unreverted AI transaction while keeping conflicting later manual edits. The status reports retained conflicts. |

For example:

- “Create dark green painted steel with irregular edge wear and a little cavity rust.”
- “Reduce the existing rust, preserve its pattern, and make the bottom dirtier.”
- “Add a roughness-only fingerprint layer. Keep color and metalness unchanged.”
- “The scratches look too uniform. Explain what to change.”

For visual discussion, enable **Send Current 3D View** and use Material Preview or Rendered shading.
Frame the material close enough to see it. Wait for its shader preview to finish before sending.
View and map checkboxes apply to the complete instruction, including per-material inspection in
Scene Materials scope. Each inspection sends that material's isolated view and its own map previews;
it does not reuse another part's bakes. The chosen appearance reference is attached last.
Follow-up instructions capture fresh material state and views. The panel lists the initial visual inputs.
The last six instruction/reply pairs for the current material stay in memory, so Explain followed
by “Fix those two problems” can use the diagnosis. They are not saved in the `.blend`; disconnect,
file load, Blender undo/redo, and Clear AI History clear them. Current material state takes precedence.

Create and Refine commit the last valid proposal only when the turn completes successfully.
They produce one Blender undo step, regardless of the number of commands. **Ctrl+Z** follows
ordinary Blender history; **Undo Last AI Change** uses the persisted receipt to preserve later
artist edits where possible. If an AI-created layer was subsequently edited manually, a conflicting
reversal keeps that layer. A new protection lock can prevent reversal; the material remains intact.

All controls remain normal Open Painter controls in the **Painter** sidebar and Open Graph.
There is no requirement to keep Codex connected to edit, save, reopen or export the result.

## Generated textures

With **Generate Surface Textures** enabled, try: “Generate dark olive enamel and an irregular
grayscale chipped-paint mask. Apply both, with separate exposed steel and cavity rust layers.”
Material Director enables Codex's native `image_generation` feature through the connected account.
It does not require an additional image API key or run an image-generation script.

The panel reports generation progress. Codex receives IDs for completed images and assigns them
through `assign_texture`. Color and emission textures use **sRGB**; grayscale masks, scalar maps and
detail inputs use **Non-Color**. Imported originals are packed into the `.blend`, with their generated
prompt recorded on the image. Image inputs retain normal UV/triplanar projection, scale, layer/mask
levels, filters and opacity. Image replacements never overwrite existing pixels.

Generate at most four PNGs per instruction, each up to 4096 × 4096 and 16 MiB. The instruction has a
15-minute time limit when generation is enabled, or 30 minutes for a scene instruction. The four-image
budget is shared across the entire scene: one generated paint source can serve many editable stacks.
Native image output is decoded in the companion;
the Blender connection carries small metadata messages. Only verified artifacts from the active
instruction can be newly assigned. Plan keeps them in the private cache until Apply; no Blender
image datablocks are allocated for an uncommitted draft. Stop/discard never applies pending images.

Generated textures supply surface detail. Geometry masks still determine cavities, edge wear,
bottom dirt and named regions. A generated albedo is not a physical roughness, height or normal map;
the model is instructed to keep those channels independent. Seamlessness and physically calibrated,
aligned PBR maps are not guaranteed by the image service. Inspect the result before production use.
A chosen reference image is loaded as a read-only bounded preview (up to 768 px), without altering
the file or existing Blender image pixels. Mesh-map previews are at most 256 px. The integration
does not automatically upload every authored layer image. Small follow-ups edit existing controls
or generate a replacement.

See [native image-generation validation](material-director-imagegen-validation.md) for the actual
account test, generated source images, rendered material, and remaining quality limitations.

## Protected content and input changes

Paint layers and painted/stroke masks are protected automatically. **Protected Content** also
lets you lock the selected layer, selected channel, or selected mask. A protected folder includes
its descendants. A channel lock still permits changes to unrelated channels. Shared anchor inputs
are checked so editing a producer cannot indirectly alter a protected consumer's sampled result.
These protections constrain AI authoring; they do not freeze the final composed appearance against
every possible layer added elsewhere in the stack.

The request captures texture-set IDs, scene/window identity, object bindings and a digest of the
authoring/geometry inputs. Manual edits, changed inputs, missing targets, or changed protection
settings reject a stale draft. Loading a file, Blender undo/redo, disconnecting or cancelling
invalidates the active request. A switch to a different active texture set also prevents applying
the draft there. Run the next instruction against the current state.

In Scene Materials scope, changes to any captured material invalidate the draft. All proposed targets
are validated before mutation, and a failure restores the entire group. Scene receipts persist on
each affected texture set. AI undo from any member reverses the group; if a member has a newer AI
change, undo that first. Removing a member or clearing its history makes that group unavailable.

Local receipts retain up to eight transactions within an 8 MiB budget per texture set. They include
before/after material data and the actual parameter report, and survive saving/reopening a `.blend`.
They are a bounded convenience history, not a replacement for Blender undo or project backups.
**Changes → Clear AI History** (the trash button) removes those receipts and the current conversation without changing layers.

## Panel layout since 0.12.0

The *Director* tab reads **Target → Instruction → Run → Changes**: the target names the set, folder
or scene the instruction applies to (the same path the inspector shows) with the scope and the
number of protected items; the instruction holds the prompt, a compact mode choice (Refine by
default — ordinary requests need no mode decision), the reference image and *What is sent*, a
list generated from the same attachment description the request is built from (model views,
mesh maps, reference, texture generation), with the summary of the last request that actually
went out; Run holds the connection state and the run/stop button; Changes holds the draft's
Apply / Discard, the reply, the change report and the AI undo. Connection settings live in the
add-on preferences.

## What the first implementation can change

- Fill layers and folders with stable IDs and semantic names.
- Enabled channels, values, colors, opacity, procedural sources, seeds, scales and projections.
- Native generated PNGs assigned to channel content, scalar detail inputs, or fill masks.
- Generator, procedural/noise, baked-map, scene-field, filter and published-input masks.
- Channel filters and mask filters, including levels, contrast and color adjustments.
- Live orientation, height, edge and concavity fields, and explicitly bound spatial inputs.
- Published mask/channel anchors and validated connections.
- Bundled catalog materials, stackable effects and module recipes. Catalog provenance is recorded
  separately from library module inheritance, avoiding false parent-version metadata.

Catalog insertion explicitly chooses **BAKED** or **LIVE**. BAKED preserves the catalog's requested
maps. LIVE substitutes supported live edge/concavity/orientation fields; it is a different mask
construction, not a claim that curvature and AO are interchangeable. Unsupported map requirements
still produce an actionable “bake missing maps” error. Scene fields and material aging are artistic
approximations, not water-flow or corrosion simulations.

Each material proposal is limited to 64 commands, 24 new layers, 128 new masks/filters, 256 total
layers and 256 KiB of command JSON. A coordinated scene proposal supports up to 16 materials,
512 commands and 1 MiB total. Geometry context is bounded to 64 assigned/selected objects; each
material context is limited to 256 KiB.
Blender commits on its main thread, with shader callbacks coalesced for the batch. Unchanged
layers, images and numeric shader bindings are retained. Validation or commit failure restores the
prior authoring state and removes newly allocated, unused recipe resources.

Proposals also have a conservative budget of **12 estimated shader texture inputs** to reserve
room for Blender's viewport on Metal. Images, non-analytic procedural sources and scene fields
consume inputs; constants, levels and shared mask references are cheaper. The model receives the
budget and validation feedback. Existing larger materials can receive edits that do not increase
the estimate. This guard does not replace checking the rendered result.

### Current limits

- Existing texture sets and material assignments only. It does not create UVs, change mesh topology,
  segment object parts, reassign material slots, alter output resolution, export, or modify geometry helpers.
- No arbitrary Python, shell, RNA paths or free-form node wiring from the model. The provider cannot
  call Blender operators directly. Destructive delete/reparent commands are outside the initial API.
- It retrieves bundled recipes, not arbitrary user-directory assets or remote material libraries.
- Bakes remain explicit Blender actions. It does not launch Cycles baking or destructive mesh preparation.
- Region-sensitive placement needs an existing named attribute, vertex group or selected reference
  object. It can suggest a region or discuss a view; it cannot reliably infer physical handles or
  identify every part of an assembly from names and bounding dimensions.
- The panel displays the latest reply and carries bounded recent conversation. It does not persist full
  chat transcripts, support streaming edits, or run an unattended render/refine loop.
- A committed recipe can still hit an existing shader/resource evaluation limit. The receipt records
  the committed authoring change; it does not certify completed rendering. The normal Painter preview
  diagnostics remain authoritative, and AI undo remains available.
- Real Codex/ChatGPT-account inference was tested on Blender 5.2.1 LTS, macOS Apple Silicon.
  See the [live acceptance report](material-director-live-validation.md) for actual renders, failures,
  fixes and remaining quality limits. Browser sign-in, account exhaustion and Windows/Linux remain untested.

## Data and process boundaries

The Blender process gathers bounded summaries: object names/dimensions/counts, UV/attribute/group
names, material structure, values, available anchors/maps and local recipe descriptions. The full mesh
and `.blend` remain local. Model views and mesh-map previews are sent when their checkboxes are enabled
(both default on), plus the chosen reference image. Pending prompts, provider replies and account display state are transient.
Receipts and protection paths are stored in the project; credentials and OAuth URLs are not.

Blender starts a bundled companion script using its own Python executable. A randomly authenticated
loopback socket carries bounded JSON messages; Blender polls it without worker threads touching `bpy`.
The companion owns Codex's stdio connection, network activity and reader thread. Disconnect/unregister
terminates that companion's process group. The app uses its own Codex state directory in Blender's
extension user-data area (the add-on config directory when run from source).

Codex is started with restrictive configuration, disabled execution environments and disabled
shell/web/apps/plugins/agent features. The model-facing inventory consists of five Painter
tools, account-supported native image generation, and the runtime's built-in `skills.list`,
`skills.read`, and `request_user_input` declarations. The fake-provider capability probe does not
advertise account-gated image generation; the real ChatGPT-account test exercised that feature.
The host denies unexpected approval/question requests. Those built-ins are not Painter mutation tools;
the capability test fails if execution, generic file, web, MCP or other tools appear. This is a
version-specific integration boundary, not a claim that Codex's read-only preset alone is sufficient.
See the [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).
Scene scope substitutes `propose_scene_changes` for `propose_changes` and adds
`get_material_context`, whose tool results can include labeled images.

## Source and validation

| Area | Entry points |
| --- | --- |
| Strict schema, compilation, scope and locks | [authoring/schema.py](../src/open_painter/authoring/schema.py), [authoring/commands.py](../src/open_painter/authoring/commands.py) |
| Detached documents, minimal reconciliation and receipts | [authoring/document.py](../src/open_painter/authoring/document.py), [authoring/transactions.py](../src/open_painter/authoring/transactions.py) |
| Scene validation, grouped commit and undo | [authoring/scene.py](../src/open_painter/authoring/scene.py) |
| Reviewable system instructions | [material_director_prompt.md](../src/open_painter/authoring/material_director_prompt.md) |
| Context and trusted recipe expansion | [authoring/context.py](../src/open_painter/authoring/context.py), [authoring/assets.py](../src/open_painter/authoring/assets.py) |
| Request lifecycle and Codex | [ai/session.py](../src/open_painter/ai/session.py), [ai/codex_client.py](../src/open_painter/ai/codex_client.py), [ai/companion.py](../src/open_painter/ai/companion.py) |
| Native image output and packed imports | [ai/generated_images.py](../src/open_painter/ai/generated_images.py), [authoring/generated_textures.py](../src/open_painter/authoring/generated_textures.py) |
| Native UI and explicit visual context | [ui/material_director.py](../src/open_painter/ui/material_director.py), [ai/viewport.py](../src/open_painter/ai/viewport.py) |

Validation performed during implementation:

- **Real account use cases:** editable material creation, rust reduction, bottom dirt, screw exclusion,
  roughness-only handling marks, image-based Explain → Fix, and Plan → Apply → Undo were exercised
  through the actual Codex pipeline. The tests exposed conversation, mask semantics, scene-field
  evaluation and Metal sampler failures, which were addressed and rerun. First-pass material quality
  remains weak. [Read the evidence and exact scope](material-director-live-validation.md).

- **78 pure tests** passed, including thirteen Material Director schema/protocol/image/cancellation tests.
- **23 Material Director Blender tests** passed: real layer/mask/catalog authoring, numeric shader
  updates without texture raster work, scoped/protected edits, anchor cycles/shared dependencies,
  stale input, idempotency, failure rollback/resource cleanup, conflict-aware undo, Plan/Explain,
  invalid draft replacement, save/reopen receipts, complete scene scope, grouped rollback and undo.
- **Visible Blender workflow** passed: the native Director tab draws, a provider fixture traverses
  Run → validate → automatic commit, one Blender undo reverses the batch, redo restores it, and
  opted-in viewport capture produces a PNG without creating image datablocks or changing the material.
- **Real Codex runtime probe** passed against a local fake Responses endpoint: initialization,
  model discovery, context and proposal tool round trips, model-facing tool inventory, actual companion
  authentication/handshake, and companion cleanup. It uses no account credentials or paid inference.
- Existing registration, shader undo/redo, structural shader and graph-link regressions were run
  alongside these checks. Test commands and the account-dependent acceptance steps are in [TESTING.md](../TESTING.md).

The original [implementation plan](material-director-plan.md) remains the roadmap for automatic
segmentation, richer geometry inspection, explicit bake orchestration and bounded autonomous visual
refinement. Scene coordination now works across existing managed material assignments.
