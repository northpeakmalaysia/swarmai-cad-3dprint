/**
 * @swarmai/cad-3dprint — SwarmAI plugin: text → 3D-printable STL.
 *
 * This is the runtime entry the SwarmAI plugin-loader imports (see
 * package.json#main + swarmai-package.yaml#entry). It is intentionally
 * self-contained ESM: it depends only on its own `zod` (for handler input
 * validation) and Node builtins, and ships a `schemaOverride` (raw JSON
 * Schema) on every tool so the LLM surface never round-trips through the
 * host's `zodToJsonSchema` (avoids any cross-zod-version mismatch for an
 * out-of-tree plugin).
 *
 * The geometry is produced by the Python engine under ../python, invoked as
 * a child process with a JSON request on stdin and a JSON result on stdout.
 *
 * Contract: default export is `PluginEntry = (api, config) => void`.
 *   api.registerTool(toolDef)  — from @swarmai/plugin-sdk PluginAPI.
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { z } from 'zod';
import { CAD_SYSTEM_PROMPT } from './system-prompt.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PLUGIN_ROOT = resolve(__dirname, '..');
const PYTHON_DIR = join(PLUGIN_ROOT, 'python');
const RUNNER = join(PYTHON_DIR, 'cad_runner.py');
const TOOLSET = 'cad-3dprint';

/** Resolve the python executable + default output dir from config/env. */
function resolveEnv(config) {
  const cfg = config || {};
  const python = cfg.pythonPath || process.env.CAD_PYTHON || 'python';
  const outDir =
    cfg.outputDir || process.env.CAD_OUTPUT_DIR || join(PLUGIN_ROOT, 'output');
  return { python, outDir };
}

/** Spawn the Python engine, feed `request` as JSON on stdin, parse JSON out. */
function runPython(python, request) {
  return new Promise((resolveP) => {
    let out = '';
    let err = '';
    let child;
    try {
      child = spawn(python, [RUNNER], {
        cwd: PYTHON_DIR,
        stdio: ['pipe', 'pipe', 'pipe'],
      });
    } catch (e) {
      resolveP({ ok: false, error: `failed to spawn '${python}': ${e?.message || e}` });
      return;
    }
    child.on('error', (e) => {
      resolveP({
        ok: false,
        error:
          `cannot run python ('${python}'): ${e?.message || e}. ` +
          `Set CAD_PYTHON or the plugin's pythonPath config to a Python 3 binary.`,
      });
    });
    child.stdout.on('data', (d) => (out += d.toString()));
    child.stderr.on('data', (d) => (err += d.toString()));
    child.on('close', () => {
      const trimmed = out.trim();
      // The runner prints exactly one JSON object as its last line.
      const lastLine = trimmed.slice(trimmed.lastIndexOf('\n') + 1).trim() || trimmed;
      try {
        resolveP(JSON.parse(lastLine || '{}'));
      } catch {
        resolveP({
          ok: false,
          error: `could not parse engine output: ${trimmed.slice(0, 400)}`,
          stderr: err.slice(0, 800),
        });
      }
    });
    try {
      child.stdin.write(JSON.stringify(request));
      child.stdin.end();
    } catch (e) {
      resolveP({ ok: false, error: `failed to send request: ${e?.message || e}` });
    }
  });
}

/** Build a human-friendly summary line list from an engine result. */
function summarise(result) {
  if (!result?.parts) return [];
  return result.parts.map(
    (p) =>
      `${p.name}: watertight=${p.watertight}, vol=${p.volume_cm3}cm³, ` +
      `bbox=${p.bbox_mm?.join('×')}mm -> ${p.path}`,
  );
}

// ── shared numeric-overrides JSON schema fragment (for schemaOverride) ──
const dimsJson = {
  type: 'object',
  description:
    'Numeric overrides in millimetres. Any omitted key uses the default.',
  additionalProperties: { type: 'number' },
};

export default function pluginEntry(api, config) {
  const { python, outDir: defaultOut } = resolveEnv(config);

  // ───────────────────────── cad_generate_turbine ─────────────────────────
  const turbineParts = ['housing', 'runner', 'nozzle', 'cover', 'base', 'coupling'];
  api.registerTool({
    name: 'cad_generate_turbine',
    toolset: TOOLSET,
    emoji: '🌊',
    policy: 'pair-gated',
    description:
      'Generate a complete 3D-printable Pelton WATER-TURBINE GENERATOR set ' +
      '(runner, nozzle, housing, cover, base, coupling) as watertight STL files. ' +
      'Parametric: override dimensions (millimetres) such as pitch_radius, ' +
      'n_buckets, jet_d, shaft_d, wall. Returns STL paths + watertight/volume/bbox ' +
      'validation and an optional preview PNG.',
    schema: z.object({
      outputDir: z.string().optional(),
      parts: z.array(z.enum(turbineParts)).optional(),
      dimensions: z.record(z.string(), z.number()).optional(),
      preview: z.boolean().default(true),
    }),
    schemaOverride: {
      type: 'object',
      properties: {
        outputDir: {
          type: 'string',
          description: 'Folder to write STLs into (absolute path recommended).',
        },
        parts: {
          type: 'array',
          items: { type: 'string', enum: turbineParts },
          description: 'Subset of parts to generate. Omit for the full set.',
        },
        dimensions: dimsJson,
        preview: {
          type: 'boolean',
          description: 'Render a preview.png of the parts. Default true.',
        },
      },
      additionalProperties: false,
    },
    handler: async (input) => {
      const out = resolve(input.outputDir || join(defaultOut, 'turbine'));
      const res = await runPython(python, {
        action: 'turbine',
        params: {
          out_dir: out,
          parts: input.parts,
          dimensions: input.dimensions || {},
          preview: input.preview !== false,
        },
      });
      if (!res.ok) return res;
      return {
        ok: true,
        outputDir: res.result.out_dir,
        parts: res.result.parts,
        preview: res.result.preview,
        summary: summarise(res.result),
        note:
          'Impulse turbine — housing contains splash, not pressure; never cap ' +
          'the outlet. Print runner ≥40% infill / 4 perimeters (jet impact).',
      };
    },
  });

  // ──────────────────────── cad_generate_primitive ────────────────────────
  const shapes = ['box', 'cylinder', 'tube', 'cone', 'sphere', 'washer', 'standoff'];
  api.registerTool({
    name: 'cad_generate_primitive',
    toolset: TOOLSET,
    emoji: '🧱',
    policy: 'pair-gated',
    description:
      'Generate a single parametric, 3D-printable part as a watertight STL. ' +
      'Shapes: box {x,y,z}, cylinder {d,h}, tube {od,id,h}, cone {d0,d1,h}, ' +
      'sphere {d}, washer {od,id,t}, standoff {d,h,bore,sides}. All sizes in mm.',
    schema: z.object({
      shape: z.enum(shapes),
      name: z.string().optional(),
      outputDir: z.string().optional(),
      dimensions: z.record(z.string(), z.number()).optional(),
    }),
    schemaOverride: {
      type: 'object',
      properties: {
        shape: { type: 'string', enum: shapes },
        name: { type: 'string', description: 'Output file name (no extension).' },
        outputDir: { type: 'string' },
        dimensions: dimsJson,
      },
      required: ['shape'],
      additionalProperties: false,
    },
    handler: async (input) => {
      const out = resolve(input.outputDir || defaultOut);
      const res = await runPython(python, {
        action: 'primitive',
        params: {
          shape: input.shape,
          name: input.name,
          out_dir: out,
          dimensions: input.dimensions || {},
        },
      });
      if (!res.ok) return res;
      return {
        ok: true,
        outputDir: res.result.out_dir,
        parts: res.result.parts,
        summary: summarise(res.result),
      };
    },
  });

  // ─────────────────────────────── cad_design ─────────────────────────────
  api.registerTool({
    name: 'cad_design',
    toolset: TOOLSET,
    emoji: '✨',
    policy: 'pair-gated',
    description:
      'Generate ANY custom 3D-printable part from Python geometry code you ' +
      'write — not limited to the turbine/primitive templates. Set ' +
      '`result = <solid>` (single part) or `parts = {"name": <solid>, ...}` ' +
      '(assembly). Two backends: backend="mesh" (default) gives the cadlib ' +
      'vocabulary (box, cylinder, tube, cone, sphere, prism, extrude, ' +
      'rounded_polygon, revolve, hull, loft, union/difference/intersect, ' +
      'translate/rotate/scale/mirror, linear_pattern/radial_pattern) on ' +
      'trimesh+manifold3d — robust, always available. backend="brep" gives ' +
      'the full build123d (OpenCascade) vocabulary for fillets, chamfers, ' +
      'lofts, sweeps, sketches and STEP export (requires build123d). Output ' +
      'is auto-validated (watertight) and, on the mesh backend, auto-repaired. ' +
      'Call cad_print_guide first for the API reference and the ' +
      'clarify-before-you-build checklist.',
    schema: z.object({
      code: z.string(),
      name: z.string().optional(),
      backend: z.enum(['mesh', 'brep']).default('mesh'),
      outputDir: z.string().optional(),
      preview: z.boolean().default(true),
      export_step: z.boolean().default(false),
    }),
    schemaOverride: {
      type: 'object',
      properties: {
        code: {
          type: 'string',
          description:
            'Python geometry source. Set `result = <solid>` or `parts = {name: solid}`. ' +
            'mesh backend: cadlib names are pre-imported (no import needed). ' +
            'brep backend: the build123d vocabulary is pre-imported.',
        },
        name: { type: 'string', description: 'Output STL name (no extension). Default "part".' },
        backend: {
          type: 'string',
          enum: ['mesh', 'brep'],
          description: 'mesh = trimesh+manifold3d (default, robust CSG). brep = build123d (fillets/lofts/STEP).',
        },
        outputDir: { type: 'string', description: 'Folder to write the STL(s) into.' },
        preview: { type: 'boolean', description: 'Render preview.png. Default true.' },
        export_step: { type: 'boolean', description: 'brep only — also write a .step file. Default false.' },
      },
      required: ['code'],
      additionalProperties: false,
    },
    handler: async (input) => {
      const out = resolve(input.outputDir || join(defaultOut, 'design'));
      const res = await runPython(python, {
        action: 'design',
        params: {
          code: input.code,
          name: input.name,
          backend: input.backend || 'mesh',
          out_dir: out,
          preview: input.preview !== false,
          export_step: input.export_step === true,
        },
      });
      if (!res.ok) return res;
      return {
        ok: true,
        backend: res.result.backend,
        outputDir: res.result.out_dir,
        parts: res.result.parts,
        preview: res.result.preview,
        summary: summarise(res.result),
      };
    },
  });

  // ─────────────────────────── cad_validate_stl ───────────────────────────
  api.registerTool({
    name: 'cad_validate_stl',
    toolset: TOOLSET,
    emoji: '✅',
    policy: 'pair-gated',
    description:
      'Validate an STL for 3D printing: watertightness, winding consistency, ' +
      'volume (cm³), bounding box (mm), and a printable verdict.',
    schema: z.object({ path: z.string() }),
    schemaOverride: {
      type: 'object',
      properties: { path: { type: 'string', description: 'Absolute path to an .stl' } },
      required: ['path'],
      additionalProperties: false,
    },
    handler: async (input) => {
      const res = await runPython(python, {
        action: 'validate',
        params: { path: resolve(input.path) },
      });
      if (!res.ok) return res;
      return { ok: true, ...res.result };
    },
  });

  // ──────────────────────────── cad_print_guide ───────────────────────────
  api.registerTool({
    name: 'cad_print_guide',
    toolset: TOOLSET,
    emoji: '📐',
    policy: 'open',
    description:
      'Return the CAD/3D-print toolset system prompt and live capability list ' +
      '(how to drive the tools, parameters, safety, and print settings).',
    schema: z.object({}),
    schemaOverride: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => ({
      ok: true,
      systemPrompt: CAD_SYSTEM_PROMPT,
      capabilities: {
        toolset: TOOLSET,
        tools: [
          'cad_generate_turbine',
          'cad_generate_primitive',
          'cad_design',
          'cad_validate_stl',
          'cad_print_guide',
        ],
        primitives: shapes,
        turbineParts,
        design: {
          backends: ['mesh', 'brep'],
          mesh: 'trimesh + manifold3d via cadlib vocabulary (default, always available)',
          brep: 'build123d / OpenCascade — fillets, chamfers, lofts, sweeps, STEP (needs build123d)',
        },
        engine: 'python: trimesh + manifold3d (watertight CSG); optional build123d (B-rep)',
      },
    }),
  });
}

// Named exports so hosts that prefer `pluginEntry` over default also work,
// and re-export the prompt for persona injection.
export { pluginEntry, CAD_SYSTEM_PROMPT };
