#!/usr/bin/env node
// Checks the TLA+ models that live beside the code, with tla-ts.
//
// A spec is checked once per config beside it: Foo.tla pairs with Foo.cfg and
// with each Foo-<variant>.cfg in the same directory. A .tla with no config is a
// helper module, such as an abstract interface, and is checked only through
// the specs that EXTENDS or INSTANCE it. Module names resolve from the spec's
// own directory, then from tools/modules.
//
// A spec with Foo.mutants.json beside it also runs each mutant listed there:
// the spec's source with one or more exact text replacements, checked in
// memory. Each mutant removes a guard the code relies on, so each must break a
// rule; a mutant that passes means the spec does not model what the guard
// protects, or the guard is dead code, and fails the run.
//
// Every result prints its state counts, so a baseline and a simpler candidate
// can be compared over the same bounds. -json prints one JSON object per
// model instead.

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { check, formatValue } from 'tla-ts'

const toolsDir = path.dirname(fileURLToPath(import.meta.url))
const sharedDir = path.join(toolsDir, 'modules')

// Directories never walked for specs.
const skipDirs = new Set(['node_modules', 'vendor', 'dist', 'build', 'testdata'])

// modelsInDir yields { spec, config } for each config directly in dir.
function* modelsInDir(dir, names) {
  const stems = names
    .filter((n) => n.endsWith('.tla'))
    .map((n) => n.slice(0, -4))
    .sort((a, b) => b.length - a.length)
  for (const name of names.filter((n) => n.endsWith('.cfg')).sort()) {
    const cfg = name.slice(0, -4)
    const stem = stems.find((s) => cfg === s || cfg.startsWith(s + '-'))
    if (stem === undefined) continue
    yield { spec: path.join(dir, stem + '.tla'), config: path.join(dir, name) }
  }
}

// findModels yields every model under target: a directory, a .tla or a .cfg.
function* findModels(target) {
  if (fs.statSync(target).isFile()) {
    const dir = path.dirname(target)
    for (const m of modelsInDir(dir, fs.readdirSync(dir))) {
      if (m.spec === target || m.config === target) yield m
    }
    return
  }
  const entries = fs.readdirSync(target, { withFileTypes: true })
  yield* modelsInDir(
    target,
    entries.filter((e) => e.isFile()).map((e) => e.name),
  )
  for (const e of entries) {
    if (!e.isDirectory() || e.name.startsWith('.') || skipDirs.has(e.name))
      continue
    yield* findModels(path.join(target, e.name))
  }
}

// moduleLoader resolves a module name from the spec's directory, then from the
// shared modules.
function moduleLoader(spec) {
  const dirs = [path.dirname(spec), sharedDir]
  return (name) => {
    for (const dir of dirs) {
      const file = path.join(dir, `${name}.tla`)
      if (fs.existsSync(file)) return fs.readFileSync(file, 'utf8')
    }
    return undefined
  }
}

// standardModule names the module that defines each operator a spec most often
// uses without extending it.
const standardModule = {
  '+': 'Naturals', '-': 'Naturals', '*': 'Naturals', '<': 'Naturals',
  '>': 'Naturals', '<=': 'Naturals', '>=': 'Naturals', '\\leq': 'Naturals',
  '\\geq': 'Naturals', '..': 'Naturals', '%': 'Naturals', '\\div': 'Naturals',
  Nat: 'Naturals', Int: 'Integers', Cardinality: 'FiniteSets',
  IsFiniteSet: 'FiniteSets', Seq: 'Sequences', Len: 'Sequences',
  Append: 'Sequences', Head: 'Sequences', Tail: 'Sequences',
  SubSeq: 'Sequences',
}
const missingSymbol = /Could not find declaration or definition of symbol '([^']+)'/
const sourceRange = / line \d+, col \d+ to line \d+, col \d+ of module \w+$/
const location = /^line (\d+), col (\d+) to line (\d+), col (\d+) of module (\w+)$/

// ruleName names a broken rule. tla-ts names a broken temporal formula, such
// as a refinement's [][Next]_vars, by its source position; ruleName shows the
// formula's text and its module instead.
function ruleName(name, source, loadModule) {
  const m = location.exec(name)
  if (!m) return name
  const [, l1, c1, l2, c2, module] = m.map((x, i) => (i > 0 && i < 5 ? Number(x) : x))
  const text = /MODULE\s+(\w+)/.exec(source)?.[1] === module ? source : loadModule(module)
  if (!text) return name
  const lines = text.split('\n').slice(l1 - 1, l2)
  if (!lines.length) return name
  lines[lines.length - 1] = lines[lines.length - 1].slice(0, c2)
  lines[0] = lines[0].slice(c1 - 1)
  return `${lines.join(' ').replace(/\s+/g, ' ')} (${module} line ${l1})`
}

// explain adds the fix to an error whose cause is a missing EXTENDS.
function explain(error) {
  const module = standardModule[missingSymbol.exec(error)?.[1] ?? '']
  return module ? `${error} (add EXTENDS ${module})` : error
}

// describeTrace renders each state of a trace as its step label and the
// variables that changed from the state before.
function describeTrace(trace, loopStart) {
  let prev = {}
  return trace.map((step, i) => {
    const cur = {}
    for (const [k, v] of Object.entries(step.state)) cur[k] = formatValue(v)
    const changed = Object.keys(cur).filter((k) => cur[k] !== prev[k])
    prev = cur
    const label =
      i === 0 ? 'Init' : (step.label ?? step.action ?? 'Next').replace(sourceRange, '')
    const loop = loopStart === i ? '  <- loop starts here' : ''
    return `${i + 1}. ${label}: ${changed.map((k) => `${k} = ${cur[k]}`).join(', ')}${loop}`
  })
}

// run checks one spec source under one config and returns its outcome.
async function run(spec, source, config, workers) {
  const start = performance.now()
  const base = { ok: false, ms: 0, states: 0, generated: 0, depth: 0 }
  const loadModule = moduleLoader(spec)
  try {
    const r = await check({
      source,
      config: fs.readFileSync(config, 'utf8'),
      loadModule,
      workers,
    })
    const out = {
      ...base,
      ms: performance.now() - start,
      states: r.distinctStates,
      generated: r.generatedStates,
      depth: r.depth,
    }
    if (r.status === 'success') return { ...out, ok: true }
    const v = r.violation
    return {
      ...out,
      status: r.status,
      rule: v ? ruleName(v.name, source, loadModule) : r.status,
      error: v?.message ?? r.failure?.message,
      trace: describeTrace(v?.trace ?? r.failure?.trace ?? [], v?.loopStart),
    }
  } catch (err) {
    return {
      ...base,
      ms: performance.now() - start,
      status: 'error',
      rule: 'error',
      error: explain(err instanceof Error ? err.message : String(err)),
    }
  }
}

// applyMutant returns source with each [from, to] replacement applied. Each
// from must occur exactly once, so a mutant cannot go stale silently.
function applyMutant(source, mutant) {
  let out = source
  for (const [from, to] of mutant.replace) {
    const n = out.split(from).length - 1
    if (n !== 1) throw new Error(`text occurs ${n} times, want 1: ${JSON.stringify(from)}`)
    out = out.replace(from, () => to)
  }
  return out
}

// mutantsOf returns the mutants listed beside spec, or [] when it has none.
function mutantsOf(spec) {
  const file = spec.slice(0, -4) + '.mutants.json'
  return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : []
}

// A mutant is killed when the checker finds a counterexample: a broken
// invariant or property, a liveness failure or a deadlock. An error such as a
// parse failure checks nothing and does not count.
const killed = (r) => !r.ok && r.status !== 'error'

// expected reports whether a killed mutant broke the rule its entry expects:
// any rule when expect is unset, else a rule whose name contains expect.
const expected = (r, expect) => killed(r) && (!expect || r.rule.includes(expect))

function printResult(json, kind, name, r, extra = {}) {
  if (json) {
    const { trace, ...rest } = r
    console.log(JSON.stringify({ kind, name, ...extra, ...rest, trace }))
    return
  }
  const verdict =
    kind === 'model' ? (r.ok ? 'pass' : 'FAIL')
    : expected(r, extra.expect) ? 'kill'
    : killed(r) ? 'MISS'
    : 'LIVE'
  const stats = `${String(r.states).padStart(7)} states ${String(r.depth).padStart(3)} deep ${r.ms.toFixed(0).padStart(6)} ms`
  console.log(`${verdict}  ${stats}  ${name}${r.rule ? `  ${r.rule}` : ''}`)
  const showTrace = verdict === 'FAIL' || verdict === 'LIVE' || verdict === 'MISS' || process.env.TLA_MUTANT_TRACES
  if (!showTrace) return
  for (const line of r.error?.split('\n') ?? []) console.log(`      ${line}`)
  for (const line of r.trace ?? []) console.log(`      ${line}`)
}

const usage = `usage: tla [-json] [-workers N] [path...]

Checks every TLA+ spec with a .cfg beside it under each path (default: the
working directory), then each mutant in Foo.mutants.json, which must break a
rule. Prints state counts for every model and the shortest trace to each broken
rule. Exits 1 when a model fails or a mutant survives, and 2 when the
arguments are wrong, such as a missing path or a spec file with no config
beside it. A directory with no models passes, so the check can be set up
before the first spec.`

async function main(argv) {
  let json = false
  let workers = 1
  const paths = []
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '-h' || a === '-help' || a === '--help') {
      console.log(usage)
      return 0
    }
    if (a === '-json') json = true
    else if (a === '-workers') workers = Number(argv[++i])
    else if (a.startsWith('-')) return fail(`unknown flag ${a}`)
    else if (!fs.existsSync(a)) return fail(`no such path: ${a}`)
    else paths.push(a)
  }
  if (!Number.isInteger(workers) || workers < 1)
    return fail('-workers takes a positive integer')
  let models = 0
  let failed = 0
  for (const target of paths.length ? paths : ['.']) {
    const seen = new Set()
    let found = 0
    for (const m of findModels(target)) {
      found++
      models++
      const source = fs.readFileSync(m.spec, 'utf8')
      const r = await run(m.spec, source, m.config, workers)
      printResult(json, 'model', path.relative('.', m.config), r)
      if (!r.ok) failed++
      if (seen.has(m.spec)) continue
      seen.add(m.spec)
      const defaultConfig = m.spec.slice(0, -4) + '.cfg'
      for (const mutant of mutantsOf(m.spec)) {
        const config = mutant.config
          ? path.join(path.dirname(m.spec), mutant.config)
          : fs.existsSync(defaultConfig) ? defaultConfig : m.config
        const name = `${path.relative('.', m.spec)} mutant "${mutant.name}"`
        let mr
        try {
          mr = await run(m.spec, applyMutant(source, mutant), config, workers)
        } catch (err) {
          mr = { ok: false, ms: 0, states: 0, depth: 0, status: 'error', rule: 'stale', error: err.message }
        }
        printResult(json, 'mutant', name, mr, { expect: mutant.expect })
        if (!expected(mr, mutant.expect)) failed++
      }
    }
    if (found === 0 && fs.statSync(target).isFile())
      return fail(`${target} has no Foo.cfg beside its Foo.tla`)
  }
  if (!json) console.log(`${models} model(s), ${failed} failure(s)`)
  return failed ? 1 : 0
}

// fail prints a usage error and returns the exit code for one.
function fail(message) {
  console.error(`tla: ${message}\n\n${usage}`)
  return 2
}

process.exitCode = await main(process.argv.slice(2))
