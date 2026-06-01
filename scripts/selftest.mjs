/**
 * selftest.mjs — load the plugin with a mock PluginAPI and exercise every
 * tool, mirroring how @swarmai/plugin-loader + @swarmai/tools drive it.
 *
 *   node scripts/selftest.mjs
 */
import { pathToFileURL } from 'node:url';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const entry = pathToFileURL(join(root, 'dist', 'index.js')).href;

// Minimal stand-in for @swarmai/tools' registry, validating like the host:
// it runs schema.safeParse(input) before calling the handler (registry.ts).
const tools = new Map();
const api = {
  registerTool(def) {
    if (!def?.name || !def?.schema || typeof def.handler !== 'function') {
      throw new Error(`bad ToolDef: ${JSON.stringify(Object.keys(def || {}))}`);
    }
    tools.set(def.name, def);
    console.log(`  registered ${def.emoji || '•'} ${def.name}  [${def.toolset}/${def.policy}]`);
  },
  // unused capabilities — present so a misbehaving plugin can't crash on them
  registerProvider() {}, registerChannel() {}, registerMemoryProvider() {},
  registerMonitorSource() {}, registerImageGenerationProvider() {},
  registerSpeechProvider() {}, registerTranscriptionProvider() {},
  registerMediaUnderstandingProvider() {}, registerService() {},
};

const ctx = { sessionId: 'selftest', agentId: 'selftest', isMain: true };

async function call(name, input) {
  const def = tools.get(name);
  if (!def) throw new Error(`tool not registered: ${name}`);
  const parsed = def.schema.safeParse(input); // host-equivalent validation
  if (!parsed.success) {
    throw new Error(`schema rejected input for ${name}: ${parsed.error}`);
  }
  return def.handler(parsed.data, ctx);
}

function assert(cond, msg) {
  if (!cond) { console.error('  ✗ FAIL:', msg); process.exitCode = 1; }
  else console.log('  ✓', msg);
}

const out = join(root, 'output', 'selftest');

console.log('\n[1] loading plugin entry …');
const mod = await import(entry);
const entryFn = mod.default ?? mod.pluginEntry;
assert(typeof entryFn === 'function', 'default export is a PluginEntry function');
assert(typeof mod.CAD_SYSTEM_PROMPT === 'string' && mod.CAD_SYSTEM_PROMPT.length > 200,
  'CAD_SYSTEM_PROMPT exported for persona injection');

console.log('\n[2] registering tools …');
await entryFn(api, { outputDir: out });
assert(tools.size === 4, `4 tools registered (got ${tools.size})`);

console.log('\n[3] cad_print_guide …');
const guide = await call('cad_print_guide', {});
assert(guide.ok && guide.systemPrompt.includes('Pelton'), 'print guide returns system prompt');

console.log('\n[4] cad_generate_primitive (tube) …');
const prim = await call('cad_generate_primitive', {
  shape: 'tube', name: 'selftest_tube', dimensions: { od: 24, id: 16, h: 18 },
});
assert(prim.ok, 'primitive ok');
assert(prim.parts?.[0]?.watertight === true, 'primitive tube is watertight');
console.log('   ', prim.summary?.[0]);

console.log('\n[5] cad_validate_stl …');
const val = await call('cad_validate_stl', { path: prim.parts[0].path });
assert(val.ok && val.printable === true, 'validate reports printable=true');

console.log('\n[6] cad_generate_turbine (runner + nozzle, no preview) …');
const turb = await call('cad_generate_turbine', {
  parts: ['runner', 'nozzle'], preview: false,
});
assert(turb.ok, 'turbine ok');
assert(turb.parts?.every((p) => p.watertight), 'all turbine parts watertight');
for (const line of turb.summary || []) console.log('   ', line);

console.log('\nselftest complete.');
