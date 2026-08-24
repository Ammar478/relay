# Relay Control

The dashboard is how a human supervises a run without reading code. Your job as
coach is to keep it truthful; their job is to decide whether to intervene. It is
a view, never a gate — a failed render never blocks a relay.

## Regenerating it

```
python3 scripts/render_dashboard.py --relay-dir .relay
```

Run it, then send the file to the user, at exactly these moments:

- after the plan is approved (the relay's opening state)
- after each leg completes and its baton is disposed of
- at every stage gate, after the judges report
- whenever an attention signal appears — do not wait for the next leg
- at the finish

It reads `legs.json` and `state.json` directly. Everything those two cannot hold
goes in `.relay/dashboard.json`, which you write:

```json
{
  "path": "~/dev/password-manager",
  "status": "RUNNING",
  "elapsed": "6h 38m",
  "tokens": { "input": "324.0K", "cached": "16.8M", "output": "111.0K" },
  "activeLeg": { "id": "sharing-invite-flow", "stage": "sharing", "skill": "fullstack-runner",
                 "fulfills": [{"id":"ACC-SHARE-001","status":"passed"}],
                 "preconditions": ["..."], "expected": ["..."], "description": "..." },
  "runners": [{ "n": 9, "session": "a91f04c8-…", "start": "15:08", "dur": "47m",
                "status": "Running", "input": "—", "cached": "—", "output": "—",
                "leg": "sharing-invite-flow", "model": "Opus 4.6 (High)",
                "stream": [{"op":"Execute","arg":"pnpm test","res":"3 failed","bad":true}] }],
  "log": [{ "t": "22m ago", "m": "Stage judging: ACC-SHARE-002 still failing", "cls": "gatebad" }],
  "checkTitles": { "ACC-SHARE-002": "Accepting re-wraps the entry key" },
  "attention": [{ "level": "warn", "label": "SLOW", "text": "...", "action": "pause → mark complete" }]
}
```

Log line classes: `gate` (stage cleared), `gatebad` (stage failed), `note` (coach
decision or warning), omitted for ordinary events.

## The attention band

This is the part that earns the dashboard. Everything else reports; this decides.
Two signals are derived automatically:

- **STALLED** — a check failed three or more rounds. The contract or the leg spec
  is wrong, not the code. Stop and say so.
- **BLOCKED** — a check cannot be evidenced at all. Someone has to decide whether
  the harness gets extended or the check gets rewritten.

Add your own to `dashboard.json` when you see them:

| Level | When |
|---|---|
| `bad` | The relay cannot make honest progress without a human |
| `warn` | Something is off-pattern — a runner well past the median, a fix that broke a passing check, spend running ahead of progress |
| `calm` | Nothing needs them. Say so explicitly; silence reads as a stall |

Write signals the way you would tell a colleague: what happened, what it means,
and what the person could do. `"Runner #a91f04c has been on sharing-invite-flow
for 47m with no commit; median this stage is 18m"` is a signal. `"Runner slow"`
is not.

Never let the band be empty. A `calm` signal is information — it tells a
returning supervisor the quiet is real and not a frozen screen.

## What the views are for

| View | Question it answers |
|---|---|
| Status strip | Is it running, and how far in |
| Attention band | Do I need to do something right now |
| Active leg | What is being built, and which checks it is meant to satisfy |
| Legs | Where the plan stands, stage by stage — judge legs included |
| Contract | Is the quality real — what has actually been evidenced |
| Runners | Who ran, how long, what it cost, what they flagged |
| Progress log | What happened while I was away |
| Runner session | The full trace, when someone wants to look closely |

The Contract view is the honest counterweight to the progress bar. A relay can
show ten of thirteen legs complete while half its checks are unevidenced —
legs-done measures motion, checks-passed measures progress. When those two
diverge, trust the second.

## Honesty rules

- Never mark a check `passed` without the evidence it names. No evidence means
  `blocked`.
- Never hide fix legs. The `[+N]` count in the status strip is a quality signal,
  not an embarrassment — around a third is normal.
- Show judge legs in the queue alongside implementation legs. Judging is real
  work with a real cost, and hiding it makes the plan look cheaper than it is.
- Show spend alongside progress. A supervisor deciding whether to let a run
  continue needs both numbers in one glance.
- If the relay is stuck, say so in the attention band and stop. A dashboard that
  looks busy while nothing converges is worse than no dashboard.
