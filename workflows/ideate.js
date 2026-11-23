export const meta = {
  name: 'co-scientist-ideate',
  description: 'One co-scientist round: generate, review, deduplicate, rank in an Elo tournament, evolve and meta-review hypotheses',
  whenToUse: 'Called by the /co-scientist skill with the args printed by scripts/cs_state.py summary',
  phases: [
    { title: 'Generate and review', detail: 'generation agents, then initial and full review of each hypothesis' },
    { title: 'Deduplicate', detail: 'one proximity agent over the pool' },
    { title: 'Tournament', detail: 'Swiss rounds; each match is judged in both presentation orders' },
    { title: 'Evolve and review', detail: 'new hypotheses built from the top of the ranking' },
    { title: 'Tournament (evolved)', detail: 'Swiss rounds including the evolved hypotheses' },
    { title: 'Meta-review', detail: 'feedback for the next round and the research overview' },
  ],
}

// ---------------------------------------------------------------------------
// Inputs. The run directory is the source of truth; this script only keeps
// the in-memory ratings needed to schedule matches. scripts/cs_state.py
// rebuild recomputes everything from the files the agents write.
// ---------------------------------------------------------------------------

if (!args || !args.runDir || !args.skillDir) {
  throw new Error('args.runDir and args.skillDir are required: pass the JSON printed by scripts/cs_state.py summary')
}
const RUN = args.runDir
const SKILL = args.skillDir
const CFG = args.config || {}
const P = Object.assign(
  { n_generate: 6, swiss_rounds: 3, n_evolve: 3, evolved_swiss_rounds: 2, top_k: 4, elo_k: 32, elo_init: 1200 },
  CFG.ideate || {},
)
const MODELS = CFG.models || {}
const LANG = CFG.language === 'pt' ? 'Brazilian Portuguese' : 'English'
const PRIOR = args.prior || {}
const ROUND = PRIOR.round || 1

const pad = (n, w) => String(n).padStart(w, '0')
const RLABEL = `R${pad(ROUND, 2)}`
let nextH = PRIOR.nextHypothesis || 1
let nextM = PRIOR.nextMatch || 1
const newHypId = () => `H${pad(nextH++, 3)}`
const newMatchId = () => `M${pad(nextM++, 4)}`

const ratings = {}
const titles = {}
const active = new Set()
for (const h of PRIOR.known || []) titles[h.id] = h.title
for (const h of PRIOR.active || []) {
  active.add(h.id)
  titles[h.id] = h.title
}
for (const [id, r] of Object.entries(PRIOR.ratings || {})) {
  ratings[id] = r
  active.add(id)
}
for (const id of active) if (ratings[id] === undefined) ratings[id] = P.elo_init
const played = new Set(PRIOR.playedPairs || [])
const touched = new Set() // hypotheses that played in this run
const matches = []
let disagreements = 0
const rejected = []
const duplicates = []
const failures = []

// ---------------------------------------------------------------------------
// Prompts and schemas
// ---------------------------------------------------------------------------

function task(role, lines) {
  return [
    `You are an agent in a co-scientist run. Read your role instructions in ${SKILL}/roles/${role}.md and follow them.`,
    `Shared references: ${SKILL}/references/methodology.md, ${SKILL}/references/literature.md, ${SKILL}/references/file-formats.md, ${SKILL}/references/writing-style.md.`,
    `Run directory (every relative path below is inside it): ${RUN}`,
    `Write all files in ${LANG}.`,
    '',
    ...lines,
  ].join('\n')
}

function opts(role, label, phaseTitle, schema) {
  const o = { label, phase: phaseTitle }
  if (schema) o.schema = schema
  const m = MODELS[role]
  if (m && m.model) o.model = m.model
  if (m && m.effort) o.effort = m.effort
  return o
}

const str = { type: 'string' }
const int = { type: 'integer' }
const GEN_SCHEMA = {
  type: 'object',
  properties: { id: str, title: str, summary: str },
  required: ['id', 'title', 'summary'],
}
const INITIAL_SCHEMA = {
  type: 'object',
  properties: { id: str, pass: { type: 'boolean' }, reason: str },
  required: ['id', 'pass', 'reason'],
}
const FULL_SCHEMA = {
  type: 'object',
  properties: {
    id: str,
    verdict: { type: 'string', enum: ['accept', 'revise', 'reject'] },
    novelty: int,
    plausibility: int,
    testability: int,
    one_line: str,
  },
  required: ['id', 'verdict', 'novelty', 'plausibility', 'testability', 'one_line'],
}
const DEDUP_SCHEMA = {
  type: 'object',
  properties: {
    clusters: {
      type: 'array',
      items: {
        type: 'object',
        properties: { keep: str, drop: { type: 'array', items: str }, reason: str },
        required: ['keep', 'drop', 'reason'],
      },
    },
  },
  required: ['clusters'],
}
const GAME_SCHEMA = {
  type: 'object',
  properties: { winner: { type: 'integer', enum: [1, 2] }, confidence: str, rationale: str },
  required: ['winner', 'confidence', 'rationale'],
}
const META_SCHEMA = { type: 'object', properties: { summary: str }, required: ['summary'] }

const GEN_STRATEGIES = [
  { key: 'literature', brief: 'Survey recent and foundational work, find an open gap or an unexplained result, and address it.' },
  { key: 'debate', brief: 'Simulate a debate among three experts with different priors until one hypothesis survives.' },
  { key: 'assumptions', brief: 'Find a testable, possibly false assumption the field makes and build on its failure.' },
  { key: 'expansion', brief: 'Propose a direction that none of the existing hypotheses, reviews or test verdicts covers.' },
  { key: 'analogy', brief: 'Transfer a mechanism from a neighbouring field and state what it predicts here.' },
  { key: 'anomaly', brief: 'Explain a reported anomaly, negative result or failed replication related to the goal.' },
]

const EVOLVE_STRATEGIES = [
  { key: 'grounding', parents: 1 },
  { key: 'combination', parents: 2 },
  { key: 'simplification', parents: 1 },
  { key: 'divergent', parents: 0 },
  { key: 'feasibility', parents: 1 },
]

const byRating = (a, b) => ratings[b] - ratings[a] || (a < b ? -1 : 1)
const knownList = () =>
  Object.keys(titles).sort().map((id) => `- ${id}: ${titles[id]}`).join('\n') || '(none yet)'
const rankingText = () =>
  [...active].sort(byRating).map((id, i) => `${i + 1}. ${id} (Elo ${ratings[id].toFixed(0)}): ${titles[id] || ''}`).join('\n') ||
  '(empty)'

// ---------------------------------------------------------------------------
// Stages
// ---------------------------------------------------------------------------

async function generate(item, phaseTitle) {
  const s = item.strategy
  const r = await agent(
    task('generator', [
      'Task: propose one new hypothesis.',
      `Strategy: ${s.key}. ${s.brief}`,
      `Assigned id: ${item.id}. Write hypotheses/${item.id}.md and hypotheses/${item.id}.json with "id": "${item.id}", "origin": "generated", "strategy": "${s.key}", "parents": [], "round": ${ROUND}.`,
      'Existing hypotheses (yours must differ from all of them in mechanism or prediction):',
      knownList(),
      'Other agents generate in parallel with other strategies, so commit to your strategy.',
    ]),
    opts('generator', `generate:${item.id}`, phaseTitle, GEN_SCHEMA),
  )
  if (!r) return null
  return { id: item.id, title: r.title, summary: r.summary }
}

async function evolve(item, phaseTitle) {
  const parentText = item.parents.length
    ? item.parents.map((p) => `- ${p}: hypotheses/${p}.md (review: reviews/${p}.md)`).join('\n')
    : '(none: read the top of the ranking below)'
  const r = await agent(
    task('evolver', [
      `Task: create one new hypothesis with the ${item.strategy.key} strategy.`,
      'Parents:',
      parentText,
      `Assigned id: ${item.id}. Write hypotheses/${item.id}.md and hypotheses/${item.id}.json with "id": "${item.id}", "origin": "evolved", "strategy": "${item.strategy.key}", "parents": ${JSON.stringify(item.parents)}, "round": ${ROUND}.`,
      'Current ranking:',
      rankingText(),
      'All existing hypotheses (yours must differ from all of them):',
      knownList(),
    ]),
    opts('evolver', `evolve:${item.id}`, phaseTitle, GEN_SCHEMA),
  )
  if (!r) return null
  return { id: item.id, title: r.title, summary: r.summary }
}

async function initialReview(h, phaseTitle) {
  if (!h) return null
  const r = await agent(
    task('reviewer_initial', [
      `Task: initial review of ${h.id} (hypotheses/${h.id}.md).`,
      `Write reviews/${h.id}.initial.json with "id": "${h.id}".`,
    ]),
    opts('reviewer_initial', `review-initial:${h.id}`, phaseTitle, INITIAL_SCHEMA),
  )
  if (!r) return null
  return Object.assign({}, h, { initialPass: r.pass, initialReason: r.reason })
}

async function fullReview(h, phaseTitle) {
  if (!h || h.initialPass === false) return h
  const r = await agent(
    task('reviewer', [
      `Task: full review of ${h.id} (hypotheses/${h.id}.md and hypotheses/${h.id}.json).`,
      `Write reviews/${h.id}.md, reviews/${h.id}.json (with "id": "${h.id}") and a literature record for every source you cite.`,
    ]),
    opts('reviewer', `review:${h.id}`, phaseTitle, FULL_SCHEMA),
  )
  if (!r) return h
  return Object.assign({}, h, { verdict: r.verdict, oneLine: r.one_line })
}

// Sorts pipeline results into accepted, rejected and failed.
function settle(results, items) {
  const ok = []
  results.forEach((h, i) => {
    const id = items[i].id
    if (!h) {
      failures.push(id)
      return
    }
    titles[id] = h.title || titles[id] || id
    if (h.initialPass === false || h.verdict === 'reject') rejected.push(id)
    else if (!h.verdict) failures.push(id)
    else ok.push(h)
  })
  return ok
}

function pairKey(a, b) {
  return a < b ? `${a}|${b}` : `${b}|${a}`
}

// Swiss pairing: neighbours in the ranking, avoiding rematches when possible.
// With an odd count, the bye goes to the lowest-ranked hypothesis that has not
// had one in this run, so the bottom of the table still plays.
const byes = new Set()
function swissPairs(ids) {
  const order = [...ids].sort(byRating)
  if (order.length % 2 === 1) {
    let k = order.length - 1
    while (k > 0 && byes.has(order[k])) k--
    byes.add(order[k])
    order.splice(k, 1)
  }
  const used = new Set()
  const pairs = []
  for (let i = 0; i < order.length; i++) {
    const a = order[i]
    if (used.has(a)) continue
    let partner = null
    for (let j = i + 1; j < order.length && !partner; j++) {
      const b = order[j]
      if (!used.has(b) && !played.has(pairKey(a, b))) partner = b
    }
    for (let j = i + 1; j < order.length && !partner; j++) {
      if (!used.has(order[j])) partner = order[j]
    }
    if (partner) {
      used.add(a)
      used.add(partner)
      pairs.push([a, partner])
    }
  }
  return pairs
}

// Must stay identical to elo_update in scripts/cs_state.py.
function applyElo(a, b, scoreA) {
  const ea = 1 / (1 + Math.pow(10, (ratings[b] - ratings[a]) / 400))
  const ra = ratings[a] + P.elo_k * (scoreA - ea)
  const rb = ratings[b] + P.elo_k * (1 - scoreA - (1 - ea))
  ratings[a] = ra
  ratings[b] = rb
}

async function game(matchId, swiss, first, second, order, phaseTitle) {
  const r = await agent(
    task('judge', [
      `Task: judge match ${matchId}, game ${order}.`,
      `Hypothesis 1: hypotheses/${first}.md, review: reviews/${first}.md`,
      `Hypothesis 2: hypotheses/${second}.md, review: reviews/${second}.md`,
      `Write matches/${matchId}-${order}.json with "match": "${matchId}", "round": ${ROUND}, "swiss": ${swiss}, "order": "${order}", "first": "${first}", "second": "${second}", and "winner" set to "${first}" or "${second}".`,
      `Return winner 1 if ${first} wins, 2 if ${second} wins, matching the file.`,
    ]),
    opts('judge', `judge:${matchId}-${order}:${first}-${second}`, phaseTitle, GAME_SCHEMA),
  )
  if (!r || (r.winner !== 1 && r.winner !== 2)) return null
  return r.winner === 1 ? first : second
}

async function tournament(nRounds, phaseTitle, swissOffset) {
  for (let s = 1; s <= nRounds; s++) {
    const ids = [...active]
    if (ids.length < 2) {
      log('Fewer than two active hypotheses: tournament skipped')
      return
    }
    const pairs = swissPairs(ids)
    const scheduled = pairs.map(([a, b]) => ({ id: newMatchId(), a, b }))
    const rematches = scheduled.filter((m) => played.has(pairKey(m.a, m.b))).length
    const outcomes = await parallel(
      scheduled.flatMap((m) => [
        () => game(m.id, swissOffset + s, m.a, m.b, 'ab', phaseTitle),
        () => game(m.id, swissOffset + s, m.b, m.a, 'ba', phaseTitle),
      ]),
    )
    // Apply results in match-id order so the ratings do not depend on timing.
    scheduled.forEach((m, k) => {
      const wins = [outcomes[2 * k], outcomes[2 * k + 1]].filter(Boolean)
      if (!wins.length) {
        log(`${m.id}: both games failed, match skipped`)
        return
      }
      if (wins.length === 2 && wins[0] !== wins[1]) disagreements++
      const scoreA = wins.filter((w) => w === m.a).length / wins.length
      applyElo(m.a, m.b, scoreA)
      played.add(pairKey(m.a, m.b))
      touched.add(m.a)
      touched.add(m.b)
      matches.push({ id: m.id, a: m.a, b: m.b, score_a: scoreA, games: wins.length })
    })
    const odd = ids.length % 2 === 1 ? ' (one bye)' : ''
    log(`Swiss round ${swissOffset + s}: ${scheduled.length} matches${odd}, ${rematches} rematches; leader ${[...active].sort(byRating)[0]}`)
  }
}

// ---------------------------------------------------------------------------
// Round
// ---------------------------------------------------------------------------

const estimate =
  (P.n_generate + (PRIOR.unreviewed || []).length) * 3 + 1 +
  P.swiss_rounds * (active.size + P.n_generate) +
  P.n_evolve * 3 +
  P.evolved_swiss_rounds * (active.size + P.n_generate + P.n_evolve) + 1
log(`Round ${ROUND}: about ${estimate} agent calls (upper bound); ${active.size} hypotheses carried over`)

phase('Generate and review')
const PH1 = 'Generate and review'
const genItems = []
for (let i = 0; i < P.n_generate; i++) {
  genItems.push({ kind: 'generate', id: newHypId(), strategy: GEN_STRATEGIES[i % GEN_STRATEGIES.length] })
}
for (const u of PRIOR.unreviewed || []) genItems.push({ kind: 'existing', id: u.id, title: u.title })
const generated = await pipeline(
  genItems,
  (item) => (item.kind === 'existing' ? { id: item.id, title: item.title, summary: '' } : generate(item, PH1)),
  (h) => initialReview(h, PH1),
  (h) => fullReview(h, PH1),
)
const fresh = settle(generated, genItems)
log(`Generated or reviewed ${genItems.length}: ${fresh.length} passed, ${rejected.length} rejected, ${failures.length} failed`)

phase('Deduplicate')
const pool = [...active, ...fresh.map((h) => h.id)]
let kept = fresh
if (fresh.length > 0 && pool.length >= 3) {
  const freshIds = new Set(fresh.map((h) => h.id))
  const d = await agent(
    task('deduplicator', [
      `Task: find duplicates in round ${RLABEL}.`,
      'Pool (read each hypotheses/<id>.md):',
      pool.map((id) => `- ${id}: ${titles[id] || ''}`).join('\n'),
      `New this round (only these may be dropped): ${[...freshIds].join(', ')}`,
      `Write rounds/${RLABEL}/dedup.json with "round": ${ROUND}.`,
    ]),
    opts('deduplicator', `dedup:${RLABEL}`, 'Deduplicate', DEDUP_SCHEMA),
  )
  if (d && Array.isArray(d.clusters)) {
    for (const c of d.clusters) {
      for (const x of c.drop || []) {
        if (freshIds.has(x) && x !== c.keep && pool.includes(c.keep)) duplicates.push({ id: x, of: c.keep })
      }
    }
  } else {
    log('Deduplication failed: all new hypotheses kept')
  }
  const dropped = new Set(duplicates.map((x) => x.id))
  kept = fresh.filter((h) => !dropped.has(h.id))
} else {
  log('Deduplication skipped (pool smaller than three or nothing new)')
}
for (const h of kept) {
  active.add(h.id)
  ratings[h.id] = P.elo_init
}

phase('Tournament')
await tournament(P.swiss_rounds, 'Tournament', 0)

const evolvedIds = []
if (P.n_evolve > 0 && active.size > 0) {
  phase('Evolve and review')
  const PH2 = 'Evolve and review'
  const top = [...active].sort(byRating).slice(0, P.top_k)
  const evoItems = []
  for (let i = 0; i < P.n_evolve; i++) {
    const s = EVOLVE_STRATEGIES[i % EVOLVE_STRATEGIES.length]
    let parents = []
    if (s.parents === 1) parents = [top[i % top.length]]
    if (s.parents === 2) parents = top.length >= 2 ? [top[i % top.length], top[(i + 1) % top.length]] : [top[0]]
    evoItems.push({ id: newHypId(), strategy: s, parents })
  }
  const evolvedResults = await pipeline(
    evoItems,
    (item) => evolve(item, PH2),
    (h) => initialReview(h, PH2),
    (h) => fullReview(h, PH2),
  )
  for (const h of settle(evolvedResults, evoItems)) {
    active.add(h.id)
    ratings[h.id] = P.elo_init
    evolvedIds.push(h.id)
  }
  log(`Evolved ${evoItems.length}: ${evolvedIds.length} entered the tournament`)
  if (evolvedIds.length > 0) {
    phase('Tournament (evolved)')
    await tournament(P.evolved_swiss_rounds, 'Tournament (evolved)', P.swiss_rounds)
  }
}

phase('Meta-review')
const metaResult = await agent(
  task('meta_reviewer', [
    `Task: close round ${RLABEL}.`,
    'Ranking after this round:',
    rankingText(),
    `Matches this round: ${matches.length} (files matches/${matches.length ? matches[0].id : 'none'} to ${matches.length ? matches[matches.length - 1].id : 'none'}); ${disagreements} of them had games that disagreed when the order was swapped.`,
    `Rejected this round: ${rejected.join(', ') || 'none'}. Duplicates dropped: ${duplicates.map((d) => `${d.id} (of ${d.of})`).join(', ') || 'none'}.`,
    'Write meta_review.md and overview.md.',
  ]),
  opts('meta_reviewer', `meta-review:${RLABEL}`, 'Meta-review', META_SCHEMA),
)

const expectRatings = {}
for (const id of touched) expectRatings[id] = ratings[id]
for (const [id, r] of Object.entries(PRIOR.ratings || {})) {
  if (r !== P.elo_init) expectRatings[id] = ratings[id]
}

return {
  round: ROUND,
  ranking: [...active].sort(byRating).map((id) => ({ id, title: titles[id], elo: Math.round(ratings[id] * 100) / 100 })),
  new_hypotheses: fresh.map((h) => h.id).concat(evolvedIds),
  rejected,
  duplicates,
  failures,
  matches: matches.length,
  position_disagreements: disagreements,
  expect_ratings: expectRatings,
  meta_summary: metaResult ? metaResult.summary : 'meta-review failed',
}
