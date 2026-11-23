# Literature search and citations

## Tools

Load MCP tools with ToolSearch before calling them, several in one query, for
example `select:mcp__lacuna__search_lacuna,mcp__lacuna__get_paper`. Use them in
this order and fall back when one is missing or returns nothing:

1. Lacuna, a knowledge graph of ML and AI research:
   `mcp__lacuna__search_lacuna` (search_type `paper`), then
   `mcp__lacuna__get_paper` on the few results that matter.
2. OpenAlex: `mcp__openalex__search_works`, `mcp__openalex__get_work`
   (full abstract, venue and ids).
3. Reference lookup (OpenAlex plus arXiv):
   `mcp__plugin_research-desk_reference-lookup__search_works`,
   `mcp__plugin_research-desk_reference-lookup__lookup_reference`,
   `mcp__plugin_research-desk_reference-lookup__verify_reference`.
   The same tools may appear under `...reference-lookup-hosted__...`.
4. WebSearch and WebFetch for OpenReview, DBLP, ACL Anthology and the official
   proceedings (proceedings.neurips.cc, proceedings.mlr.press,
   openaccess.thecvf.com).

Record in your output which sources you searched and with what queries, so a
reader can tell an absent result from an unsearched one.

## Rules

- Cite only sources you retrieved in this session with a tool. Never cite from
  memory, and never invent authors, years, venues, titles or numbers.
- For every source you cite, write `literature/<key>.json` (format in
  file-formats.md) with the abstract copied verbatim from the tool output.
- Every citation in a JSON file carries a `snippet`: an exact quote of at most
  30 words from that stored abstract supporting the `claim`. The rebuild
  script checks the snippet against the stored abstract and, with `--online`,
  against OpenAlex or arXiv.
- Prefer the peer-reviewed version (conference or journal). If an arXiv paper
  has a published version, cite the published one. Set `peer_reviewed: false`
  for preprints and say "preprint" where you use them.
- A claim that no retrieved source supports is written as your own reasoning,
  not attributed to the literature.
- Novelty is a claim about the literature. Before calling a hypothesis novel,
  search with at least three different phrasings of its core mechanism and
  record the closest work you found.
