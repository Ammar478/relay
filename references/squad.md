# The squad: seats, not a pool

A relay on a product codebase runs as a **standing team**. Each seat is a
specialist with one area of authority; the coach is the **orchestrator** and owns
sequencing, gates and the only conversation with the human.

A seat is a briefing, not a tool name. `.factory/droids/` in this repository holds
one definition per seat — name, model, tool policy, system prompt. On a harness
with its own agent format, read those files as the briefings they are and port the
body. On a harness with no agent definitions at all, the seat is the opening
paragraph of the leg briefing: *"You are the quality seat for this relay."*

| Seat | Owns | Definition |
|---|---|---|
| Orchestrator | Sequencing, gates, merge order, the human's attention | the coach (you) — no definition, you are already running |
| Scrum master | The board: legs exist before code, WIP limits, blockers surfaced | `relay-scrum-master` |
| Architect | Cross-cutting design, build-vs-buy, what is out of scope, `architecture.md` | `relay-architect` |
| Full-stack | Legs spanning both ends of a seam | `relay-fullstack` |
| Frontend | Everything the user sees, and the client contract | `relay-frontend` |
| UI/UX | Whether the change is usable, not merely rendered | `relay-ux` |
| Quality | Tests that bite, mutation evidence, coverage direction | `relay-quality` |
| DevOps | Pipelines, images, manifests, environments, `services.yaml` | `relay-devops` |
| Release manager | Rebase, MR hygiene, merge, issue closure, the release note | `relay-release` |
| Operations | What is actually running, and what it is doing now | `relay-operations` |
| Code judge | The code's own claims, at a gate | `relay-code-judge` |
| Behaviour judge | The product as a black box, at a gate | `relay-behaviour-judge` |
| Reviewer | The merge gate — never the author | `relay-reviewer` |

Where the harness cannot vary models or tool policies per seat, the seats still
earn their place: a briefing that says *"you own the client contract"* produces
different work from one that does not, on the same model.

## Seats do not talk to each other

Earlier versions of this file had seats messaging each other directly. No harness
Relay targets provides reliable agent-to-agent messaging, and the two that were
checked provide none at all, so the instruction produced either a hallucinated
tool call or silence.

**Every question between seats goes through the coach, and the answer goes onto
disk.** Quality needs to know which pipeline gates: the coach answers from
`relay.md`, or dispatches a read-only leg to find out and writes the answer into
`relay.md` where the next six runners will also find it. This is slower per
question and cheaper per relay, because an answer written down is answered once.

The exchange zone is **git and `.relay/`**, never a message channel. A fact that
lives only in a conversation dies with that context.

## Which seat gets a leg

Name the seat in the leg's `skillName` field in `legs.json`, and dispatch the
matching agent definition. Two rules:

- **The judge is never the seat that wrote the code**, and on a harness that can
  vary models, never the same model family either.
- **The reviewer at the merge gate is never the author.** See
  `references/governance.md`.

A leg whose seat is unclear is usually a leg that spans two and should be split,
or a genuine full-stack leg. Say which, in the leg's goal.
