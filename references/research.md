# Deep research loop

Used in Phase 0, and as the whole harness when the request is research rather
than a build. The goal is a brief someone could act on, not a summary someone
could have written from the first page of results.

## Four angles, not four queries

Decompose the topic into orthogonal angles and give each one its own agent.
Angles fan out in parallel because they are read-only.

1. **Mechanics** — how does this actually work underneath? What are the moving
   parts, the data flow, the failure surface?
2. **Practice** — how are people building this right now? Prefer engineering
   posts, post-mortems, real repos, conference talks over tutorials.
3. **Primitives** — the concrete APIs, CLI flags, config keys, file formats,
   library names. Exact strings, not paraphrases.
4. **Pitfalls** — where it breaks, what the known bugs and limits are, what
   people migrated away from and why.

Each agent returns a compressed report into `.relay/research/<angle>.md`.
You read the reports. You do not read the raw sources.

## Gap analysis

After the first pass, before writing anything, ask:

- What could I not name precisely? A version, a flag, a field, a threshold.
- What claim rests on exactly one source?
- What would break if I were wrong about it?
- What would someone implementing this next week still have to figure out?

Every gap becomes a second-pass query. Run those in parallel too. Repeat until
gaps stop producing new answers — usually two passes, occasionally three.
Stop when a round returns nothing new, not when you hit a round count.

## Triangulation

- Cross-check any load-bearing claim across independent sources. Official docs,
  a practitioner writeup, and source code are three; three blog posts quoting
  the same announcement are one.
- Mark confidence explicitly in the brief: verified, single-source, or
  inferred. Say plainly what you could not verify. An honest gap is more
  useful than a confident guess.
- Discard marketing copy and overview tutorials. If a source has no numbers,
  no code, and no failure story, it is not evidence.
- Prefer primary artifacts: the repo, the changelog, the spec, the captured
  config.

## Output shape

1. **Synthesis** — the core takeaway in two paragraphs. What someone needs to
   understand before the details make sense.
2. **Landscape table** — approaches, tools, or primitives compared on the axes
   that actually decide between them.
3. **Mechanics** — how the pieces fit, step by step.
4. **Implementation** — complete, untruncated config, code, or prompt. No
   ellipses, no "and so on".
5. **Tradeoffs and failure modes** — with concrete mitigations.
6. **Sources** — links, with the confidence marks.
