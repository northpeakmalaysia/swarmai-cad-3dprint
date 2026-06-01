# @swarmai/cad-3dprint

A SwarmAI **tool plugin** that turns text requests into **watertight,
slicer-ready STL** files. Geometry is produced by a Python engine
(`trimesh` + `manifold3d`) so every solid is guaranteed manifold (printable).

Built from the *text-to-CAD* exercise that produced a complete Pelton
water-turbine generator set — generalised into reusable SwarmAI tools.

## Tools (toolset `cad-3dprint`)

| Tool | Purpose |
|------|---------|
| `cad_generate_turbine` | Complete Pelton water-turbine generator set: `runner`, `nozzle`, `housing`, `cover`, `base`, `coupling`. Parametric (mm). |
| `cad_generate_primitive` | One parametric part: `box`, `cylinder`, `tube`, `cone`, `sphere`, `washer`, `standoff`. |
| `cad_validate_stl` | Watertight / winding / volume / bbox / printable verdict for any STL. |
| `cad_print_guide` | Returns the toolset **system prompt** + live capability list. |

## How it fits SwarmAI

- Default export is a `PluginEntry = (api, config) => void` (the
  `@swarmai/plugin-sdk` contract). It calls `api.registerTool(def)` for each
  tool — exactly like first-party `@swarmai/tools`.
- Discoverable via `package.json#name` / `swarmai-package.yaml#id`
  (`@swarmai/cad-3dprint`); the loaded entry is `dist/index.js`.
- Each `ToolDef` ships both a `zod` `schema` (host input validation via
  `safeParse`) **and** a `schemaOverride` (raw JSON Schema for the LLM) so the
  out-of-tree plugin never depends on the host's zod copy.
- Heavy geometry runs in a **child Python process** (`python/cad_runner.py`),
  exchanging JSON over stdin/stdout — the Node layer stays thin.

```
SwamAI_Plugin_Tools/
├─ dist/                  # shipped, prebuilt runtime (loaded by SwarmAI)
│  ├─ index.js            #   PluginEntry + 4 tools  ← entry point
│  └─ system-prompt.js    #   exports CAD_SYSTEM_PROMPT
├─ src/                   # canonical TypeScript source (build in monorepo → build/)
│  ├─ index.ts
│  └─ system-prompt.ts
├─ python/                # geometry engine
│  ├─ cad_engine.py       #   turbine + primitives + validate (trimesh/manifold3d)
│  └─ cad_runner.py       #   JSON stdin/stdout dispatch
├─ scripts/selftest.mjs   # mock-PluginAPI end-to-end test
├─ SYSTEM_PROMPT.md       # agent system prompt (canonical)
├─ swarmai-package.yaml   # plugin manifest (id, entry, tools, requirements)
├─ package.json           # name=@swarmai/cad-3dprint, main=dist/index.js, dep: zod
├─ requirements.txt       # numpy trimesh manifold3d shapely matplotlib
├─ install.ps1            # one-time setup (npm + pip + selftest)
└─ plugins.yaml.example   # ready-to-paste manifest entry
```

## Install (one time)

```powershell
cd "F:\SwarmAI\CAD\SwamAI_Plugin_Tools"
./install.ps1                 # installs zod (npm) + python deps (pip) + selftests
# or manually:
npm install zod
python -m pip install -r requirements.txt
```

## Activate

Add the plugin to your SwarmAI manifest (`%APPDATA%\swarmai\plugins.yaml` on
Windows). See `plugins.yaml.example` — the loader accepts a Windows absolute
path, so the simplest entry is:

```yaml
version: 1
plugins:
  - module: "F:\\SwarmAI\\CAD\\SwamAI_Plugin_Tools\\dist\\index.js"
    enabled: true
    config:
      pythonPath: python
      outputDir: "F:\\SwarmAI\\CAD\\output"
```

Restart the SwarmAI server; the 4 tools appear under the `cad-3dprint` toolset.

### Config / env

| Setting | Config key | Env var | Default |
|---------|-----------|---------|---------|
| Python binary | `pythonPath` | `CAD_PYTHON` | `python` |
| Output folder | `outputDir` | `CAD_OUTPUT_DIR` | `<plugin>/output` |

## The system prompt

You asked for the toolset to ship a **system prompt**. It lives in three synced
places:

1. **`SYSTEM_PROMPT.md`** — canonical, human-readable.
2. **`CAD_SYSTEM_PROMPT`** — exported from `dist/system-prompt.js` (and
   `src/system-prompt.ts`) so a host can inject it into a persona/system prompt.
3. **`cad_print_guide`** tool — returns it at runtime so the agent can pull the
   guidance on demand (parameters, safety, print settings).

> Note: `PluginAPI` has no typed "register system prompt" hook (only
> `registerTool`/`registerService`/provider/channel/etc.), so a plugin can't
> *force* text into the global system prompt. The three mechanisms above are the
> supported ways to surface it — wire `CAD_SYSTEM_PROMPT` into your persona, or
> let the agent call `cad_print_guide`.

## Develop (build in monorepo)

`src/*.ts` imports `@swarmai/plugin-sdk` + `@swarmai/shared`, so it only builds
inside the SwarmAI workspace. `tsconfig.json` emits to `build/` (not `dist/`) so
a build never clobbers the tested, shipped `dist/` runtime.

## Test

```powershell
node scripts/selftest.mjs      # loads the plugin, exercises all 4 tools
```

## Example calls

```jsonc
// Full turbine set, scaled up, custom output
cad_generate_turbine {
  "dimensions": { "pitch_radius": 55, "n_buckets": 20, "shaft_d": 5, "wall": 4 },
  "outputDir": "F:\\SwarmAI\\CAD\\output\\turbine-big"
}

// Just a coupling and runner
cad_generate_turbine { "parts": ["runner", "coupling"], "preview": false }

// A mounting bushing
cad_generate_primitive { "shape": "tube", "name": "bushing", "dimensions": { "od": 16, "id": 8, "h": 12 } }

// Verify before slicing
cad_validate_stl { "path": "F:\\SwarmAI\\CAD\\output\\turbine\\runner.stl" }
```

## Notes & limits

- Impulse turbine: the housing contains **splash, not pressure** — never cap the
  outlet. DIY / educational micro-hydro, not a certified pressure device.
- A part larger than a ~200–250 mm bed → split or rescale via `dimensions`.
- If a tool returns `missing_deps`, run `install.ps1` / `pip install -r
  requirements.txt`.
