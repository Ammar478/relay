---
name: relay-control
description: Open the live relay dashboard in a new terminal window and print a snapshot into the conversation — phase, leg counts, check counts, the active leg and the attention items — together with the exact shell line that opens the live terminal dashboard. Use when the user asks how a relay is going, what the runner is on, for relay status, to see the dashboard, or types /relay-control.
---

# Relay Control

Render a snapshot of the relay into this conversation, then hand the user the
line that opens the live view.

**You cannot hand *this* terminal to the dashboard** — a slash command is a
prompt for you, and Claude Code gives child processes no TTY, so a full-screen
curses program cannot take over the session you are in. That is documented and
settled (`.relay/research/slash-command-mechanism.md`).

**But on macOS you can open the dashboard in a new terminal window, and you
should.** `osascript` asks Terminal.app to start it, which gives it a real TTY of
its own. Print the snapshot into the conversation first, then open that window,
so the user has both: a still picture here, and the live view in a window.

If the launch fails — not macOS, Terminal.app unavailable, `osascript` missing —
say so plainly and give the user the line to run themselves. Never claim to have
opened a window you did not open.

## Run this, exactly as written

**Step 1 — print the snapshot into the conversation.**

```bash
RELAY_CONTROL="${RELAY_CONTROL:-$(ls ~/.factory/skills/relay/relay-control \
  ~/.claude/skills/relay/relay-control 2>/dev/null | head -1)}"
"$RELAY_CONTROL" --snapshot
```

It reports the relay found from the working directory. To report a different
one, name it — the relay directory itself or the project above it:

```bash
"$RELAY_CONTROL" --snapshot ~/work/some-project
```

**Step 2 — open the live dashboard in its own window.**

```bash
RELAY_CONTROL="${RELAY_CONTROL:-$(ls ~/.factory/skills/relay/relay-control \
  ~/.claude/skills/relay/relay-control 2>/dev/null | head -1)}"
osascript -e "tell application \"Terminal\" to do script \"cd '$PWD' && '$RELAY_CONTROL'\"" \
  -e 'tell application "Terminal" to activate'
```

A `tab N of window id …` reply means it opened. Anything else means it did not —
report that honestly and stop there.

Tell the user the window is open and that `q` closes it. The snapshot above it is
a still picture — say so, so the two are not confused.

## Then

1. **Put the output in your reply verbatim**, in a fenced block. Do not
   summarise the figures, do not round them, do not re-order or shorten the
   attention items. Every number in it was read from the relay's own files;
   a paraphrase is a second answer that nothing checked.
2. **Keep the last section.** It carries the exact line the user runs to open
   the live view, with the relay's path already in it.
3. **Claim only the window step 2 proved.** Say the live view is open only if
   step 2 replied `tab N of window id …`; otherwise say it did not open and
   point to that line. The snapshot itself is a still picture and launches
   nothing — never describe it as live.
4. After the block you may add at most two sentences of your own — what you
   would look at first, or what an attention item implies. Nothing that
   restates a figure.

If the command exits non-zero it prints one line saying why — almost always
that the working directory has no relay under it. Show that line, and ask which
relay the user means rather than guessing.

## Installing this skill

How this nested skill is discovered differs by harness, and at least one of them
has changed, so check rather than assume: Claude Code used not to search a skill
directory recursively, while Factory does. Run `/skills` (or the harness's own
manager) with the relay installed and see whether `relay-control` is listed.

If it is not, link it to the top level once:

```bash
ln -s ~/.factory/skills/relay/skills/relay-control ~/.factory/skills/relay-control
# or, for a Claude Code install:
ln -s ~/.claude/skills/relay/skills/relay-control ~/.claude/skills/relay-control
```

`/relay-control` is then available in every session. The live dashboard needs no
such step — `<the clone>/relay-control` is runnable straight out of it.
