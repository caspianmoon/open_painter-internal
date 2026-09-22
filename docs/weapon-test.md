# Two-weapon Material Director test

Implements [Weapon_Test_Guide.md](../Weapon_Test_Guide.md) with the supplied CC0
rifle and SMG. This is a development test, not independent human validation.

Run from the repository root on the Blender test machine:

```sh
python3 scripts/benchmark_weapons.py prepare
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --disable-autoexec --python-exit-code 1 \
  --python tests/benchmarks/material_director/test_weapon_fixture_blender.py \
  -- .blender-test/weapon-test
python3 scripts/benchmark_weapons.py run --use-codex-login
/Applications/Blender.app/Contents/MacOS/Blender --background --factory-startup \
  --disable-autoexec --python-exit-code 1 \
  --python tests/benchmarks/material_director/verify_weapon_checkpoints.py \
  -- .blender-test/weapon-test
python3 scripts/benchmark_weapons.py report
```

`--pack`, `--rig`, `--output`, `--blender`, `--codex` and `--model` override paths
and the pinned provider model. The default rig is the existing frozen
`fixtures-eevee-v1/_infrastructure/benchmark_rig.blend`. Preparation requires
that file and the supplied download ZIP; it never fetches replacement models.
Live inference uses the current Codex login through the product's normal
Material Director operator. Both weapons use `gpt-6-astra` by default, with
generated textures and ordinary view/map context enabled. No extra prompt
instructions or authored material recipes are added.

Preparation verifies source checksums, appends only mesh objects to the rig,
neutralizes labels/materials, verifies UVs with Bake Doctor, and records any
UV-only repair. Geometry, object transforms and occupied material partitions
must retain their hashes. Source scale is retained as supplied; it is not
normalized to a guessed real-world weapon length. A source zero-area face can
remain only when its geometric area is verified below the diagnostic threshold;
the exception is recorded separately from usable UV surfaces. Source scripts
are disabled. No source scene lighting is imported and no Cycles render/bake is
used. Mesh maps are not prebaked by this test; normal product context and live
geometry tools remain available.

The five camera poses are frozen before inference. Each neutral/applied/failure
checkpoint saves a packed `.blend`, structured editable-material state and
left/right/front/rear/close Eevee renders. Geometry and rig drift fail evidence
validation rather than being repaired. Each weapon gets a fresh conversation;
refinements continue its existing conversation. A response without an applied
commit is failed and its dependent prompts are not run. An exclusive attempt
marker prevents silent reruns, including after process failure. A second run
command only starts weapons that have no attempt marker.

The output contains `index.html`, `Weapon_Test_Review.zip`, checksummed source
provenance, preparation reports, fixtures and all available actual evidence.
If live inference is blocked, the gallery still contains neutral views and
explicit `NOT_RUN` labels; neutral images are never substituted for AI results.
The report includes a root `review.json` when present. Reviews should address
believability, apocalypse direction, geometry-specific wear, each refinement's
effect, editability, a yes/no game-readiness verdict and the three main problems.
No verdict is invented when live evidence has not been assessed.

The revised generated-surface test uses `--surface-sources` and a separate output
directory. Copy the already frozen `fixtures` and `sources` directories from the
baseline; do not prepare again or overwrite an attempt. Both weapons receive the
same original create prompt, then exactly one follow-up:

> This still looks too empty and procedural. Make it look like a professionally textured hero weapon from a AAA post-apocalyptic game.

The automatic preview/critique/correction turns are internal to each instruction.
Candidate images, structured critiques and generated-source contribution evidence
are retained in the ordinary trace/outcome. The baseline three-prompt protocol
remains the default for reproducing the original guide.

The post-run verifier reopens each actual saved checkpoint, compares its authoring
document and frozen geometry/rig with the recorded evidence, checks packed source
images, and changes/restores ordinary layer and mask controls in memory. It never
saves over a checkpoint or sends another AI prompt. Writable controls are evidence
of editability; the verifier does not claim that every control has useful visual
influence or grade the artwork.

Offline checks:

```sh
python3 -m unittest discover -s tests/unit -p 'test_material_director_benchmark*.py'
```

The Blender preflight above verifies all ten neutral views, shared lighting and
exposure, frozen rig restoration, and rejection of exposure drift. It makes no
AI calls and supplies no authored proposals.
