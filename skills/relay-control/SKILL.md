---
name: relay-control
description: Print a snapshot of a running relay into the conversation — phase, leg counts, check counts, the active leg and the attention items — together with the exact shell line that opens the live terminal dashboard. Use when the user asks how a relay is going, what the runner is on, for relay status, to see the dashboard, or types /relay-control.
---

# Relay Control

Render a snapshot of the relay into this conversation, then hand the user the
line that opens the live view.

**You cannot open the live view from here, and you must not say that you have.**
A slash command is a prompt for you, not a shell — it cannot hand the terminal
over to a full-screen curses program. The live dashboard is a terminal
application the user runs themselves, in their own terminal. The research
behind this is `.relay/research/slash-command-mechanism.md` in the relay skill;
it is why this skill reports instead of launching.

## Run this, exactly as written

```bash
RELAY_CONTROL="${RELAY_CONTROL:-$HOME/.claude/skills/relay/relay-control}"
"$RELAY_CONTROL" --snapshot
```

It reports the relay found from the working directory. To report a different
one, name it — the relay directory itself or the project above it:

```bash
"$RELAY_CONTROL" --snapshot ~/work/some-project
```

## Then

1. **Put the output in your reply verbatim**, in a fenced block. Do not
   summarise the figures, do not round them, do not re-order or shorten the
   attention items. Every number in it was read from the relay's own files;
   a paraphrase is a second answer that nothing checked.
2. **Keep the last section.** It carries the exact line the user runs to open
   the live view, with the relay's path already in it.
3. **Say nothing about having launched anything.** You have printed a still
   picture. If the user wants the live view, they run that line.
4. After the block you may add at most two sentences of your own — what you
   would look at first, or what an attention item implies. Nothing that
   restates a figure.

If the command exits non-zero it prints one line saying why — almost always
that the working directory has no relay under it. Show that line, and ask which
relay the user means rather than guessing.

## Installing this skill

A skill directory exposes exactly one command and is not searched recursively,
so a skill nested inside the relay skill is never loaded. Link it to the top
level once:

```bash
ln -s ~/.claude/skills/relay/skills/relay-control ~/.claude/skills/relay-control
```

`/relay-control` is then available in every session. The live dashboard needs no
such step — `~/.claude/skills/relay/relay-control` is runnable straight out of
the clone.
