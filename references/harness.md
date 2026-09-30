# The harness: capabilities, not tool names

Relay is a way of working, not a plugin for one product. It needs seven things
from whatever agent harness it is running inside, and every harness spells them
differently. **Name the capability in the skill; look the spelling up here.**

A coach that writes a vendor's tool name into a leg briefing has hard-coded the
relay to one product. A coach that writes "dispatch a fresh agent with clean
context" has not, and the pre-flight below tells it how that is spelled today.

## The capability pre-flight

**Run this at kickoff, before Phase 0, and record the answers in `relay.md`.**
Two minutes here is what stops a relay planning a fan-out the harness cannot
perform and discovering it at leg nine.

| # | Capability | How to establish it |
|---|---|---|
| 1 | **Fresh agent, clean context** — the thing a leg runs in | Is there a subagent or task primitive? If not, a leg is a fresh top-level session and the human or a script starts it. |
| 2 | **Parallel dispatch** — several legs at once | Can several be started in one turn, or in the background? If not, the relay is serial and the plan should stop pretending otherwise. |
| 3 | **Read-only fan-out** — search, research, review | Almost always yes, and it is the cheapest capability. Use it hardest. |
| 4 | **Nested dispatch** — can a dispatched agent dispatch its own? | **Assume no.** See the rule below. |
| 5 | **Model per role** — a judge on a different model from the runner | Per-agent model pinning, a complexity tier, or nothing. Record which. |
| 6 | **Project conventions file** | Read whichever of `AGENTS.md`, `CLAUDE.md`, `.cursor/rules` exists. More than one may. |
| 7 | **A terminal for the dashboard** | Can this session hand a TTY to a full-screen program? Usually not. The HTML render always works. |

Write the answers as a block in `relay.md`:

```
## Harness

Harness: <name and version>
Fresh agent: <the primitive, or "fresh top-level session">
Parallel: yes, <n> at once | no
Nested dispatch: no
Model per role: <mechanism, or "one model for every role">
Conventions: AGENTS.md | CLAUDE.md | both | none found
Dashboard: HTML render | live TUI in its own window
```

## The rule that matters most: assume nested dispatch is unavailable

Several harnesses forbid a dispatched agent from dispatching its own, and at
least one does so explicitly: in Factory a subagent has no Task tool at all.
Relay used to depend on nesting twice, and both were silent failures rather than
errors — the work simply collapsed into one context:

- a **runner** spawning read-only subagents to trace flows
- a **code judge** spawning one review subagent per completed leg, in parallel

So: **the coach owns every fan-out.** A judge that needs eight per-leg reviews
gets them as eight read-only legs the coach dispatched, and synthesises their
reports. A runner that needs a deep read asks for it in its briefing, or the
coach dispatches the read as its own read-only leg before the runner starts.
Where a harness does allow nesting, treat it as a bonus that shortens a round,
never as something the plan depends on.

## Known spellings

Verified against each product's own documentation at the time of writing. A
harness changes under you, so the pre-flight above is the authority, not this
table.

| | Claude Code | Factory (droid) | Codex CLI | Cursor |
|---|---|---|---|---|
| Skill lives in | `~/.claude/skills/<n>/` | `~/.factory/skills/<n>/` or `.factory/skills/<n>/` | no skill loader; paste or reference the file | no skill loader; a rule file or a referenced path |
| Fresh agent | Task / subagent | Task with `subagent_type` | fresh `codex exec` run | fresh chat or background agent |
| Agent definitions | `.claude/agents/*.md` | `.factory/droids/*.md` | none | none |
| Built-in agents | general-purpose | `worker`, `explorer` | none | none |
| Nested dispatch | assume no | **no** (documented) | n/a | assume no |
| Model per role | per-agent `model:` | per-droid `model:`, `reasoningEffort:`, or a `complexity` tier | per-run flag | per-chat selection |
| Conventions | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md`, `.cursor/rules` |
| Headless | yes | `droid exec` | `codex exec` | background agent |

`.factory/droids/` in this repository holds a droid definition for each seat in
`references/squad.md`. They are Factory's format; on another harness read them as
the seat briefings they are and paste the body into whatever that harness calls
an agent definition. The system prompt is the portable part.

## Where there is no subagent primitive

Relay still works, and invariant 3 is why: **the baton carries state, not the
conversation.** If a leg has to be a fresh top-level session, nothing about the
architecture changes — `.relay/` is the whole handoff, and it was always meant to
be readable by an agent that has never seen this conversation.

What changes is who starts the session. Either the human does it from the queue
the dashboard shows, or a script does:

```bash
# one leg, one fresh session, state read from and written back to disk
<harness-exec-command> "You are a Relay runner. Read .relay/legs.json for leg \
$LEG, .relay/contract.md for the checks it fulfils, references/execution.md for \
the handoff shape, then run it."
```

Serial-only is a slower relay, not a broken one. Say so at the approval gate so
the human's run-count estimate is honest.

## Model per role

No single provider is best at all three roles, and the third row is the one that
earns its keep.

| Role | What it needs |
|---|---|
| Coach | Slow, careful reasoning: strategy, constraints, long-horizon decomposition. |
| Runner | Code fluency and speed: fast generation, confident tool use. |
| Judge | Strict instruction-following, and **a different provider from the runner** — same-family models share the blind spot that produced the bug. |

Record the assignment in `.relay/dashboard.json`'s `runners[].model` so the
supervisor can see which model produced which leg. Where the harness cannot vary
the model, say so at the approval gate: the relay is then capped at its one
model's weakest capability, and judging is the place that costs you.
