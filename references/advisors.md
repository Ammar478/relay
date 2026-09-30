# Advisory decisions

The coach makes the same kinds of call many times a run: is this check
behavioural, does this leg fit one runner, what happens to this handoff item, is
this failure the code or the contract. A second opinion on each is cheap and
sometimes changes the answer.

**An advisor is advice, never evidence.** Everything in this file is subordinate to
that sentence.

## The switch

Advisors are on only when `relay.md` records an `Advisors:` line, agreed at the
Phase 3 approval gate (the `ADVISORS` line in the gate summary). They send
contract, leg, handoff and diff text to a third-party model, so they stay **off**
for a codebase whose data may not leave the organisation.

If a call fails — no key, no network, the tool is not installed — note it once in
`relay.md` and carry on without it. **A missing advisor never blocks a leg.**

Any key belongs to the human and is stored by them, through the advisor's own
login. Never ask for one in the conversation, and never write one into `.relay/`.

## The reference implementation: `jev`

`jev` is a small typed-verdict CLI. It is the advisor Relay was built against, and
the pattern generalises to any tool that takes text on stdin and returns a short
structured verdict.

Resolve it once, from wherever the harness installed it, and never hard-code one
harness's path:

```bash
J="$(command -v jev || ls ~/.factory/skills/jev/scripts/jev.py ~/.claude/skills/jev/scripts/jev.py 2>/dev/null | head -1)"
[ -n "$J" ] || echo "advisors: jev not found; continuing without" >&2
# call as python3 "$J" — zsh does not word-split an unquoted variable
```

## Why this advisor and no other kind

Jev is not a chat model. It is a typed Decisions API (OpenRouter
`~typesafe/jev-latest` → `POST /api/alpha/decisions`): it accepts a `state` and
typed questions, and answers **`noul`** (a yes/no probability), **`choice`**
(one of options you define, with a distribution) or **`score`** (a position on
an ordered rubric). It generates no text.

That shape earns it two things no prose advisor gets. **"Advice, never
evidence" is structural, not a rule** — a model that cannot emit a sentence
cannot write evidence, mark a check or talk a judge out of a verdict; the worst
it can do is answer a question you should not have asked. And **its answers
carry calibrated probabilities**, so "low confidence goes up, not through"
stops being a judgement call: below the threshold, the CLI itself says
`needs_human`, and the answer climbs to you. The `jev` CLI wraps this endpoint
(`JEV_ENDPOINT`), checks the key against your keychain, and turns one raw
answer into one advisory decision — which is why every row below is one narrow
question about one small state.

## The decision points, as typed questions

| When | Decision | Type | Options / rubric | What the coach does with the answer |
|---|---|---|---|---|
| Kickoff | Relay or single session? | `choice` | `relay` / `single_session` | Weigh it in **Scaling** |
| Phase 1, per check | Is this check behavioural, clear, evidenced? | `noul` + `choice` | no → `implementational` / `unjudgeable` / `no_threshold` / `vague` | Rewrite every check below the bar |
| Phase 2, per leg | Does this leg fit one runner? | `noul` | — | Split the leg |
| Phase 2, per stage | Is this a horizontal layer? | `noul` | — | Re-slice the stage |
| Phase 4, per handoff item | What happens to this item? | `choice` | `follow_up_leg` / `dismiss` / `debt` / `human` / `attention` / `repeat_friction` | `follow_up_leg`: write the leg. `dismiss`: your written reason. `debt` or `human`: to the human, who alone records debt. `attention` → the band. `repeat_friction` → `.relay/skills/` |
| Phase 4, per issue | Is this blocking? | `noul` | — | A **lead** for disposal order, never the severity of record |
| Phase 6, per failed check | Code, test, environment, or ambiguous contract? | `choice` | `code` / `test` / `environment` / `contract` | `contract` → ask the human, never a fix leg |
| Attention band | Is this quiet enough to leave alone? | `noul` | — | Below the bar, write `calm` explicitly |
| Before the merge gate | Best practice and reuse, per file | `score` | fine → worth a look → needs review | Hand the table to the reviewer as **leads, not findings** |

The calls, through the resolved CLI:

```bash
relay-scale   --input <objective>
relay-contract --input .relay/contract.md --split-on '^### ACC-' --summary
relay-leg     --input <leg or stage json>
relay-baton   --input <one handoff item's text>
relay-failure --input "$(jq -r '.checks["<ID>"].reason' .relay/state.json)"
code-practice --input <one file's diff> --threshold 0.6 --summary
```

Ask **per item, not per document.** Asked about a whole handoff, the advisor
cleared its confidence bar on none of two; asked per item, on three of thirteen.
One answer cannot cover several unrelated issues — and with a 32k-token context
and a zero output price, the small calls are also the cheap ones.

## Rules that do not bend

- **Advice, never evidence.** An advisor never marks a check, never replaces a
  judge, the reviewer or a test run, and is never cited as proof in `state.json`.
- **Low confidence goes up, not through.** An answer marked as needing a human is
  decided by the coach reading the material itself, or goes to the attention band.
  Never taken as-is.
- **Log the call.** Each disposition, split or rewrite an advisor informed gets one
  line in `relay.md`'s decisions log: the set, its answer, and what the coach did —
  including when it overrode the advisor.
- **Deterministic stays deterministic.** Coverage, `touches` disjointness, the
  concurrency ceiling, `rev-list` freshness and the three-legs-per-check count are
  **computed, not asked**:

```bash
python3 scripts/relay_decide.py dispatch|coverage|budget --relay-dir .relay   # exits 1 on a blocker
```

An advisor asked a question that a script answers is a slower, less reliable
script.
