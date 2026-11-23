export const meta = {
  name: 'co-scientist-prepare-tests',
  description: 'Pre-register, critique and implement tests for selected co-scientist hypotheses, ending with a smoke run',
  whenToUse: 'Called by the /co-scientist skill after the scientist picks hypotheses to test',
  phases: [
    { title: 'Design', detail: 'pre-registered plan per hypothesis' },
    { title: 'Critique', detail: 'adversarial review of the plan (revisor), then one revision' },
    { title: 'Implement', detail: 'code, launch script and a smoke run; no package installs' },
  ],
}

if (!args || !args.runDir || !args.skillDir || !Array.isArray(args.ids) || !args.ids.length) {
  throw new Error('args.runDir, args.skillDir and a non-empty args.ids are required')
}
if (!args.env || !args.env.python) throw new Error('args.env.python (interpreter for the experiments) is required')
const RUN = args.runDir
const SKILL = args.skillDir
const CFG = args.config || {}
const T = Object.assign(
  { smoke_max_minutes: 5, local_max_minutes_per_hypothesis: 30, command_max_minutes: 9, default_seeds: 5 },
  CFG.tests || {},
)
const MODELS = CFG.models || {}
const WANDB = CFG.wandb || { enabled: false }
const LANG = CFG.language === 'pt' ? 'Brazilian Portuguese' : 'English'
const ENV = args.env
const RUN_NAME = RUN.replace(/\/+$/, '').split('/').pop()

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

const COMPUTE = [
  `Python interpreter for experiments: ${ENV.python}`,
  `Local GPU: ${ENV.local_gpu || 'none declared'}`,
  `Local limits: smoke test <= ${T.smoke_max_minutes} min; full test <= ${T.local_max_minutes_per_hypothesis} min per hypothesis; each shell command <= ${T.command_max_minutes} min. Default seeds: ${T.default_seeds}.`,
  'Anything larger is tier gpu-server and runs only after the scientist approves it.',
]

const str = { type: 'string' }
const strList = { type: 'array', items: str }
const DESIGN_SCHEMA = {
  type: 'object',
  properties: {
    tier: { type: 'string', enum: ['local', 'gpu-server', 'not-cheaply-testable'] },
    design: { type: 'string', enum: ['paired-two-arm', 'custom'] },
    primary_metric: str,
    direction: str,
    min_effect: { type: 'number' },
    n_seeds: { type: 'integer' },
    est_minutes: { type: 'number' },
    needs_gpu: { type: 'boolean' },
  },
  required: ['tier', 'design', 'primary_metric', 'n_seeds', 'est_minutes', 'needs_gpu'],
}
const CRITIQUE_SCHEMA = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, must_fix: strList, should_fix: strList },
  required: ['ok', 'must_fix', 'should_fix'],
}
const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    smoke_ok: { type: 'boolean' },
    blocked_reason: str,
    est_minutes_full: { type: 'number' },
    needs_gpu: { type: 'boolean' },
    gpu_mem_gb: { type: 'number' },
  },
  required: ['smoke_ok', 'est_minutes_full', 'needs_gpu'],
}

async function design(id) {
  return agent(
    task('test_designer', [
      `Task: pre-register a test for ${id} (hypotheses/${id}.md, review reviews/${id}.md).`,
      ...COMPUTE,
      `Write tests/${id}/plan.md and tests/${id}/plan.json with "id": "${id}".`,
    ]),
    opts('test_designer', `design:${id}`, 'Design', DESIGN_SCHEMA),
  )
}

async function critiqueAndRevise(d, id) {
  if (!d) return null
  const crit = await agent(
    [
      `Review adversarially a pre-registered experiment plan for an ML research hypothesis, before any result exists.`,
      `Files: ${RUN}/hypotheses/${id}.md, ${RUN}/tests/${id}/plan.md, ${RUN}/tests/${id}/plan.json. Methodology the plan must follow: ${SKILL}/references/methodology.md.`,
      'Check: can the decision rule actually refute the hypothesis; is min_effect justified; are there enough seeds for the expected effect; is the baseline tuned like the treatment; leakage (test data in any choice); confounders a control could remove; whether a proxy test says what the plan claims; whether the compute estimate and tier are realistic.',
      `Compute available: ${COMPUTE.join(' ')}`,
      'Set ok to false only if there is at least one must-fix item. Write the field contents in English.',
    ].join('\n'),
    { label: `critique:${id}`, phase: 'Critique', schema: CRITIQUE_SCHEMA, agentType: 'revisor' },
  )
  if (!crit) {
    log(`${id}: critique failed; plan kept as designed`)
    return { design: d, critique: null }
  }
  if (crit.ok || !crit.must_fix.length) return { design: d, critique: crit }
  const revised = await agent(
    task('test_designer', [
      `Task: revise the plan for ${id} (tests/${id}/plan.md and plan.json) to address this critique.`,
      'Must fix:',
      crit.must_fix.map((x) => `- ${x}`).join('\n'),
      'Should fix (address when cheap):',
      crit.should_fix.map((x) => `- ${x}`).join('\n') || '- none',
      ...COMPUTE,
    ]),
    opts('test_designer', `revise:${id}`, 'Critique', DESIGN_SCHEMA),
  )
  return { design: revised || d, critique: crit, revised: Boolean(revised) }
}

async function implement(c, id) {
  if (!c || !c.design) return null
  if (c.design.tier === 'not-cheaply-testable') return Object.assign({}, c, { implement: null })
  const wandbLine = WANDB.enabled
    ? `W&B is enabled: project "${WANDB.project}"${WANDB.entity ? `, entity "${WANDB.entity}"` : ''}, group "${RUN_NAME}-${id}".`
    : 'W&B is disabled for this run.'
  const impl = await agent(
    task('implementer', [
      `Task: implement and smoke-test the plan in tests/${id}/plan.json.`,
      ...COMPUTE,
      wandbLine,
      `Write tests/${id}/code/, tests/${id}/launch.sh and tests/${id}/prepare.json (with "python": "${ENV.python}").`,
    ]),
    opts('implementer', `implement:${id}`, 'Implement', IMPL_SCHEMA),
  )
  return Object.assign({}, c, { implement: impl })
}

const results = await pipeline(
  args.ids,
  (id) => design(id),
  (d, id) => critiqueAndRevise(d, id),
  (c, id) => implement(c, id),
)

return args.ids.map((id, i) => {
  const r = results[i]
  if (!r) return { id, status: 'failed' }
  const d = r.design || {}
  const impl = r.implement
  let status = 'ready'
  if (d.tier === 'not-cheaply-testable') status = 'not-cheaply-testable'
  else if (!impl) status = 'implementation-failed'
  else if (!impl.smoke_ok) status = 'blocked'
  else if (d.tier === 'gpu-server' || impl.est_minutes_full > T.local_max_minutes_per_hypothesis) status = 'needs-server'
  return {
    id,
    status,
    tier: d.tier,
    design: d.design,
    primary_metric: d.primary_metric,
    direction: d.direction,
    min_effect: d.min_effect,
    n_seeds: d.n_seeds,
    est_minutes: impl ? impl.est_minutes_full : d.est_minutes,
    needs_gpu: impl ? impl.needs_gpu : d.needs_gpu,
    gpu_mem_gb: impl ? impl.gpu_mem_gb : null,
    blocked_reason: impl ? impl.blocked_reason || null : null,
    critique_must_fix: r.critique ? r.critique.must_fix : null,
    revised: Boolean(r.revised),
  }
})
