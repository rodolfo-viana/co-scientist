export const meta = {
  name: 'co-scientist-run-tests',
  description: 'Run approved co-scientist tests locally, analyze them with the pre-registered rule and audit the verdicts',
  whenToUse: 'Called by the /co-scientist skill after the scientist approves the prepared tests',
  phases: [
    { title: 'Execute', detail: 'local runs within the configured time limits (skipped for analyze-only items)' },
    { title: 'Analyze', detail: 'seed statistics, rule decision, draft verdict' },
    { title: 'Audit', detail: 'adversarial check of the conclusion (revisor)' },
    { title: 'Record', detail: 'write the final verdict file' },
  ],
}

if (!args || !args.runDir || !args.skillDir || !Array.isArray(args.items) || !args.items.length) {
  throw new Error('args.runDir, args.skillDir and a non-empty args.items are required')
}
const RUN = args.runDir
const SKILL = args.skillDir
const CFG = args.config || {}
const T = Object.assign(
  { smoke_max_minutes: 5, local_max_minutes_per_hypothesis: 30, command_max_minutes: 9, default_seeds: 5 },
  CFG.tests || {},
)
const MODELS = CFG.models || {}
const LANG = CFG.language === 'pt' ? 'Brazilian Portuguese' : 'English'
const ENV = args.env || {}

function task(role, lines) {
  return [
    `You are an agent in a co-scientist run. Read your role instructions in ${SKILL}/roles/${role}.md and follow them.`,
    `Shared references: ${SKILL}/references/methodology.md, ${SKILL}/references/file-formats.md, ${SKILL}/references/writing-style.md.`,
    `Run directory (every relative path below is inside it): ${RUN}`,
    `Write all files in ${LANG}.`,
    '',
    ...lines,
  ].join('\n')
}

function opts(role, label, phaseTitle, schema) {
  const o = { label, phase: phaseTitle, schema }
  const m = MODELS[role]
  if (m && m.model) o.model = m.model
  if (m && m.effort) o.effort = m.effort
  return o
}

const str = { type: 'string' }
const strList = { type: 'array', items: str }
const VERDICT = { type: 'string', enum: ['supported', 'refuted', 'inconclusive'] }
const EXEC_SCHEMA = {
  type: 'object',
  properties: { completed: { type: 'boolean' }, seeds_done: { type: 'integer' }, failures: strList, deviations: strList },
  required: ['completed', 'seeds_done', 'failures', 'deviations'],
}
const ANALYZE_SCHEMA = {
  type: 'object',
  properties: {
    rule_decision: { type: 'string', enum: ['supported', 'refuted', 'inconclusive', 'none'] },
    verdict: VERDICT,
    summary: str,
  },
  required: ['rule_decision', 'verdict', 'summary'],
}
const AUDIT_SCHEMA = {
  type: 'object',
  properties: { verdict_holds: { type: 'boolean' }, downgrade: { type: 'boolean' }, issues: strList },
  required: ['verdict_holds', 'downgrade', 'issues'],
}
const RECORD_SCHEMA = { type: 'object', properties: { written: { type: 'boolean' } }, required: ['written'] }

async function execute(item) {
  if (item.analyzeOnly) return { id: item.id, exec: null }
  const r = await agent(
    task('runner', [
      `Task: run the approved test tests/${item.id}/ on this machine.`,
      `Python interpreter: ${ENV.python || 'the one in tests/' + item.id + '/prepare.json'}`,
      `Local GPU: ${ENV.local_gpu || 'none declared'}`,
      `Limits: each shell command <= ${T.command_max_minutes} min; total <= ${T.local_max_minutes_per_hypothesis} min for this hypothesis.`,
      `Write tests/${item.id}/run.json with "id": "${item.id}", "where": "local".`,
    ]),
    opts('runner', `execute:${item.id}`, 'Execute', EXEC_SCHEMA),
  )
  if (!r) return null
  return { id: item.id, exec: r }
}

async function analyze(x) {
  if (!x) return null
  const r = await agent(
    task('analyst', [
      `Task: analyze the finished test tests/${x.id}/.`,
      `Statistics script: python3 ${SKILL}/scripts/seed_stats.py --test-dir ${RUN}/tests/${x.id}`,
      `Write tests/${x.id}/result.md and tests/${x.id}/verdict.draft.json with "id": "${x.id}".`,
    ]),
    opts('analyst', `analyze:${x.id}`, 'Analyze', ANALYZE_SCHEMA),
  )
  if (!r) return null
  return Object.assign({}, x, { analysis: r })
}

async function audit(x) {
  if (!x) return null
  const a = await agent(
    [
      'Audit adversarially the conclusion of a finished, pre-registered ML experiment.',
      `Read ${RUN}/hypotheses/${x.id}.md, then everything in ${RUN}/tests/${x.id}/: plan.md, plan.json, code/, launch.sh, run.json, deviations.md if present, results/, stats.json, result.md, verdict.draft.json.`,
      `Draft verdict: ${x.analysis.verdict} (rule decision: ${x.analysis.rule_decision}).`,
      'Look for: evaluation bugs (wrong split, metric computed wrongly, test data used for any choice), leakage, seeds that did not change anything, failed or missing seeds, deviations from the pre-registered plan, an untuned or unfair control, effects within seed noise, and claims in result.md beyond what the numbers show.',
      'Set downgrade to true if any issue makes a supported or refuted verdict unreliable; the verdict then becomes inconclusive. You cannot upgrade a verdict. Write the field contents in English.',
    ].join('\n'),
    { label: `audit:${x.id}`, phase: 'Audit', schema: AUDIT_SCHEMA, agentType: 'revisor' },
  )
  return Object.assign({}, x, { audit: a })
}

async function record(x) {
  if (!x) return null
  const draft = x.analysis.verdict
  let final = draft
  let decidedBy = draft === x.analysis.rule_decision ? 'rule' : 'analyst'
  let auditBlock
  if (!x.audit) {
    auditBlock = { verdict_holds: null, issues: ['audit unavailable'] }
  } else {
    auditBlock = { verdict_holds: x.audit.verdict_holds, issues: x.audit.issues }
    if (x.audit.downgrade && draft !== 'inconclusive') {
      final = 'inconclusive'
      decidedBy = 'audit'
    }
  }
  const r = await agent(
    [
      `Read ${RUN}/tests/${x.id}/verdict.draft.json. Write ${RUN}/tests/${x.id}/verdict.json with the same fields, except:`,
      `"verdict": "${final}", "decided_by": "${decidedBy}", "audit": ${JSON.stringify(auditBlock)}.`,
      'Write valid JSON with the Write tool and change nothing else. Return written: true.',
    ].join('\n'),
    opts('finalizer', `record:${x.id}`, 'Record', RECORD_SCHEMA),
  )
  return Object.assign({}, x, { final, decidedBy, recorded: Boolean(r && r.written) })
}

const results = await pipeline(
  args.items,
  (item) => execute(item),
  (x) => analyze(x),
  (x) => audit(x),
  (x) => record(x),
)

return args.items.map((item, i) => {
  const r = results[i]
  if (!r) return { id: item.id, status: 'failed' }
  return {
    id: item.id,
    executed: r.exec ? { completed: r.exec.completed, seeds_done: r.exec.seeds_done, failures: r.exec.failures } : 'analyze-only',
    rule_decision: r.analysis.rule_decision,
    draft_verdict: r.analysis.verdict,
    final_verdict: r.final,
    decided_by: r.decidedBy,
    audit_issues: r.audit ? r.audit.issues : ['audit unavailable'],
    summary: r.analysis.summary,
    recorded: r.recorded,
  }
})
