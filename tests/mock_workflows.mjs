// Offline harness for the workflow scripts. It runs each script with fake
// agents that write the files a real agent would write, then checks that
// scripts/cs_state.py rebuild agrees with the ratings the workflow computed.
// No model is called.
//
// Usage: node tests/mock_workflows.mjs

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SKILL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const RUN = fs.mkdtempSync(path.join(os.tmpdir(), 'cs-mock-'))
const MODELS = new Set(['opus', 'sonnet', 'haiku', 'fable'])
const EFFORTS = new Set(['low', 'medium', 'high', 'xhigh', 'max'])
const calls = []

const write = (rel, data) => {
  const p = path.join(RUN, rel)
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, typeof data === 'string' ? data : JSON.stringify(data, null, 2))
}
const read = (rel) => JSON.parse(fs.readFileSync(path.join(RUN, rel), 'utf8'))
const py = (...a) => execFileSync('python3', [path.join(SKILL, 'scripts', a[0]), ...a.slice(1)], { encoding: 'utf8' })

function checkSchema(schema, where) {
  if (!schema) return
  if (schema.type === 'object') {
    const props = Object.keys(schema.properties || {})
    for (const r of schema.required || []) assert.ok(props.includes(r), `${where}: required ${r} not in properties`)
    for (const [k, v] of Object.entries(schema.properties || {})) checkSchema(v, `${where}.${k}`)
  }
  if (schema.type === 'array') checkSchema(schema.items, `${where}[]`)
}

function checkCall(prompt, opts) {
  assert.ok(opts && opts.label, 'every agent call needs a label')
  checkSchema(opts.schema, opts.label)
  if (opts.model) assert.ok(MODELS.has(opts.model), `${opts.label}: bad model ${opts.model}`)
  if (opts.effort) assert.ok(EFFORTS.has(opts.effort), `${opts.label}: bad effort ${opts.effort}`)
  for (const m of prompt.matchAll(/roles\/([a-z_]+)\.md/g)) {
    assert.ok(fs.existsSync(path.join(SKILL, 'roles', `${m[1]}.md`)), `${opts.label}: missing role file ${m[1]}`)
  }
  for (const m of prompt.matchAll(/references\/([a-z-]+)\.md/g)) {
    assert.ok(fs.existsSync(path.join(SKILL, 'references', `${m[1]}.md`)), `${opts.label}: missing reference ${m[1]}`)
  }
}

const num = (id) => Number(id.slice(1))

function writeHypothesis(id, origin, round, parents) {
  write(`hypotheses/${id}.md`, `# Hypothesis ${id}\n`)
  write(`hypotheses/${id}.json`, { id, title: `Hypothesis ${id}`, summary: 's', origin, strategy: 'x', parents, round, citations: [] })
  return { id, title: `Hypothesis ${id}`, summary: 's' }
}

// Behaviour of the fake agents, keyed by label prefix.
async function fakeAgent(prompt, opts) {
  checkCall(prompt, opts)
  calls.push(opts.label)
  const [kind, rest] = [opts.label.split(':')[0], opts.label.split(':').slice(1).join(':')]
  const round = Number((prompt.match(/"round": (\d+)/) || [0, 1])[1])
  switch (kind) {
    case 'generate':
      return writeHypothesis(rest, 'generated', round, [])
    case 'evolve':
      return writeHypothesis(rest, 'evolved', round, JSON.parse(prompt.match(/"parents": (\[[^\]]*\])/)[1]))
    case 'review-initial': {
      const pass = num(rest) % 7 !== 2 // H002, H009, ... fail triage
      write(`reviews/${rest}.initial.json`, { id: rest, stage: 'initial', pass, reason: 'r' })
      return { id: rest, pass, reason: 'r' }
    }
    case 'review': {
      const verdict = num(rest) % 7 === 3 ? 'reject' : 'accept'
      write(`reviews/${rest}.json`, { id: rest, stage: 'full', verdict, scores: { novelty: 3, plausibility: 3, testability: 4 } })
      write(`reviews/${rest}.md`, 'review')
      return { id: rest, verdict, novelty: 3, plausibility: 3, testability: 4, one_line: 'ok' }
    }
    case 'dedup': {
      const newIds = prompt.match(/only these may be dropped\): (.*)/)[1].split(', ')
      const file = prompt.match(/Write (rounds\/R\d+\/dedup[a-z-]*\.json)/)[1]
      assert.equal(file, rest.endsWith('-evolved') ? `rounds/${rest.slice(0, -8)}/dedup-evolved.json` : `rounds/${rest}/dedup.json`)
      // H004 duplicates H001 at generation; the evolved H008 duplicates H001 in the second pass.
      const clusters = []
      if (newIds.includes('H004')) clusters.push({ keep: 'H001', drop: ['H004'], reason: 'same' })
      if (newIds.includes('H008')) clusters.push({ keep: 'H001', drop: ['H008'], reason: 'same primary as its parent' })
      write(file, { round, clusters })
      return { clusters }
    }
    case 'judge': {
      const [gameId, pair] = rest.split(':')
      const [match, order] = gameId.split('-')
      const [first, second] = pair.split('-')
      // Lower id wins, except every third match where Hypothesis 1 always wins (position bias).
      let winner = num(first) < num(second) ? first : second
      if (num(match) % 3 === 0) winner = first
      write(`matches/${gameId}.json`, { match, round, swiss: 1, order, first, second, winner, confidence: 'medium', rationale: 'r' })
      return { winner: winner === first ? 1 : 2, confidence: 'medium', rationale: 'r' }
    }
    case 'meta-review':
      write('meta_review.md', 'meta')
      write('overview.md', 'overview')
      return { summary: 'done' }
    case 'design':
    case 'revise': {
      const plan = {
        id: rest, design: 'paired-two-arm', claim: 'c', prediction: 'p', primary_metric: 'acc', direction: 'higher',
        min_effect: 0.01, treatment_arm: 'treatment', control_arm: 'control', seeds: [0, 1, 2, 3, 4], tier: 'local',
        est_minutes: 3, needs_gpu: false, gpu_mem_gb: 0, open_issues: [], round: 1,
      }
      write(`tests/${rest}/plan.json`, plan)
      write(`tests/${rest}/plan.md`, 'plan')
      return { tier: 'local', design: 'paired-two-arm', primary_metric: 'acc', direction: 'higher', min_effect: 0.01, n_seeds: 5, est_minutes: 3, needs_gpu: false }
    }
    case 'critique':
      assert.equal(opts.agentType, 'revisor')
      return num(rest) % 2 === 0 ? { ok: false, must_fix: ['more seeds'], should_fix: [] } : { ok: true, must_fix: [], should_fix: [] }
    case 'implement':
      write(`tests/${rest}/prepare.json`, { id: rest, smoke_ok: true, blocked_reason: null, est_minutes_full: 3, needs_gpu: false, gpu_mem_gb: 0, packages_missing: [] })
      return { smoke_ok: true, blocked_reason: '', est_minutes_full: 3, needs_gpu: false, gpu_mem_gb: 0 }
    case 'execute': {
      // Odd ids: clear positive effect. Even ids: no effect at all.
      for (let s = 0; s < 5; s++) {
        const base = 0.8 + 0.01 * s
        const gain = num(rest) % 2 ? 0.05 + 0.002 * (s % 2) : 0.0005 * (s % 2 ? 1 : -1)
        write(`tests/${rest}/results/control/seed_${s}.json`, { arm: 'control', seed: s, metrics: { acc: base }, config: { seed: s } })
        write(`tests/${rest}/results/treatment/seed_${s}.json`, { arm: 'treatment', seed: s, metrics: { acc: base + gain }, config: { seed: s } })
      }
      write(`tests/${rest}/run.json`, { id: rest, where: 'local', completed: true, seeds_done: {}, failures: [], deviations: [] })
      return { completed: true, seeds_done: 10, failures: [], deviations: [] }
    }
    case 'analyze': {
      py('seed_stats.py', '--test-dir', path.join(RUN, 'tests', rest))
      const stats = read(`tests/${rest}/stats.json`)
      write(`tests/${rest}/verdict.draft.json`, { id: rest, verdict: stats.rule_decision, rule_decision: stats.rule_decision, decided_by: 'rule', notes: '' })
      write(`tests/${rest}/result.md`, 'result')
      return { rule_decision: stats.rule_decision, verdict: stats.rule_decision, summary: 's' }
    }
    case 'audit':
      assert.equal(opts.agentType, 'revisor')
      return { verdict_holds: true, downgrade: false, issues: [] }
    case 'record': {
      const draft = read(`tests/${rest}/verdict.draft.json`)
      const verdict = prompt.match(/"verdict": "([a-z]+)"/)[1]
      write(`tests/${rest}/verdict.json`, Object.assign(draft, { verdict }))
      return { written: true }
    }
    default:
      throw new Error(`unexpected agent label ${opts.label}`)
  }
}

async function parallel(thunks) {
  return Promise.all(thunks.map((t) => Promise.resolve().then(t).catch(() => null)))
}

async function pipeline(items, ...stages) {
  return Promise.all(
    items.map(async (item, i) => {
      let value = item
      for (const stage of stages) {
        try {
          value = await stage(value, item, i)
        } catch (e) {
          console.error(`stage error on item ${i}:`, e)
          return null
        }
      }
      return value
    }),
  )
}

async function runWorkflow(file, args) {
  const src = fs.readFileSync(path.join(SKILL, 'workflows', file), 'utf8')
  const meta = src.match(/export const meta = (\{[\s\S]*?\n\})/)
  assert.ok(meta, `${file}: meta block not found`)
  const phases = new Function(`return ${meta[1]}`)().phases.map((p) => p.title)
  const body = src.replace('export const meta =', 'const meta =')
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
  const fn = new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'args', 'budget', body)
  const realRandom = Math.random
  const realNow = Date.now
  Math.random = () => {
    throw new Error('Math.random is not allowed in workflows')
  }
  Date.now = () => {
    throw new Error('Date.now is not allowed in workflows')
  }
  const phase = (t) => assert.ok(phases.includes(t), `${file}: phase ${t} missing from meta.phases`)
  try {
    return await fn(fakeAgent, parallel, pipeline, phase, (m) => console.log(`  [${file}] ${m}`), args, { total: null })
  } finally {
    Math.random = realRandom
    Date.now = realNow
  }
}

function rebuild(expect) {
  const file = path.join(RUN, 'expect.json')
  fs.writeFileSync(file, JSON.stringify(expect))
  try {
    return py('cs_state.py', 'rebuild', '--run-dir', RUN, '--expect-ratings', file)
  } catch (e) {
    console.error(e.stdout)
    throw new Error('rebuild reported problems')
  }
}

// --- round 1 and round 2 of ideation ---------------------------------------
py('cs_state.py', 'init', '--run-dir', RUN)
write('goal.md', '# Goal\n')
const small = { ideate: { n_generate: 6, swiss_rounds: 2, n_evolve: 3, evolved_swiss_rounds: 2, top_k: 3 } }
fs.writeFileSync(path.join(RUN, 'config.json'), JSON.stringify(Object.assign(read('config.json'), small)))

for (const round of [1, 2]) {
  const summary = JSON.parse(py('cs_state.py', 'summary', '--run-dir', RUN, '--skill-dir', SKILL))
  assert.equal(summary.prior.round, round)
  const out = await runWorkflow('ideate.js', summary)
  console.log(`round ${round}: ${out.ranking.length} ranked, ${out.matches} matches, ${out.position_disagreements} disagreements, rejected ${out.rejected}, duplicates ${JSON.stringify(out.duplicates)}`)
  assert.ok(out.matches > 0 && out.position_disagreements > 0)
  console.log(rebuild(out.expect_ratings).trim())
}
const state = read('state.json')
assert.equal(state.hypotheses.find((h) => h.id === 'H004').status, 'duplicate')
assert.equal(state.hypotheses.find((h) => h.id === 'H008').status, 'duplicate')
assert.ok(calls.includes('dedup:R01-evolved') && calls.includes('dedup:R02-evolved'))
assert.ok(!fs.readdirSync(path.join(RUN, 'matches')).some((f) => read(`matches/${f}`).first === 'H008' || read(`matches/${f}`).second === 'H008'))
assert.equal(state.hypotheses.find((h) => h.id === 'H002').status, 'rejected-initial')
assert.equal(state.hypotheses.find((h) => h.id === 'H003').status, 'rejected')

// --- a user hypothesis enters review and the tournament ----------------------
const before = JSON.parse(py('cs_state.py', 'summary', '--run-dir', RUN, '--skill-dir', SKILL))
const userId = `H${String(before.prior.nextHypothesis).padStart(3, '0')}`
writeHypothesis(userId, 'user', before.prior.round, [])
const withUser = JSON.parse(py('cs_state.py', 'summary', '--run-dir', RUN, '--skill-dir', SKILL))
assert.deepEqual(withUser.prior.unreviewed.map((u) => u.id), [userId])
withUser.config.ideate = { n_generate: 0, swiss_rounds: 1, n_evolve: 0, evolved_swiss_rounds: 0, top_k: 3 }
const out3 = await runWorkflow('ideate.js', withUser)
assert.ok(calls.includes(`review:${userId}`))
console.log(rebuild(out3.expect_ratings).trim())

// --- tests -------------------------------------------------------------------
// One odd id (fake effect: supported) and one even id (no effect: refuted, and its plan gets a must-fix critique).
const activeIds = read('state.json').hypotheses.filter((h) => h.status === 'active').map((h) => h.id)
const top = [activeIds.find((id) => num(id) % 2 === 1), activeIds.find((id) => num(id) % 2 === 0)]
const cfg = read('config.json')
const prep = await runWorkflow('prepare_tests.js', { runDir: RUN, skillDir: SKILL, config: cfg, ids: top, env: { python: 'python3' } })
console.log('prepare:', JSON.stringify(prep.map((p) => [p.id, p.status, p.revised])))
assert.ok(prep.every((p) => p.status === 'ready'))
const ran = await runWorkflow('run_tests.js', { runDir: RUN, skillDir: SKILL, config: cfg, items: top.map((id) => ({ id })), env: { python: 'python3' } })
console.log('run:', JSON.stringify(ran.map((r) => [r.id, r.rule_decision, r.final_verdict])))
const final = read('state.json')
console.log(py('cs_state.py', 'rebuild', '--run-dir', RUN).trim())
const after = read('state.json')
for (const r of ran) {
  const expected = num(r.id) % 2 ? 'supported' : 'refuted'
  assert.equal(r.final_verdict, expected, `${r.id}`)
  assert.equal(after.hypotheses.find((h) => h.id === r.id).status, expected)
}
assert.ok(final)
console.log(`\nOK: ${calls.length} fake agent calls; run dir ${RUN}`)
fs.rmSync(RUN, { recursive: true, force: true })
