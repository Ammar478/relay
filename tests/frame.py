"""Frame capture harness — run a terminal program under a pty and read the screen.

This is the evidence mechanism for every visual check in this project. A *frame*
is the visible terminal screen, as plain text, after some action. Frames are
deterministic, assertable, and can be written to `.relay/evidence/`.

Nothing here is specific to the relay TUI: it drives any program that draws on a
terminal. Python 3 standard library only.

Quick start
-----------

    import sys
    from frame import TerminalSession

    with TerminalSession([sys.executable, "scripts/relay_control.py"],
                         rows=24, cols=80) as term:
        frame = term.wait_for("Overview")      # wait for the first paint
        frame.assert_contains("RUNNING")
        frame.assert_within_width()            # nothing wider than 80 cells

        frame = term.send("<Down><Down><Enter>")   # waits for the program to act
        assert frame.find("cutover-flip") is not None

        frame = term.send("<Enter>", expect="Leg detail")   # the strongest
        frame.assert_finished()                # ...and this demands the proof

        frame = term.resize(48, 160)           # SIGWINCH, then recapture
        frame.dump(".relay/evidence/legs-160x48.txt")

        term.send("q")
        assert term.wait() == 0                # clean exit

One-shot form, when a judge just wants the frames:

    frames = run_frames([sys.executable, "scripts/relay_control.py"],
                        keys=["F", "<Down>", "<Enter>", "<Esc>", "q"],
                        rows=24, cols=80, wait_for="Overview")
    # frames[0] is the first paint, frames[n] is the screen after keys[n-1]

Key notation
------------

`send()` and `run_frames(keys=...)` take strings. Ordinary characters are sent
literally; `<Name>` is a named key:

    <Up> <Down> <Left> <Right> <Home> <End> <PageUp> <PageDown>
    <Enter> <Tab> <BackTab> <Esc> <Space> <Backspace> <Delete> <Insert>
    <F1>..<F12>
    <C-c>, <C-a>, ... (control keys; <^C> works too)
    <lt>  a literal "<"

A step in `run_frames(keys=...)` may also be `(keys, expect)`, which is
`send(keys, expect=...)`.

Names are case-insensitive. Arrow/Home/End encoding follows the *program's own*
DECCKM state, exactly as a real terminal does: the harness watches for
`ESC [ ? 1 h` in the output and switches to `ESC O A` form, so ncurses apps that
call `keypad(True)` receive real KEY_UP/KEY_DOWN.

Synchronising with the program
------------------------------

A frame is evidence only if it is the screen the program drew *after* the
action. `send()` therefore does not simply write the keys and read whatever is
there; it waits, on two positive signals:

* **Delivery.** The keys are waited out of the pty's input queue, which is
  something a terminal can observe without the program's cooperation: bytes sit
  there until the program reads them (`FIONREAD` on the slave fd). So "the
  program has seen this keystroke" is an observation, not an assumption, and a
  frame can no longer predate the keystroke it is meant to show. A program that
  is alive and still has not read within `timeout` fails the call — a wedged
  TUI is a finding, not a frame.

  Delivery says nothing about the answer, and the barrier claims nothing about
  it. What it does claim is the other direction, which *is* provable: output
  readable while the keys are still queued was written before the program took
  them, so it can never be a response to them. The barrier consumes exactly
  that output — which also keeps a program that is slow to read from blocking
  on a full output buffer — and leaves the pty alone the moment the queue is
  empty, so the repaint is still there for the response wait.
* **The answer.** Then the program's answer is waited for: the first byte it
  writes after delivery, and the repaint settling after that, bounded by
  `redraw` (0.75s) so a keystroke that legitimately draws nothing stays cheap.
  `send(keys, expect="...")` names text the repaint must show and is the
  strongest form available — it fails loudly with the frame instead of
  returning a stale one, and the needle triggers the capture rather than being
  it: the wait runs on past the needle until the repaint ends. Without that, a
  program painting a screen region by region hands back a frame that passes on
  the needle and shows the previous screen everywhere else, and text that
  survives a repaint (a pane heading) ends the wait before the program has
  drawn anything at all.

When is a repaint over?
-----------------------

Two answers are proof and five are not, so every frame `expect=` returns
carries `frame.paint_end` saying which it was:

* `"synchronised"` — the program bracketed the repaint in **DEC private mode
  2026**: `ESC [ ? 2026 h` before it, `ESC [ ? 2026 l` after. That closing
  sequence *is* the program saying "the screen is whole", which is the only
  statement about its own painting a terminal can read. The wait ends there.
  Five things have to hold before the harness reads it as that statement, and
  every one of them was once missing; see "What a closed bracket does not
  say".
* `"exited"` — the program is gone with no repaint open, **and it never
  painted a glyph outside a bracket it closed**. So the screen is bracketed
  repaints and nothing else, each one vouched for, and no further repaint can
  start. Both halves are needed; see "What an exit does not say".
* `"abandoned"` — the program is gone with no repaint open, but it had painted
  outside one, so no statement of its own covers the screen. Not proof.
* `"torn"` — the program is gone, but it died **inside** a bracket it had
  opened. Nothing more can arrive and the program never said the screen was
  whole, so this is the one screen a terminal knows is half a repaint. Kept as
  evidence of exactly that; not proof.
* `"unsound"` — the program's brackets did not balance. Not proof, and worse
  than silence; see below.
* `"quiet"` — it stopped writing for `paint` (0.2s). **This is a heuristic and
  the API does not pretend otherwise.** A program that pauses longer than that
  inside one repaint — clear the screen, draw the title, pause, draw the body —
  is quiet and half-painted at the same instant, and the frame then shows
  neither the old body nor the new one. Every negative assertion passes on such
  a frame for the wrong reason. There is no observation a terminal can make
  that separates a pause from an ending: `FIONREAD` answers for input, not
  output, and a byte the program has not written yet is not visible anywhere.

What to do about the last one, in order of preference: have the program
bracket its repaints in 2026; call `frame.assert_finished()`, which refuses
anything but the two proofs; raise the window for a call with `quiet=` (or for
a session with `paint=`) past the longest pause the program takes; and assert
on what the screen *should say*, never only on what it should not.

What a closed bracket does not say
----------------------------------

`ESC [ ? 2026 l` says "the repaint I opened is complete". Read as anything
wider than that, it hands back frames the program never vouched for, and three
shapes did:

* **Painting the close did not cover.** The sequence vouches for the bracket,
  not for the screen; they are the same thing only for a program that draws
  nothing outside a bracket. Paint the heading, then flush an empty
  `ESC[?2026h ESC[?2026l` beside it, and a wait ended on that close with the
  answer still undrawn — on the strongest label the harness has. So a close is
  read as a statement about the screen only while nothing has been painted
  outside a bracket since the wait began (`Screen.unbracketed_paints`);
  otherwise the wait falls back to silence, which is what it actually has.

  An *empty* bracket on its own is not the defect and is not treated as one: a
  repaint that found nothing to change still brackets, and it is telling the
  truth. Nothing separates it from a program about to paint unbracketed — so
  the discipline is what gets checked, not the emptiness.
* **An empty bracket on a screen no bracket ever covered.** Which leaves what
  the empty bracket is worth, and the answer is: exactly the proof that was
  already there. "A repaint that drew nothing is over" carries a whole screen
  forward for a program whose screen a closed bracket does cover — a TUI whose
  curses layer found no cell to change is that program, and roughly forty
  checks here are judged off frames taken that way. It carries nothing at all
  for a program that painted its whole screen outside every bracket and then
  flushed `ESC[?2026h ESC[?2026l` at a keystroke, and that program was handed
  `synchronised` for a screen whose own exit reports `abandoned`. So a close
  is read as a statement about the screen only while the screen is still
  covered by one (`Screen.screen_vouched`): a close whose bracket enclosed
  painting sets that, anything touching the screen with no bracket open clears
  it, and an empty bracket does neither.
* **A bracket that was already open when the wait began.** Its close is a
  statement about a repaint that started before the keystroke existed. A
  program that opens a repaint, is drained, and then closes it the instant it
  reads the keys ended the wait on the PRE-KEYSTROKE screen, stamped
  `synchronised` — a negative assertion passing against a screen the keystroke
  never touched. A bracket opened since the wait began speaks for that wait
  whatever it drew; one that straddles the start of it speaks for that wait
  only where it enclosed painting done since (`Screen.synchronized_opens`,
  `Screen.bracketed_paints`). A TUI that opens its repaint before reading the
  next key — which is the ordinary shape — is unaffected, because its bracket
  encloses the painting that answers the key.
* **Bracket bytes arriving as content.** A pane rendering a log line, a
  fixture or a baton that *quotes* the sequence puts those bytes on the wire,
  and they are byte for byte the program's own sequence — nothing a terminal
  can observe separates them, and no clever reading will. What *is* observable
  is the wreckage: a close with nothing open, or an open inside an open, which
  a program bracketing its repaints never sends. Those are counted
  (`Screen.synchronized_faults`), and a wait that sees one reports `unsound`
  rather than a frame it vouches for — for the screen too, since a stream
  carrying escape bytes as content has had that content taken for control all
  along. The residual is honest and stated: when the forged close arrives
  ahead of the program's own, the frame goes back stamped `synchronised`
  before anything can show otherwise. A frame already returned cannot be
  un-returned — so the *session* refuses to end once the brackets are known
  not to balance, and nothing captured from that program is quietly kept.
* **Dying inside a bracket.** "The program is gone, so nothing can be added"
  is true and is the wrong question: it had said "one whole frame begins" and
  never said it ended. That is `torn`, and `assert_finished()` refuses it.

What an exit does not say
-------------------------

The same sentence, one level up. An exit proves that nothing more is coming,
which is real and is why exiting was read as the end of a repaint. It is not
"what is on screen is whole": a child killed mid-paint has also stopped
writing, and a program that dies on an uncaught exception halfway through a
repaint exits with a status it chose. Nothing a terminal can observe separates
those from a program that finished.

So an exit is proof only for a program whose painting was all inside brackets
it closed — then the screen is repaints it vouched for, and the exit adds the
one thing a close cannot say, that no further repaint will start. Anything
else is `abandoned`. The reading this replaces was exactly backwards: `torn`
is only reachable for a program that DOES bracket, so the programs able to
prove nothing at all — the ones that never bracket — were the ones being
handed proof. A child that never emitted a 2026 bracket in its life cannot
have proved that any screen of its was whole.

Unlike a close, which vouches for one repaint and so is measured from the
wait's baseline, this is asked of the whole session: the screen an exit leaves
is everything the program ever drew, and a glyph painted outside a bracket ten
repaints ago is still on it and still unvouched for. That asymmetry is
deliberate and it is not a hole; what WAS a hole is what each side does with
the screen in front of it, and both sides now ask that too — see
`Screen.screen_vouched`.

What the harness itself may not launder
---------------------------------------

Two of the four routes into a false proof were not the program's doing at all.

`ESC c` (RIS) resets the screen. It does not reset the record of what the
program has done — brackets it closed, glyphs it painted outside one,
sequences a repaint-bracketing program cannot send. Clearing those with the
grid let a program erase the evidence of its own misbehaviour by asking for a
blank screen: `unsound` became `synchronised`, the session-wide backstop had
nothing left to refuse, and a program that had never bracketed a glyph in its
life exited to `exited`. RIS is also *painting* — it blanks every cell — so a
program that wipes the screen outside a bracket has drawn outside one.

`TerminalSession.resize()` narrows the grid, and `Screen.resize()` keeps the
top-left corner and deletes the rest. Those are cells a program drew, deleted
by the harness; the screen that is left is one no program ever produced. A
resize that drops a drawn cell therefore clears `screen_vouched`, which a
repaint at the new size — what a program does on SIGWINCH — sets again. Where
the program has already exited it never can, and the exit branch, which asks
only what the PROGRAM did, called the wreckage `exited`.

A program still writing when `redraw` runs out is the one case a terminal can
be certain about — that screen is definitely partial — so `expect=` raises with
the frame instead of returning it.

Two residual failure modes in the synchronisation itself, stated plainly:

* A keystroke whose repaint *starts* later than `redraw`, with no `expect`
  given, still yields the pre-repaint frame — a terminal cannot tell "still
  thinking" from "decided to draw nothing". That is a bounded wait, not a
  guess: name the text with `expect=` (or raise `redraw=`) whenever a
  transition may be slower than that.
* A program that writes on its own timetable — a clock, a progress tick, a
  repaint it began in the instant before it read the keys — can have that write
  taken for the answer. Only writes the harness observes while the keys are
  still queued are provably not the answer; one that lands microseconds after
  the read is indistinguishable from a response to it, and no observation a
  terminal can make closes that gap. `expect=` is the answer here too: it waits
  for text, not for bytes.

`resize()` has no delivery barrier to use, because a signal leaves nothing in
the input queue: `expect=` is the only sound signal there.

What the screen emulator does
-----------------------------

Escape sequences are *interpreted*, not stripped. The screen is a grid of cells
that the sequences mutate, so the captured text is what a human would see:
cursor addressing (CUP/HVP, CUU/CUD/CUF/CUB, CHA/VPA, CNL/CPL), erases (ED, EL,
ECH), insert/delete of lines and characters (IL/DL, ICH/DCH, IRM), scroll
regions (DECSTBM) and scrolling (IND, RI, NEL, SU/SD), repeat (REP), tab stops,
the alternate screen (`?1049`), autowrap (`?7`), synchronized output (`?2026`),
save/restore cursor, the alternate character set and UTF-8 decoding across read
boundaries. Escape bytes never reach the text: frames carry what a human would
see, never the sequences that put it there. A sequence the emulator does not
model leaves *nothing* behind — an escape with an intermediate byte
(`ESC # 8`, `ESC SP F`) is swallowed whole rather than dropping its final
character into the text plane as content. An `ESC` inside an OSC or DCS string
is that string's terminator only when `\\` follows it; anything else aborts the
string and *begins a sequence*, so `ESC ] 0 ; t ESC [ 1 ; 1 H` addresses the
cursor instead of drawing `1;1H`.

Nothing is dropped either. A zero-width combining mark joins the cell it
follows rather than vanishing, so `cafe` + U+0301 is on the screen as the
accented word a terminal shows; the grid holds one string per cell and
`display_width` already counts the mark as zero, so no column moves. And an
SGR parameter that is present but is not a number is an unknown parameter,
recorded in `CellAttrs.other` — not parameter 0, which is a full reset.

That includes the box drawing. `curses.border()` on `xterm-256color` does not
send `┌` and `│`; it sends `ESC ( 0`, then the letters `l q k x m j`, then
`ESC ( B`. The emulator keeps G0-G3 and the DEC Special Graphics table (SO/SI
included), so a border arrives as a border. An emulator that dropped the
designation would report `lqqqk` — and then `assert_contains("│")` could never
pass, `assert_not_contains` would trip over the injected runs, and every frame
written to `.relay/evidence/` would be a wrong artefact.

No sequence can wedge the emulator either. A count is clamped to the screen
wherever one is looped or allocated over — a scroll of 200 million and a scroll
of 24 leave the same screen — because `feed()` runs inside a drain that checks
its deadline only *between* reads, so one twelve-byte sequence would otherwise
hang the harness with no exception and no timeout. A hung judge is
indistinguishable from a slow one.

Known limits, on purpose: no scrollback (only the visible screen, so `ED 3`
has nothing to erase and does nothing), no origin mode (`?6`), and no reply to
cursor-position queries.

What a program left on the alternate screen is kept when it gives the screen
back, and `TerminalSession.last_alt_frame()` returns it. A curses program's
exit path runs `endwin()`, which sends `?1049l` and restores the primary
screen, so after a crash or a SIGTERM `frame()` shows the shell line and not
the screen the program died holding — which is precisely the evidence a judge
wants.

The attribute plane
-------------------

Alongside the text, every cell records *how it was drawn* — the SGR parameters,
exactly as the program sent them. Nothing is resolved to RGB and nothing is
normalised, because a judge asserts "this was drawn with SGR 32 and bold", not
"this was #00ff00":

    frame.attrs_at(row, col)        # the CellAttrs of one cell
    frame.attr_runs(row)            # contiguous runs of like-attributed cells
    frame.run_with("FAILED")        # the run containing a substring
    frame.attrs_for("FAILED")       # that run's CellAttrs
    frame.assert_attrs("FAILED", fg=31, has="bold")
    frame.assert_attrs_differ("FAILED", "PASSED")

None of those can pass vacuously. `assert_attrs` refuses a call with nothing to
assert and a flag name that is not a flag (`lacks="bolt"` used to pass on any
styling at all), and `run_with` refuses a needle that was drawn two different
ways in two places rather than answering for the first one it finds — name a
`row` to say which copy is meant. Frames compare on text *and* attributes, so
`after != before` still sees a status that went from green to red.

`CellAttrs.fg` / `.bg` are parameter tuples — `(32,)`, `(38, 5, 214)`,
`(38, 2, r, g, b)` — or `None` for the terminal default. `.flags` is a frozenset
of names (`bold`, `dim`, `italic`, `underline`, `blink`, `reverse`, `invisible`,
`strike`), also reachable as `.bold`, `.reverse` and friends. `.other` keeps any
SGR parameter the harness does not model, so an unknown parameter is *recorded*
rather than silently mistaken for something else — a child that emits no SGR at
all simply has a plane of `DEFAULT_ATTRS`.

Erasing follows back-colour erase, which is what `xterm-256color` advertises
(`bce`): a cell blanked by ED/EL/ECH, or scrolled/inserted into existence, keeps
the *current background* and loses everything else. `Screen.reset()` and
`Screen.resize()` produce default cells.

Gotchas worth knowing before you debug something
------------------------------------------------

* `COLUMNS` and `LINES` are always removed from the child environment. ncurses
  prefers them over the pty size, and they would silently defeat `rows=`/`cols=`.
* `ESCDELAY=25` is set by default so `<Esc>` is not swallowed for a second by
  ncurses' escape-sequence timeout. Override via `env=` if a test needs the
  default.
* Terminal attributes start at the pty defaults (echo on). `initial_attrs`
  records them at launch; compare with `termios_attrs()` after exit to prove the
  program restored the terminal. Both read the master side, because macOS
  revokes the slave fd the moment the child session leader exits.
* The child's stderr goes to the pty, so a traceback appears in the frame. That
  is deliberate — a crashed TUI is visible evidence rather than a silent blank.
* A curses program writes its endwin cleanup on the way out and blocks there if
  nobody is reading the pty, so `wait()` and `close()` keep draining while they
  wait. Do not replace them with a bare `os.waitpid`.
* A resize while the program holds the alternate screen keeps the saved primary
  screen, clipped and padded to the new size. ncurses takes the alternate screen
  (`?1049`) on `xterm-256color`, so a SIGWINCH mid-run and a clean quit meet
  here: dropping it would mean the program could never hand back the screen it
  was given.
* `lines` are right-stripped; use `raw_lines` when a column position matters.
* Width is *display* width: wide (East Asian W/F) characters count as two cells,
  combining marks as zero.
* A row that wrapped stops being a wrapped row when the screen is made wider
  than the width it wrapped at: the grid is not reflowed, so what is on screen
  afterwards is two short rows, not one long one.

Catching content that is too wide
---------------------------------

A real terminal wraps an over-long line onto the next row, so no row is ever
wider than the terminal — a naive "is any line > cols" check can never fail. The
emulator therefore records, as it draws, what ran past the right margin on each
row: the width at which the row *continued* onto the next one, and the cells
destroyed at the margin when autowrap was off. `Frame.overlong_lines()` returns
`(row, width)` per logical line from those records — never recomputed from the
current size, so a resize can neither drop a violation nor inflate a width into
a number that was never on the screen.

`Frame.assert_within_width()` fails with the offending rows and the whole frame
in the message. A line that exactly fills the width is not a violation, but it
is not a pass either: ncurses clips at the window edge and cursor-addresses
every row rather than letting the terminal wrap, so on a curses screen there is
nothing to read and the helper would be incapable of failing. It therefore
refuses to certify a frame with a row at the last column — nothing a terminal
can observe tells an exact fit from content truncated to fit — and the caller
says which it is with `assert_within_width(allow_full_width=True)`. Also note
that `contains` / `assert_not_contains` see a needle the terminal broke across
two rows; a line-by-line search does not.
"""

from __future__ import annotations

import codecs
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios
import time
import unicodedata
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "DEFAULT_ATTRS",
    "PAINT_ABANDONED",
    "PAINT_ENDS",
    "PAINT_EXITED",
    "PAINT_PROVED",
    "PAINT_QUIET",
    "PAINT_SYNCHRONISED",
    "PAINT_TORN",
    "PAINT_UNFINISHED",
    "PAINT_UNSOUND",
    "AttrRun",
    "CellAttrs",
    "Frame",
    "RowOverflow",
    "Screen",
    "TerminalSession",
    "display_width",
    "encode_keys",
    "run_frames",
]

DEFAULT_ROWS = 24
DEFAULT_COLS = 80
DEFAULT_TERM = "xterm-256color"

# How a wait for the end of a repaint ended — recorded on the frame, because
# only two of these are proof that the program had finished the screen.
#
#   synchronised  the program closed a DEC 2026 bracket that it had opened
#                 cleanly and that enclosed painting. It said, in a sequence a
#                 terminal reads, "that is the whole screen". Proof.
#   exited        the program is gone with no repaint open, AND every glyph it
#                 ever put on the screen was inside a bracket it closed. Both
#                 halves are load-bearing: "nothing more is coming" is equally
#                 true of a child killed mid-paint, so on its own it says
#                 nothing about the screen. What makes this proof is that the
#                 program vouched for everything on the screen and has now
#                 stopped adding to it. Proof.
#   abandoned     the program is gone with no repaint open, and no statement
#                 of its own covers the screen: it painted outside a bracket,
#                 or the harness's own resize deleted cells it had drawn and
#                 it will never repaint them. It stopped writing; whether it
#                 stopped because it was finished or because it was killed
#                 mid-repaint is exactly what a terminal cannot see. Not
#                 proof.
#   torn          the program is gone, but it died *inside* a bracket it had
#                 opened. Nothing further can arrive and the program never
#                 said the screen was whole — so this is the one screen a
#                 terminal can be certain is half a repaint, kept as evidence
#                 of exactly that. Not proof.
#   unsound       the program's 2026 brackets did not balance during the wait
#                 — a close with nothing open, or an open inside an open. A
#                 program whose *content* carries bracket bytes is
#                 indistinguishable from one issuing the sequence, so nothing
#                 it appeared to say about its painting can be read. Not
#                 proof, and worse than silence: the text plane is suspect too.
#   quiet         it stopped writing for `paint` seconds. *Not* proof: a
#                 program pausing mid-repaint for longer than that is quiet
#                 and half-painted at the same time, and no observation a
#                 terminal can make tells the two apart.
#   unfinished    it was still writing when the window ran out. That is the
#                 other case a terminal can be certain *is* a partial screen,
#                 so `expect=` refuses it rather than handing it over.
PAINT_SYNCHRONISED = "synchronised"
PAINT_EXITED = "exited"
PAINT_ABANDONED = "abandoned"
PAINT_TORN = "torn"
PAINT_UNSOUND = "unsound"
PAINT_QUIET = "quiet"
PAINT_UNFINISHED = "unfinished"
PAINT_ENDS = (PAINT_SYNCHRONISED, PAINT_EXITED, PAINT_ABANDONED, PAINT_TORN,
              PAINT_UNSOUND, PAINT_QUIET, PAINT_UNFINISHED)
PAINT_PROVED = frozenset({PAINT_SYNCHRONISED, PAINT_EXITED})
# The two endings that are positive evidence the screen cannot be read: the
# program died INSIDE a repaint it had opened, so this screen is certainly
# half of one; or its brackets did not balance, so the emulator has been
# taking that stream's content for control and the text plane is suspect. A
# NEGATIVE assertion — "the error pane is gone" — passes on either for the
# wrong reason, so it is refused rather than believed.
#
# `abandoned` is deliberately NOT here, and neither is `quiet`. Both mean "no
# statement covers this screen", which is not the same as knowing the screen
# is not whole: a program that prints a message and exits is `abandoned`, and
# asserting that its message came without a traceback is a fair thing to do.
# `assert_finished()` is how a caller demands more than that.
_KNOWN_PARTIAL = frozenset({PAINT_TORN, PAINT_UNSOUND})

# What `assert_finished()` tells a caller to do about it. The default is the
# advice for silence; the other two endings are not the caller's knob to turn
# and saying "raise the quiet window" at them would send a reader looking for
# a timing problem that is not there.
_NOT_PROVED_ADVICE = (
    ". A program proves it by bracketing the repaint in DEC 2026"
    " (ESC[?2026h ... ESC[?2026l) around everything it draws, and by exiting"
    " outside one; short of that, raise the quiet window with quiet=/paint="
    " and assert on what the screen should say rather than on what it"
    " should not."
)
_WHY_NOT_PROOF = {
    PAINT_ABANDONED: (
        ". The program is gone, which proves only that nothing more is"
        " coming — a child killed mid-repaint has also stopped writing. And"
        " nothing it ever said covers this screen: either it painted outside"
        " any DEC 2026 bracket, and a program that never brackets cannot have"
        " proved that any screen of its was whole, or the harness's own"
        " resize() deleted cells it had drawn and it has exited without"
        " repainting them. Bracket every repaint, and take the frame before"
        " the resize or after the redraw at the new size; no window setting"
        " turns an exit into a statement."
    ),
    PAINT_TORN: (
        ". The program exited INSIDE a bracket it had opened, so this screen"
        " is a repaint it never finished and never said was whole — kept as"
        " evidence of exactly that. The finding is the exit; raising a window"
        " cannot change it."
    ),
    PAINT_UNSOUND: (
        ". The program's 2026 brackets did not balance — a close with nothing"
        " open, or an open inside an open. Bracket bytes reaching the terminal"
        " as CONTENT look exactly like that, so this program's statements"
        " about its own painting cannot be read, and neither can its text"
        " plane. Escape what the program renders; no window setting helps."
    ),
}

# The DEC Special Graphics set: what `ESC ( 0` turns the ASCII range 0x5F-0x7E
# into until `ESC ( B` turns it back. ncurses draws every box, rule and arrow
# through this set — `curses.border()` on xterm-256color sends the letters
# `l q k x m j` between the two designations — so an emulator that drops the
# designation renders "lqqqk" where the terminal shows "┌───┐", and every
# assertion about a pane border in every frame it captures is wrong.
_DEC_SPECIAL_GRAPHICS = {
    "_": " ", "`": "◆", "a": "▒", "b": "␉", "c": "␌", "d": "␍", "e": "␊",
    "f": "°", "g": "±", "h": "␤", "i": "␋", "j": "┘", "k": "┐", "l": "┌",
    "m": "└", "n": "┼", "o": "⎺", "p": "⎻", "q": "─", "r": "⎼", "s": "⎽",
    "t": "├", "u": "┤", "v": "┴", "w": "┬", "x": "│", "y": "≤", "z": "≥",
    "{": "π", "|": "≠", "}": "£", "~": "·",
}

# Which slot `ESC (`, `ESC )`, `ESC *`, `ESC +` (94-character sets) and
# `ESC -`, `ESC .`, `ESC /` (96-character sets) designate into.
_CHARSET_SLOTS = {
    "(": "G0", ")": "G1", "*": "G2", "+": "G3",
    "-": "G1", ".": "G2", "/": "G3",
}

# A CSI parameter is clamped on the way in for one reason only: CPython
# refuses to build an int from more than 4300 digits, so a long enough digit
# run raises inside `feed()` instead of drawing anything. The cap is still far
# larger than any grid, so it decides nothing on its own — every routine that
# loops or allocates over a count clamps it to the screen as well.
_MAX_PARAM = 9_999_999
_MAX_PARAM_DIGITS = 7

# How much parameter text one CSI may accumulate. This is the only buffer in
# the parser whose length a program dictates, and it was unbounded. The cap is
# deliberately far above anything a program acting in good faith sends — every
# real sequence is a handful of characters, and the longest this module's own
# tests ask it to survive is two five-thousand-digit counts — so it decides
# nothing about how a sequence is read. It only stops a program from making
# `feed()` hold a buffer for as long as it cares to write.
_MAX_CSI = 65536

# How many combining marks one cell may carry, and how many unknown SGR
# parameters one pen may accumulate. Both are records the program dictates the
# size of, and both were unbounded: U+0301 written forever grew ONE cell
# without limit — every mark rewriting the whole string, so the cost of a read
# rose with everything read before it, and `REP` then copied that cell across
# the grid — and `ESC[0<0m`, `ESC[0<1m`, ... grew a frozenset that every cell
# drawn afterwards holds a reference to. Real text needs a handful of marks (a
# Hangul syllable with tone marks, a Devanagari cluster); real programs send
# no unknown SGR parameters at all. Past the cap the record stops growing,
# which loses a mark a terminal would have shown — and is the same trade the
# grid itself makes, since a screen holds what it holds.
_MAX_COMBINING = 8

# How long a drain that is only meant to take what the exit path wrote may
# run. What a program wrote on its way out is a finite thing; a grandchild
# still holding the pty and writing is not, and these drains are not waits
# with a budget of their own to spend.
_EXIT_DRAIN = 0.5
_MAX_UNKNOWN_SGR = 64


def _param(text):
    """One CSI parameter as a number, or None when it is not one.

    Leading zeros are stripped before the length is looked at, because they
    carry no value and a terminal reads `01` as 1. Counting them made a
    zero-padded parameter longer than seven characters — `ESC[00000001;1H` —
    arrive as `_MAX_PARAM` and address the last row instead of the first: a
    padded number resolved to the wrong cell, which is the one thing this
    function exists to get right. What is left after the strip is at most
    `_MAX_PARAM` when it is seven digits or fewer, so the length guard is
    still the whole cap.
    """
    if not text.isdigit():
        return None
    digits = text.lstrip("0")
    if not digits:
        return 0
    if len(digits) > _MAX_PARAM_DIGITS:
        return _MAX_PARAM
    return int(digits)


# ---------------------------------------------------------------------------
# text measurement
# ---------------------------------------------------------------------------


def _char_width(ch: str) -> int:
    if len(ch) > 1:
        # A whole cell, not a character: the grid holds one string per cell and
        # a cell carrying combining marks is several characters long. The marks
        # are zero-width, so the cell is as wide as what they are attached to.
        return _char_width(ch[0])
    if unicodedata.combining(ch):
        return 0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    return 1


def display_width(text: str) -> int:
    """Width of `text` in terminal cells."""
    return sum(_char_width(ch) for ch in text)


# ---------------------------------------------------------------------------
# attributes
# ---------------------------------------------------------------------------


class CellAttrs(NamedTuple):
    """How one cell was drawn: the SGR parameters that drew it, as given.

    `fg` and `bg` are parameter tuples — `(32,)` for SGR 32, `(38, 5, 214)` for
    256-colour 214, `(38, 2, r, g, b)` for direct colour — or `None` for the
    terminal's default. Nothing is resolved to a palette or an RGB value: a
    judge asserts which parameters drew a cell, not what shade they produced.

    `flags` names the boolean attributes that were on. `other` holds any SGR
    parameter this harness does not model, so an unrecognised one is recorded
    rather than mistaken for something it is not.
    """

    fg: tuple = None
    bg: tuple = None
    flags: frozenset = frozenset()
    other: frozenset = frozenset()

    # -- the flags, spelled out for readable assertions -------------------

    @property
    def bold(self) -> bool:
        return "bold" in self.flags

    @property
    def dim(self) -> bool:
        return "dim" in self.flags

    @property
    def italic(self) -> bool:
        return "italic" in self.flags

    @property
    def underline(self) -> bool:
        return "underline" in self.flags

    @property
    def blink(self) -> bool:
        return "blink" in self.flags

    @property
    def reverse(self) -> bool:
        return "reverse" in self.flags

    @property
    def invisible(self) -> bool:
        return "invisible" in self.flags

    @property
    def strike(self) -> bool:
        return "strike" in self.flags

    @property
    def is_default(self) -> bool:
        """True when the cell was drawn with no SGR in effect."""
        return self == DEFAULT_ATTRS

    def describe(self) -> str:
        """One short line, for an assertion message."""
        if self.is_default:
            return "default"
        parts = []
        if self.fg is not None:
            parts.append("fg=" + ";".join(str(n) for n in self.fg))
        if self.bg is not None:
            parts.append("bg=" + ";".join(str(n) for n in self.bg))
        if self.flags:
            parts.append("+".join(sorted(self.flags)))
        if self.other:
            parts.append(
                "other=" + ",".join(str(n) for n in sorted(self.other, key=str)))
        return " ".join(parts)

    def __repr__(self) -> str:
        return "<attrs %s>" % self.describe()


DEFAULT_ATTRS = CellAttrs()


class RowOverflow(NamedTuple):
    """What ran past the right margin on one row, and how.

    `continued` is the width at which the row wrapped onto the next one, kept
    as a width rather than a flag so that a later resize cannot rescale it
    into a number that was never on the screen. `lost` counts the cells
    destroyed at the right margin because autowrap was off: content the
    program drew and the screen never showed.
    """

    continued: int = 0
    lost: int = 0


_NO_OVERFLOW = RowOverflow()


class AttrRun(NamedTuple):
    """A stretch of one row drawn with identical attributes.

    `start` is inclusive and `end` exclusive, both in grid columns.
    """

    row: int
    start: int
    end: int
    text: str
    attrs: CellAttrs

    def __repr__(self) -> str:
        return "<run row %d cols %d-%d %r %s>" % (
            self.row,
            self.start,
            self.end - 1,
            self.text,
            self.attrs.describe(),
        )


# SGR parameters that turn a flag on, and the ones that turn flags off.
_SGR_ON = {
    1: "bold",
    2: "dim",
    3: "italic",
    4: "underline",
    5: "blink",
    6: "blink",
    7: "reverse",
    8: "invisible",
    9: "strike",
}
_SGR_OFF = {
    22: ("bold", "dim"),
    23: ("italic",),
    24: ("underline",),
    25: ("blink",),
    26: (),
    27: ("reverse",),
    28: ("invisible",),
    29: ("strike",),
}
_SGR_EXTENDED = (38, 48, 58)


def _extended_colour(code, tokens, index):
    """Consume the semicolon-separated arguments of SGR 38/48/58.

    Returns `(value, next_index)`; `value` is the whole parameter list as given,
    so `38;5;214` becomes `(38, 5, 214)` — the same tuple the colon form
    `38:5:214` produces.
    """

    def take():
        nonlocal index
        if index < len(tokens):
            value = tokens[index][0]
            index += 1
            return value
        return None

    kind = take()
    if kind == 5:
        which = take()
        return (code, 5, 0 if which is None else which), index
    if kind == 2:
        channels = [take() for _ in range(3)]
        return (code, 2) + tuple(0 if c is None else c for c in channels), index
    if kind is None:
        return None, index
    return (code, kind), index


def _apply_sgr(attrs: CellAttrs, raw: str) -> CellAttrs:
    """Apply one SGR sequence's parameter string to `attrs`.

    Unknown parameters land in `other` instead of being dropped, and never stop
    the known ones in the same sequence from applying.
    """
    chunks = raw.split(";")
    tokens = [tuple(_param(part) for part in chunk.split(":")) for chunk in chunks]
    fg, bg = attrs.fg, attrs.bg
    flags = set(attrs.flags)
    other = set(attrs.other)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        head = chunks[index].split(":")[0]
        index += 1
        code = token[0]
        if code is None:
            if head:
                # A parameter that is not a number is not parameter 0. Reading
                # it as one made `ESC[1;>2m` a full RESET: the model dropped
                # bold that the terminal still shows, and every attribute
                # assertion over that run then passed or failed for something
                # that never happened on screen. It is an unknown parameter,
                # and unknown parameters are recorded, not obeyed.
                if len(other) < _MAX_UNKNOWN_SGR:
                    other.add(head)
                continue
            code = 0                     # an OMITTED parameter really means 0
        if len(token) > 1 and code in _SGR_EXTENDED:
            # colon form: the colour carries its own arguments
            value = tuple(part for part in token if part is not None)
            if code == 38:
                fg = value
            elif code == 48:
                bg = value
            continue
        if code == 0:
            fg = bg = None
            flags.clear()
            other.clear()
        elif code in _SGR_ON:
            flags.add(_SGR_ON[code])
        elif code in _SGR_OFF:
            flags.difference_update(_SGR_OFF[code])
        elif 30 <= code <= 37 or 90 <= code <= 97:
            fg = (code,)
        elif code == 39:
            fg = None
        elif 40 <= code <= 47 or 100 <= code <= 107:
            bg = (code,)
        elif code == 49:
            bg = None
        elif code in _SGR_EXTENDED:
            value, index = _extended_colour(code, tokens, index)
            if code == 38:
                fg = value
            elif code == 48:
                bg = value
            # 58 (underline colour) is consumed so its arguments cannot be
            # mistaken for parameters of their own
        elif len(other) < _MAX_UNKNOWN_SGR:
            other.add(code)
    return CellAttrs(fg, bg, frozenset(flags), frozenset(other))


def _as_colour(value):
    """Normalise an expected colour: `32` and `(32,)` mean the same thing."""
    if value is None:
        return None
    if isinstance(value, int):
        return (value,)
    return tuple(value)


_KNOWN_FLAGS = frozenset(_SGR_ON.values())


def _as_flags(value, where="assert_attrs()"):
    """Normalise `has`/`lacks` and refuse a name that is not a flag.

    A misspelt name used to be checked against a set that could never contain
    it, so `lacks="bolt"` passed on any styling whatever — an assertion that
    cannot fail is a check that reports success.
    """
    names = (value,) if isinstance(value, str) else tuple(value)
    unknown = [name for name in names if name not in _KNOWN_FLAGS]
    if unknown:
        raise ValueError(
            "%s: %s is not an attribute flag. The flags are: %s"
            % (
                where,
                ", ".join(repr(name) for name in unknown),
                ", ".join(sorted(_KNOWN_FLAGS)),
            )
        )
    return names


_UNSET = object()


# ---------------------------------------------------------------------------
# Frame
# ---------------------------------------------------------------------------


class Frame:
    """One captured screen: a text plane and, when captured, an attribute plane.

    `lines` are right-stripped; `raw_lines` are padded to the full width.
    `attrs` is a row-major grid of `CellAttrs`, or None for a frame built from
    text alone (the attribute helpers then fail with that explanation rather
    than a `TypeError`). `cells` is the grid one string per cell, which is how
    column positions stay right when a wide character occupies two of them.
    """

    def __init__(self, lines, rows=None, cols=None, wrapped=(), label=None,
                 attrs=None, cells=None, overflow=None, paint_end=None):
        self.lines = [line.rstrip() for line in lines]
        self.rows = rows if rows is not None else len(self.lines)
        self.cols = cols if cols is not None else max(
            [display_width(line) for line in self.lines] or [0]
        )
        if overflow is not None:
            self.overflow = list(overflow)
            self.wrapped = frozenset(
                row for row, record in enumerate(self.overflow) if record.continued
            )
        else:
            self.wrapped = frozenset(wrapped)
            self.overflow = [
                RowOverflow(self.cols if row in self.wrapped else 0)
                for row in range(len(self.lines))
            ]
        self.label = label
        # How the wait that captured this frame ended — one of PAINT_ENDS, or
        # None for a frame nobody waited for the end of a repaint to take.
        # It is not compared by __eq__: it describes the capture, not the
        # screen.
        self.paint_end = paint_end
        # Taken as given: Screen.frame() hands over fresh copies.
        self.attrs = attrs
        self.cells = cells

    # -- text ------------------------------------------------------------

    @property
    def raw_lines(self):
        out = []
        for line in self.lines:
            pad = self.cols - display_width(line)
            out.append(line + " " * pad if pad > 0 else line)
        return out

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def row(self, index: int) -> str:
        return self.lines[index]

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        label = " %r" % self.label if self.label else ""
        return "<Frame %dx%d%s>" % (self.rows, self.cols, label)

    def __eq__(self, other):
        """Text *and* attributes.

        `after != before` is how "the screen changed" is asserted, and a
        colour-only change — a status that went from green to red without
        moving a character — is exactly the change a text-only comparison
        reports as no change at all.
        """
        if isinstance(other, Frame):
            return self.lines == other.lines and self.attrs == other.attrs
        return NotImplemented

    @property
    def paint_finished(self) -> bool:
        """Whether the program was *proved* to have finished this screen.

        True only for a repaint the program bracketed with DEC 2026 — cleanly,
        and with nothing painted outside the bracket — or for the screen an
        exit left behind when the program had bracketed everything it ever
        painted and had no repaint open (`exited`).

        A frame captured on silence alone answers False: silence is the
        strongest signal available without the program's cooperation, and it
        is still a guess. So does a frame the program died halfway through
        (`torn`), one whose brackets did not balance (`unsound`), and one an
        exit left with unbracketed painting on it (`abandoned`) — an exit
        proves that nothing more is coming, which a child killed mid-repaint
        also proves.
        """
        return self.paint_end in PAINT_PROVED

    def assert_finished(self):
        """Fail unless the program was proved to have finished this screen.

        Use it where a *negative* assertion carries the weight — "the error
        pane is gone", "no traceback" — because those pass on a half-painted
        screen for the wrong reason. It is the caller's way of demanding the
        guarantee that `expect=` alone cannot give.
        """
        if self.paint_finished:
            return self
        if self.paint_end is None:
            reason = (
                "this frame was not captured by waiting for the end of a "
                "repaint at all"
            )
        else:
            reason = (
                "this frame was captured on %s, which is not proof the "
                "program had finished the screen" % self.paint_end
            )
        raise AssertionError(self._message(reason + _WHY_NOT_PROOF.get(
            self.paint_end, _NOT_PROVED_ADVICE)))

    # -- searching -------------------------------------------------------

    def find(self, needle: str, start: int = 0):
        """Index of the first line containing `needle`, or None."""
        for i in range(start, len(self.lines)):
            if needle in self.lines[i]:
                return i
        return None

    def find_all(self, needle: str):
        """Indices of every line containing `needle`."""
        return [i for i, line in enumerate(self.lines) if needle in line]

    def logical_lines(self):
        """`(row, text)` per logical line, wrapped runs joined back together.

        A row the terminal broke onto the next carries the rest of its text on
        that next row, so a search line by line cannot see a needle that
        straddles the break. Rows that merely sit next to each other are never
        joined: joining those would invent text nobody wrote.

        A continued row is cut at the width it *continued* at, not at the
        width the screen is now. After a widening resize the grid is not
        reflowed, so the row still holds its old content followed by fresh
        blanks — pasting the padded row in would have spliced a run of spaces
        into the middle of a line the program wrote without one.
        """
        joined = []
        raw = self.raw_lines
        row = 0
        while row < len(self.lines):
            start = row
            text = ""
            while row in self.wrapped and row < len(self.lines) - 1:
                text += raw[row][:self._overflow_at(row).continued]
                row += 1
            joined.append((start, text + self.lines[row]))
            row += 1
        return joined

    def _logical_find(self, needle: str):
        """The row a logical occurrence of `needle` starts on, or None."""
        for start, text in self.logical_lines():
            if needle in text:
                return start
        return None

    def contains(self, needle: str) -> bool:
        """Whether the text is on the screen, wrapped onto two rows or not."""
        return self._logical_find(needle) is not None

    def line_with(self, needle: str) -> str:
        """The first line containing `needle`. Fails loudly if there is none."""
        index = self.find(needle)
        if index is None:
            raise AssertionError(self._message("no line contains %r" % needle))
        return self.lines[index]

    def search(self, pattern: str):
        """Index of the first line matching a regex, or None."""
        rx = re.compile(pattern)
        for i, line in enumerate(self.lines):
            if rx.search(line):
                return i
        return None

    # -- attributes ------------------------------------------------------

    def _require_attrs(self):
        if self.attrs is None:
            raise AssertionError(
                self._message(
                    "this frame carries no attribute plane: it was built from "
                    "text alone. Capture it with Screen.frame() / "
                    "TerminalSession.frame() to assert on colour."
                )
            )
        return self.attrs

    def _cell_row(self, row: int):
        """One string per grid column, so column indices survive wide chars."""
        if self.cells is not None:
            return self.cells[row]
        return list(self.raw_lines[row])

    def attrs_at(self, row: int, col: int) -> CellAttrs:
        """The attributes the cell at `(row, col)` was drawn with."""
        plane = self._require_attrs()
        if not 0 <= row < len(plane) or not 0 <= col < len(plane[row]):
            raise AssertionError(
                self._message(
                    "no cell at row %d column %d in a %dx%d frame"
                    % (row, col, self.rows, self.cols)
                )
            )
        return plane[row][col]

    def attr_runs(self, row: int, trim: bool = True):
        """The contiguous runs of like-attributed cells on `row`.

        Trailing padding is dropped unless `trim=False`, so the runs line up
        with what `lines` shows. Only *default-attributed* blanks are dropped:
        blanks carrying a background colour are part of what a viewer sees — a
        highlighted row runs to the right margin — and are kept.
        """
        plane = self._require_attrs()
        if not 0 <= row < len(plane):
            raise AssertionError(
                self._message("no row %d in a %dx%d frame" % (row, self.rows, self.cols))
            )
        attrs_row = plane[row]
        cells = self._cell_row(row)
        runs = []
        start = 0
        for col in range(1, len(attrs_row) + 1):
            if col == len(attrs_row) or attrs_row[col] != attrs_row[start]:
                runs.append(
                    AttrRun(
                        row, start, col, "".join(cells[start:col]), attrs_row[start]
                    )
                )
                start = col
        if trim and runs and runs[-1].attrs == DEFAULT_ATTRS:
            last = runs.pop()
            end = last.end
            while end > last.start and cells[end - 1] == _BLANK:
                end -= 1
            if end > last.start:
                runs.append(
                    AttrRun(row, last.start, end, "".join(cells[last.start:end]),
                            last.attrs)
                )
        return runs

    def run_with(self, needle: str, row: int = None) -> AttrRun:
        """The single run that `needle` was drawn in.

        Fails loudly if the text is not there; if it straddles two runs —
        itself a finding, since it means the substring was not drawn in one
        style; or if it appears more than once and the copies were *not* drawn
        alike, because then there is no single answer to give. Looking only at
        the first copy is how a green STATUS on one row certified a red one on
        another. Name a `row` to say which copy is meant; copies that agree
        need no naming.
        """
        self._require_attrs()
        if not needle:
            raise ValueError("run_with() needs a non-empty substring")
        if row is not None:
            if not 0 <= row < len(self.attrs):
                raise AssertionError(
                    self._message(
                        "no row %d in a %dx%d frame" % (row, self.rows, self.cols)
                    )
                )
            candidates = [row]
        else:
            candidates = self.find_all(needle)
            if not candidates:
                if self._logical_find(needle) is not None:
                    raise AssertionError(
                        self._message(
                            "%r is on the screen but the terminal wrapped it "
                            "across two rows, so it was not drawn as one run"
                            % needle
                        )
                    )
                raise AssertionError(self._message("no line contains %r" % needle))
        runs = []
        for index in candidates:
            runs.extend(self._runs_with(index, needle))
        if not runs:
            raise AssertionError(
                self._message("row %d does not contain %r" % (candidates[0], needle))
            )
        if len({run.attrs for run in runs}) > 1:
            raise AssertionError(
                self._attr_message(
                    "%r is drawn %d different ways and no row was named: %s"
                    % (
                        needle,
                        len({run.attrs for run in runs}),
                        "; ".join(
                            "row %d col %d %s"
                            % (run.row, run.start, run.attrs.describe())
                            for run in runs
                        ),
                    ),
                    runs[0].row,
                )
            )
        return runs[0]

    def _runs_with(self, row: int, needle: str):
        """The run each occurrence of `needle` on `row` was drawn in."""
        cells = self._cell_row(row)
        joined = "".join(cells)
        # a cell holds one character or, for the second half of a wide one,
        # nothing at all — so this maps string offsets back to grid columns
        columns = [col for col, cell in enumerate(cells) for _ in cell]
        runs = self.attr_runs(row, trim=False)
        found = []
        position = joined.find(needle)
        while position >= 0:
            found.append(
                self._run_covering(
                    row,
                    columns[position],
                    columns[position + len(needle) - 1],
                    needle,
                    runs,
                )
            )
            position = joined.find(needle, position + 1)
        return found

    def _run_covering(self, row: int, first: int, last: int, needle: str, runs):
        for run in runs:
            if run.start <= first and last < run.end:
                return run
        spanned = [run for run in runs if run.start <= last and first < run.end]
        raise AssertionError(
            self._attr_message(
                "%r on row %d is not drawn as one run — it spans %d: %s"
                % (
                    needle,
                    row,
                    len(spanned),
                    ", ".join(
                        "%r %s" % (run.text, run.attrs.describe()) for run in spanned
                    ),
                ),
                row,
            )
        )

    def attrs_for(self, needle: str, row: int = None) -> CellAttrs:
        """The attributes `needle` was drawn with."""
        return self.run_with(needle, row=row).attrs

    def assert_attrs(self, needle: str, fg=_UNSET, bg=_UNSET, has=(), lacks=(),
                     row: int = None):
        """Assert how `needle` was drawn.

        `fg`/`bg` take a parameter tuple or the bare int (`31` == `(31,)`), and
        `None` means the terminal default. `has`/`lacks` take a flag name or a
        list of them. At least one of the four is required — without one this
        passed on any styling at all — and a name that is not a flag is
        refused rather than quietly never matched.
        """
        if fg is _UNSET and bg is _UNSET and not has and not lacks:
            raise ValueError(
                "assert_attrs(%r) has nothing to assert: give at least one of "
                "fg, bg, has, lacks, or it passes on any styling at all" % needle
            )
        wanted = _as_flags(has, "assert_attrs(has=)")
        unwanted = _as_flags(lacks, "assert_attrs(lacks=)")
        run = self.run_with(needle, row=row)
        actual = run.attrs
        problems = []
        if fg is not _UNSET and actual.fg != _as_colour(fg):
            problems.append("foreground is %r, expected %r" % (actual.fg, _as_colour(fg)))
        if bg is not _UNSET and actual.bg != _as_colour(bg):
            problems.append("background is %r, expected %r" % (actual.bg, _as_colour(bg)))
        for flag in wanted:
            if flag not in actual.flags:
                problems.append("%s is not set" % flag)
        for flag in unwanted:
            if flag in actual.flags:
                problems.append("%s is set" % flag)
        if problems:
            raise AssertionError(
                self._attr_message(
                    "%r is drawn %s: %s"
                    % (needle, actual.describe(), "; ".join(problems)),
                    run.row,
                )
            )
        return self

    def assert_attrs_differ(self, one: str, other: str):
        """Assert two pieces of text were not drawn the same way."""
        first = self.run_with(one)
        second = self.run_with(other)
        if first.attrs == second.attrs:
            raise AssertionError(
                self._message(
                    "%r and %r are both drawn %s — they should be "
                    "distinguishable" % (one, other, first.attrs.describe())
                )
            )
        return self

    # -- assertions ------------------------------------------------------

    def assert_contains(self, needle: str, message: str = None):
        if not self.contains(needle):
            raise AssertionError(
                self._message(message or "frame does not contain %r" % needle)
            )
        return self

    def assert_not_contains(self, needle: str, message: str = None):
        """Fail if the text is on the screen — including across a wrap.

        A line-by-line search cannot see a needle the terminal broke over two
        rows, so on a narrow screen showing FAILED this used to pass.

        It also fails on a frame the harness KNOWS cannot be read: one the
        program died inside a repaint of (`torn`), and one whose brackets did
        not balance (`unsound`, where the text plane itself is suspect).
        "That text is not on the screen" is not an observation about a screen
        half of which was never drawn. `abandoned` and `quiet` are not
        refused — they mean nothing vouched for the screen, which is weaker
        than knowing it is torn, and `assert_finished()` is how a caller
        demands more.
        """
        if self.paint_end in _KNOWN_PARTIAL:
            raise AssertionError(self._message(
                "a negative assertion cannot be made against this screen: it "
                "was captured on %s, which the harness knows is not a whole "
                "screen%s"
                % (self.paint_end, _WHY_NOT_PROOF.get(self.paint_end, ""))
            ))
        index = self._logical_find(needle)
        if index is not None:
            raise AssertionError(
                self._message(
                    message
                    or "frame contains %r on row %d" % (needle, index)
                )
            )
        return self

    def _overflow_at(self, row: int) -> RowOverflow:
        if 0 <= row < len(self.overflow):
            return self.overflow[row]
        return _NO_OVERFLOW

    def overlong_lines(self):
        """`(row, width)` for every logical line wider than the terminal.

        Two things put a row here, and the emulator records both as they
        happen rather than inferring them from the screen afterwards:

        * the row wrapped onto the next one — the width reported is the sum of
          the widths those rows were *drawn* at, never a width recomputed from
          the current size, which is how a resize used to report 360 cells of
          content that were only ever 200. A wrapped run still has to be wider
          than the screen to count: a row that wrapped at 10 cells is not too
          wide for the 40-column screen it was resized onto, and reporting it
          made `assert_within_width()` fail on a frame where every row fits;
        * cells were destroyed at the right margin because autowrap was off —
          the width reported is what the program drew, including what the
          screen never showed.

        A row that exactly fills the width is not a violation; see
        `assert_within_width` for what a terminal cannot tell about those.
        """
        violations = []
        row = 0
        while row < len(self.lines):
            record = self._overflow_at(row)
            if record.continued:
                start = row
                width = 0
                while self._overflow_at(row).continued and row < len(self.lines) - 1:
                    width += self._overflow_at(row).continued
                    row += 1
                width += display_width(self.lines[row]) + self._overflow_at(row).lost
                if width > self.cols:
                    violations.append((start, width))
            else:
                width = display_width(self.lines[row]) + record.lost
                if width > self.cols:
                    violations.append((row, width))
            row += 1
        return violations

    def full_width_rows(self):
        """Rows whose content reaches the last column.

        Nothing a terminal can observe tells one of these from a row some
        program truncated to make it fit: both arrive as `cols` cells of text
        and a cursor address for the next row.
        """
        rows = []
        for row, line in enumerate(self.lines):
            if row in self.wrapped:
                continue
            if self.cells is not None and self.cols and row < len(self.cells):
                occupied = self.cells[row][self.cols - 1] != _BLANK
            else:
                occupied = display_width(line) >= self.cols
            if occupied:
                rows.append(row)
        return rows

    def assert_within_width(self, allow_full_width: bool = False):
        """Fail if any line is wider than the terminal.

        A row that reaches the last column is *refused*, not passed. ncurses
        clips at the window edge and cursor-addresses every row rather than
        letting the terminal wrap, so on a curses screen there are no wrap
        flags to read and this helper would otherwise be incapable of failing
        — a check that always reports success. What the terminal can say is
        which rows run to the margin; whether that is an exact fit or content
        cut off to make it fit is the caller's to state, with
        `allow_full_width=True` (and then the pass means "nothing wrapped and
        nothing was destroyed at the margin", which is all it ever meant).
        """
        violations = self.overlong_lines()
        if violations:
            detail = ", ".join(
                "row %d is %d cells" % (row, width) for row, width in violations
            )
            raise AssertionError(
                self._message(
                    "content is wider than the %d-column terminal: %s"
                    % (self.cols, detail)
                )
            )
        if not allow_full_width:
            full = self.full_width_rows()
            if full:
                raise AssertionError(
                    self._message(
                        "cannot certify this frame: %s of %d run to the last "
                        "column, and a terminal cannot tell content that "
                        "fitted exactly from content truncated to fit — "
                        "ncurses clips at the window edge instead of wrapping. "
                        "Assert on what those rows should say, or pass "
                        "allow_full_width=True if a full-width row is intended."
                        % (
                            ", ".join("row %d" % row for row in full),
                            self.cols,
                        )
                    )
                )
        return self

    # -- evidence --------------------------------------------------------

    def dump(self, path, header: str = None) -> Path:
        """Write the frame to `path` as plain text. Returns the path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = self.text
        if header:
            body = "# " + header + "\n" + body
        path.write_text(body + "\n", encoding="utf-8")
        return path

    def _message(self, reason: str) -> str:
        header = "%s (%dx%d%s)" % (
            self.label or "frame",
            self.rows,
            self.cols,
            "" if self.paint_end is None else ", captured on %s" % self.paint_end,
        )
        rule = "-" * max(len(header), min(self.cols, 100))
        numbered = "\n".join(
            "%3d|%s" % (i, line) for i, line in enumerate(self.lines)
        )
        return "%s\n%s\n%s\n%s\n%s" % (reason, header, rule, numbered, rule)

    def _attr_message(self, reason: str, row: int = None) -> str:
        """`_message` plus how the row in question was drawn, run by run."""
        base = self._message(reason)
        if self.attrs is None or row is None:
            return base
        detail = ["how row %d was drawn:" % row]
        for run in self.attr_runs(row, trim=False):
            detail.append(
                "  cols %3d-%-3d %-24s %s"
                % (run.start, run.end - 1, repr(run.text), run.attrs.describe())
            )
        return base + "\n" + "\n".join(detail)


# ---------------------------------------------------------------------------
# Screen — the terminal emulator
# ---------------------------------------------------------------------------

_BLANK = " "
_WIDE_PLACEHOLDER = ""


class Screen:
    """A grid of cells that escape sequences mutate.

    Feed it the bytes a program writes to its terminal; ask it for `lines()` or
    a `frame()`.
    """

    def __init__(self, rows: int = DEFAULT_ROWS, cols: int = DEFAULT_COLS):
        self.rows = rows
        self.cols = cols
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        # Survives reset(): a program that issues RIS after drawing has not
        # made what it drew on the alternate screen any less evidence.
        self._retired_alt = None
        self._reset_paint_record()
        self.reset()

    # -- state -----------------------------------------------------------

    def _reset_paint_record(self):
        """The DEC 2026 record — set up once, and never by `reset()`.

        Everything here is a record of what the PROGRAM has done: brackets it
        opened and closed, glyphs it put on the screen outside one, sequences
        it sent that a program bracketing its repaints cannot send. `ESC c`
        resets the screen; it is a sequence the program chose to send, and a
        program does not get to erase the record of its own behaviour by
        asking for a blank screen. Wiping these along with the grid laundered
        `unsound` into `synchronised` — and took the session-wide backstop
        with it, since that reads `synchronized_faults` — and laundered
        `abandoned` into `exited` for a program that had never bracketed a
        glyph in its life.

        * `synchronized_update` — True between the program's own "here comes
          one repaint" and "that repaint is complete".
        * `synchronized_opens` / `synchronized_updates` — how many brackets it
          has opened cleanly, and how many it has closed cleanly. A wait
          baselines both, because a close only speaks for the wait it ends if
          the bracket was opened during that wait or enclosed painting done
          during it. See `TerminalSession._await_paint_end`.
        * `unbracketed_paints` / `bracketed_paints` — how much it has drawn
          with no bracket open, and how much with one open.
        * `synchronized_faults` — 2026 sequences a program bracketing its
          repaints cannot emit.
        * `screen_vouched` — whether the screen AS IT STANDS is covered by a
          statement of the program's. True while nothing has touched it since
          the last close of a bracket that enclosed painting. See
          `_synchronized_update`.
        """
        self.synchronized_update = False
        self.synchronized_opens = 0
        self.synchronized_updates = 0
        self.unbracketed_paints = 0
        self.bracketed_paints = 0
        self.synchronized_faults = 0
        # Vacuously true: a screen nothing has been drawn on carries nothing
        # the program has not vouched for.
        self.screen_vouched = True
        self._bracket_paint_mark = 0

    def reset(self):
        """RIS: the screen goes back to its defaults. The record does not."""
        self._attrs = DEFAULT_ATTRS
        self._grid = [self._blank_row() for _ in range(self.rows)]
        self._attr_grid = [self._blank_attr_row() for _ in range(self.rows)]
        self._overflow = [_NO_OVERFLOW] * self.rows
        self.cursor_row = 0
        self.cursor_col = 0
        self._pending_wrap = False
        self.scroll_top = 0
        self.scroll_bottom = self.rows - 1
        self.autowrap = True
        self.insert_mode = False
        self.cursor_visible = True
        self.application_cursor_keys = False
        self.application_keypad = False
        self.in_alt_screen = False
        self._saved_cursor = None
        self._saved_screen = None
        self._last_char = None
        self._tabs = set(range(8, self.cols, 8))
        # G0..G3 hold the designated character sets; GL says which one the
        # printable range is currently taken from. "B" is ASCII, "0" is DEC
        # Special Graphics. SO/SI switch GL, ESC ( ) * + designate.
        self._charsets = {"G0": "B", "G1": "B", "G2": "B", "G3": "B"}
        self._gl = "G0"
        self._charset_slot = None
        self._state = "ground"
        self._csi = []
        self._csi_overlong = False
        self._string_esc = False

    def _blank_row(self):
        return [_BLANK] * self.cols

    def _blank_attrs(self) -> CellAttrs:
        """What a cell blanked *right now* carries.

        Back-colour erase, which is what `xterm-256color` advertises (`bce`):
        an erased cell keeps the current background and loses everything else.
        """
        background = self._attrs.bg
        return DEFAULT_ATTRS if background is None else CellAttrs(bg=background)

    def _blank_attr_row(self):
        return [self._blank_attrs()] * self.cols

    def resize(self, rows: int, cols: int):
        """Resize the grid, keeping the top-left content.

        This is an action of the *harness*, not of the program, and it can
        destroy what the program drew: the top-left corner is kept and
        everything past the new edges is deleted. A screen the harness has
        cut cells out of is no longer the screen the program vouched for — so
        if any cell that goes was one something had been drawn into, the
        screen stops being vouched for until the program paints a whole
        bracketed repaint again, which is what a program does on SIGWINCH.

        Without that, the harness's own `resize()` could delete drawn cells
        from the screen of a program that had already exited — one that could
        never repaint again — and the exit branch, which asks only what the
        PROGRAM did, still called the wreckage `exited`.
        """
        if self._drops_drawn_cells(rows, cols):
            self.screen_vouched = False
        grid = [self._blank_row_of(cols) for _ in range(rows)]
        attr_grid = [[DEFAULT_ATTRS] * cols for _ in range(rows)]
        # A row that overflowed still overflowed: the record says at which
        # width, so keeping it is neither a rescale nor a loss. Dropping it
        # when narrowing manufactured a clean bill of health for a screen that
        # had one, and keeping a bare flag while widening reported a width
        # that was never on the screen.
        # Both readers of these records — `overlong_lines()` and
        # `logical_lines()` — measure against the width the row *continued* at
        # rather than the width the screen happens to be now, which is what
        # keeps a resize from turning a kept record into a wrong answer.
        overflow = [_NO_OVERFLOW] * rows
        for r in range(min(rows, self.rows)):
            for c in range(min(cols, self.cols)):
                grid[r][c] = self._grid[r][c]
                attr_grid[r][c] = self._attr_grid[r][c]
            overflow[r] = self._overflow[r]
        self.rows = rows
        self.cols = cols
        self._grid = grid
        self._attr_grid = attr_grid
        self._overflow = overflow
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_row = min(self.cursor_row, rows - 1)
        self.cursor_col = min(self.cursor_col, cols - 1)
        self._pending_wrap = False
        self._tabs = set(range(8, cols, 8))
        # a column cut off the right edge can have been half of a glyph
        for row in range(rows):
            self._heal_wide_row(row)
        # A resize while the program holds the alternate screen must not cost it
        # the primary screen it is holding: that screen is what it gives back on
        # the way out, and a TUI is resized far more often than it exits.
        if self._saved_screen is not None:
            self._saved_screen = self._fit_saved_screen(self._saved_screen, rows, cols)

    def _drops_drawn_cells(self, rows: int, cols: int) -> bool:
        """Whether resizing to `rows` x `cols` would delete a drawn cell.

        A cell counts as drawn if it holds anything but a blank at the
        terminal's default attributes — a background colour is drawing too,
        since back-colour erase is how a pane paints its own ground.
        """
        def drawn(row, first_col):
            chars = self._grid[row]
            attrs = self._attr_grid[row]
            return any(
                chars[col] != _BLANK or attrs[col] != DEFAULT_ATTRS
                for col in range(first_col, self.cols)
            )

        if any(drawn(row, 0) for row in range(rows, self.rows)):
            return True                          # rows falling off the bottom
        if cols >= self.cols:
            return False
        return any(drawn(row, cols) for row in range(min(rows, self.rows)))

    @staticmethod
    def _blank_row_of(cols):
        return [_BLANK] * cols

    @staticmethod
    def _fit_plane(plane, rows, cols, blank):
        """A saved grid reshaped to `rows` x `cols`: clipped, then padded."""
        fitted = [
            row[:cols] + [blank] * max(0, cols - len(row)) for row in plane[:rows]
        ]
        fitted.extend([blank] * cols for _ in range(rows - len(fitted)))
        return fitted

    @classmethod
    def _fit_saved_screen(cls, saved, rows, cols):
        """A saved (grid, attrs, overflow, cursor) tuple reshaped to a size."""
        grid, attr_grid, overflow, row, col = saved
        return (
            cls._fit_plane(grid, rows, cols, _BLANK),
            cls._fit_plane(attr_grid, rows, cols, DEFAULT_ATTRS),
            (list(overflow) + [_NO_OVERFLOW] * rows)[:rows],
            min(row, rows - 1),
            min(col, cols - 1),
        )

    # -- output ----------------------------------------------------------

    def lines(self):
        """The visible screen, one padded string per row."""
        return ["".join(row) for row in self._grid]

    def cells(self):
        """The visible screen, one string per grid cell."""
        return [row[:] for row in self._grid]

    def attrs(self):
        """How every cell was drawn, row by row."""
        return [row[:] for row in self._attr_grid]

    def last_alt_frame(self, label: str = None):
        """The alternate screen as it stood when the program gave it back.

        None until a program has taken the alternate screen and left it. This
        is what a crashed or terminated TUI drew before it died: `?1049l`
        replaces that screen with the primary one, so `frame()` afterwards
        shows the shell, not the program.
        """
        if self._retired_alt is None:
            return None
        grid, attr_grid, overflow, rows, cols = self._retired_alt
        return Frame(
            ["".join(row) for row in grid],
            rows=rows,
            cols=cols,
            label=label or "last alternate screen",
            attrs=[row[:] for row in attr_grid],
            cells=[row[:] for row in grid],
            overflow=list(overflow),
        )

    def wrapped_rows(self):
        """Rows whose content continued onto the following row."""
        return [i for i, record in enumerate(self._overflow) if record.continued]

    def overflow(self):
        """What ran past the right margin, row by row."""
        return self._overflow[:]

    def frame(self, label: str = None, paint_end: str = None) -> Frame:
        return Frame(
            self.lines(),
            rows=self.rows,
            cols=self.cols,
            wrapped=self.wrapped_rows(),
            label=label,
            attrs=self.attrs(),
            cells=self.cells(),
            overflow=self.overflow(),
            paint_end=paint_end,
        )

    # -- input -----------------------------------------------------------

    def feed(self, data):
        """Feed bytes (or str) written by the program."""
        if isinstance(data, (bytes, bytearray)):
            data = self._decoder.decode(bytes(data))
        for ch in data:
            self._feed_char(ch)

    def _feed_char(self, ch):
        state = self._state
        if state == "ground":
            self._ground(ch)
        elif state == "esc":
            self._escape(ch)
        elif state == "csi":
            self._csi_char(ch)
        elif state == "string":
            self._string_char(ch)
        elif state == "charset":
            self._charset(ch)
        elif state == "esc_intermediate":
            self._esc_intermediate(ch)

    # -- ground ----------------------------------------------------------

    def _ground(self, ch):
        code = ord(ch)
        if code == 0x1B:
            self._state = "esc"
        elif code == 0x0D:
            self.cursor_col = 0
            self._pending_wrap = False
        elif code in (0x0A, 0x0B, 0x0C):
            self._index()
            self._pending_wrap = False
        elif code == 0x08:
            if self._pending_wrap:
                self._pending_wrap = False
            elif self.cursor_col > 0:
                self.cursor_col -= 1
        elif code == 0x09:
            self._tab()
        elif code == 0x0E:
            self._gl = "G1"          # shift out
        elif code == 0x0F:
            self._gl = "G0"          # shift in
        elif code in (0x07, 0x00):
            pass
        elif code < 0x20:
            pass
        elif code == 0x7F:
            # DEL. Every terminal discards it; drawing it put a character on
            # the screen that no human at a terminal would ever see.
            pass
        elif 0x80 <= code <= 0x9F:
            # A C1 control, which is what a UTF-8 terminal reads U+0080-U+009F
            # as. ECMA-48 defines each as `ESC` plus the byte minus 0x40, so
            # they go through the same dispatch as their seven-bit twins and
            # no new meaning is invented here: 0x9B is CSI, 0x9D is OSC, 0x85
            # is NEL, 0x8D is RI. Drawing them instead was the leak this
            # module claims not to have — `0x9B 1 ; 1 H` addresses the cursor
            # on a real terminal and left `1;1H` in the text plane here,
            # which is the same defect as an aborted OSC dropping its bytes
            # into a frame, arriving one byte earlier. A relay pane rendering
            # a log line with Windows-1252 mojibake in it puts exactly these
            # code points on the wire.
            self._escape(chr(code - 0x40))
        else:
            self._put(ch)

    def _combine(self, ch):
        """A zero-width mark joins the cell that precedes it.

        Dropping it rewrote the text: `e` followed by U+0301 arrived on the
        screen as `e`, so a frame showed a word the terminal never displayed
        and `assert_contains("\u00e9")` could not pass on a program that drew
        it decomposed. The grid holds one string per cell, so the mark has
        somewhere to go, and `display_width` already counts it as zero — the
        column arithmetic is unchanged.
        """
        col = self.cursor_col if self._pending_wrap else self.cursor_col - 1
        if col < 0:
            return                       # nothing on this row precedes it
        row = self._grid[self.cursor_row]
        if row[col] == _WIDE_PLACEHOLDER and col > 0:
            col -= 1                     # it belongs to the wide cell itself
        if len(self._grid[self.cursor_row][col]) > _MAX_COMBINING:
            return
        self._painted()
        row[col] += ch
        self._last_char = row[col]

    def _tab(self):
        stops = [t for t in sorted(self._tabs) if t > self.cursor_col]
        self.cursor_col = stops[0] if stops else self.cols - 1
        self._pending_wrap = False

    def _back_tab(self):
        """CBT, which is `cbt` in `xterm-256color` and so is a thing ncurses
        sends. Unimplemented, its parameters were drawn as nothing and every
        cell after it landed in the wrong column."""
        stops = [t for t in sorted(self._tabs) if t < self.cursor_col]
        self.cursor_col = stops[-1] if stops else 0
        self._pending_wrap = False

    def _translate(self, ch):
        """One character as the *designated* set draws it.

        Idempotent: a glyph the table produced is not in the table's keys, so
        REP repeating `_last_char` repeats the glyph rather than re-mapping.
        """
        if self._charsets[self._gl] != "0":
            return ch
        return _DEC_SPECIAL_GRAPHICS.get(ch, ch)

    def _put(self, ch):
        ch = self._translate(ch)
        width = _char_width(ch)
        if width == 0:
            self._combine(ch)
            return
        self._painted()
        overflowed = False
        if self._pending_wrap:
            if self.autowrap:
                self._record_wrap()
            else:
                # autowrap off: a real terminal overwrites the last cell, so
                # this character is drawn and the one under it is destroyed.
                # Nothing on the screen shows that afterwards, so it is
                # recorded here or it is lost silently.
                self._record_margin_loss(width)
                overflowed = True
            self._pending_wrap = False
        if width == 2 and self.cursor_col == self.cols - 1:
            # a wide character cannot straddle the right margin
            if self.autowrap:
                self._record_wrap()
            else:
                if not overflowed:
                    self._record_margin_loss(width)   # once, not once per test
                return
        if not overflowed:
            # this row is being written, so any overflow recorded for it
            # described content that is no longer there
            self._overflow[self.cursor_row] = _NO_OVERFLOW
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        self._break_wide_pair(self.cursor_row, self.cursor_col)
        if width == 2:
            self._break_wide_pair(self.cursor_row, self.cursor_col + 1)
        if self.insert_mode:
            shift = width
            keep = self.cols - self.cursor_col
            row[self.cursor_col:] = ([_BLANK] * shift + row[self.cursor_col:])[:keep]
            attr_row[self.cursor_col:] = (
                [self._blank_attrs()] * shift + attr_row[self.cursor_col:]
            )[:keep]
        if self.insert_mode:
            self._heal_wide_row(self.cursor_row)
        row[self.cursor_col] = ch
        attr_row[self.cursor_col] = self._attrs
        if width == 2:
            row[self.cursor_col + 1] = _WIDE_PLACEHOLDER
            attr_row[self.cursor_col + 1] = self._attrs
        self._last_char = ch
        end = self.cursor_col + width
        if end >= self.cols:
            self.cursor_col = self.cols - 1
            self._pending_wrap = True
        else:
            self.cursor_col = end

    # -- escape ----------------------------------------------------------

    def _escape(self, ch):
        if self._restarts_at_an_escape(ch):
            return
        self._state = "ground"
        if ch == "[":
            self._csi = []
            self._csi_overlong = False
            self._state = "csi"
        elif ch in "]P^_X":
            self._string_esc = False
            self._state = "string"
        elif ch in _CHARSET_SLOTS:
            self._charset_slot = _CHARSET_SLOTS[ch]
            self._state = "charset"
        elif 0x20 <= ord(ch) <= 0x2F:
            # An intermediate byte: the sequence continues until a final byte
            # in 0x30-0x7E. Dropping the intermediate and returning to ground
            # printed that final byte as text — `ESC # 8` (DECALN) put a "8"
            # on the screen, `ESC SP F` an "F" — so a sequence this harness
            # does not implement became content in a frame handed to a judge.
            self._state = "esc_intermediate"
        elif ch == "7":
            self._save_cursor()
        elif ch == "8":
            self._restore_cursor()
        elif ch == "D":
            self._index()
        elif ch == "M":
            self._reverse_index()
        elif ch == "E":
            self.cursor_col = 0
            self._index()
        elif ch == "H":
            self._tabs.add(self.cursor_col)
        elif ch == "c":
            # RIS. Blanking the screen is a change to the screen like any
            # other, so it is painting and is counted as such — a program that
            # wipes the screen outside a bracket has drawn outside one. What
            # survives is the record of everything it had already done: see
            # `_reset_paint_record`.
            self.reset()
            self._painted()
        elif ch == "=":
            self.application_keypad = True
        elif ch == ">":
            self.application_keypad = False
        # anything else: a one-character escape we do not need

    def _esc_intermediate(self, ch):
        """Swallow the rest of an escape sequence this harness does not model.

        Further intermediates (0x20-0x2F) keep the sequence open; a final byte
        (0x30-0x7E) ends it. Either way nothing is drawn — an unhandled
        sequence leaves no glyph behind.
        """
        if self._restarts_at_an_escape(ch):
            return
        if 0x20 <= ord(ch) <= 0x2F:
            return
        self._state = "ground"

    def _restarts_at_an_escape(self, ch) -> bool:
        """Whether `ch` is an `ESC`, which begins a sequence from ANY state.

        That is what makes it an escape, and it was handled only in ground and
        inside strings — so `ESC ESC [ 3 1 m X` drew `[31mX`: the second `ESC`
        was read as "a one-character escape we do not need" and it ate the `[`
        of the sequence that followed. The same defect as an aborted OSC
        dropping its bytes into a frame, in three more states.

        `CAN` (0x18) and `SUB` (0x1A) cancel a sequence, and are deliberately
        NOT handled here. They are handled where they are observable — inside
        a CSI and inside a string, where the parser would otherwise go on
        eating what the program drew. In these three states the fallthrough
        already ends the sequence and draws nothing, so a branch for them
        could not change a frame; a sweep mutation removing one proved exactly
        that, and it was deleted rather than kept as decoration.
        """
        if ch != "\x1b":
            return False
        self._state = "esc"
        self._charset_slot = None
        return True

    def _charset(self, ch):
        """The character after `ESC ( ` designates a set into that slot.

        "0" is DEC Special Graphics — the box-drawing set every curses border
        is drawn with; anything else (ASCII is "B") draws the characters
        themselves. A designation can carry intermediates of its own —
        `ESC ( % 5` — and consuming only the first byte left the final one to
        be drawn as text.
        """
        if self._restarts_at_an_escape(ch):
            return
        if 0x20 <= ord(ch) <= 0x2F:
            return                       # a multi-byte designation continues
        self._state = "ground"
        slot = self._charset_slot
        self._charset_slot = None
        if slot is not None:
            self._charsets[slot] = ch

    def _string_char(self, ch):
        # OSC / DCS / APC / PM: runs until BEL, ST, or a cancel
        if ord(ch) in (0x18, 0x1A):
            # CAN and SUB cancel the string. Ignoring them ran it on until the
            # next BEL, eating every cell the program drew in between — an
            # aborted OSC swallowing a whole pane out of a frame, with nothing
            # left to show it had ever been drawn.
            self._string_esc = False
            self._state = "ground"
            return
        if self._string_esc:
            self._string_esc = False
            # An ESC inside a string is ST when `\\` follows it and ABORTS the
            # string when anything else does — and either way the right thing
            # to do is hand the byte to `_escape`, which starts a sequence for
            # the openers and lands in ground for `\\`, which starts nothing.
            # Swallowing it instead consumed only the `[` of the sequence that
            # followed, so `ESC ] 0 ; t ESC [ 1 ; 1 H X` drew `1;1HX` into the
            # text plane: a frame showing content no terminal ever displayed,
            # which is the one thing an evidence artefact must never do.
            self._state = "esc"
            self._escape(ch)
            return
        if ch == "\x07":
            self._state = "ground"
        elif ch == "\x9c":
            # ST in its one-character C1 form, which is what terminates an
            # OSC on a UTF-8 terminal that did not use `ESC \\`. Reading it as
            # string content ran the string on until the next BEL and ate
            # everything the program drew in between.
            self._state = "ground"
        elif ch == "\x1b":
            self._string_esc = True

    # -- CSI -------------------------------------------------------------

    def _csi_char(self, ch):
        code = ord(ch)
        if 0x30 <= code <= 0x3F or 0x20 <= code <= 0x2F:
            if len(self._csi) >= _MAX_CSI:
                # The parameter list is the one buffer in this parser that
                # a program controls the length of, and it was unbounded — and
                # it was a string grown one character at a time, so a megabyte
                # of `;` inside a single CSI cost quadratic time and spent all
                # of it inside `feed()`, which checks its deadline only
                # between reads. It is a list now, joined once at the final
                # byte, so the cost is linear in what the program wrote. Past
                # the cap the
                # sequence is still parsed to its final byte — the bytes after
                # it are not content — but it is dropped rather than
                # dispatched on what was kept, because dispatching a truncated
                # parameter list would act on a cell the program never named.
                self._csi_overlong = True
                return
            self._csi.append(ch)
            return
        if 0x40 <= code <= 0x7E:
            self._state = "ground"
            raw, self._csi = "".join(self._csi), []
            if self._csi_overlong:
                self._csi_overlong = False
                return
            self._dispatch_csi(raw, ch)
            return
        if code in (0x18, 0x1A):
            # CAN / SUB cancel the sequence: the bytes after them are content
            # again, and reading them as parameters ate what the program drew.
            self._state = "ground"
            self._csi = []
            self._csi_overlong = False
            return
        # a control character inside a sequence: execute it, stay in CSI
        if code < 0x20:
            self._ground(ch)
            return
        self._state = "ground"

    def _dispatch_csi(self, raw, final):
        private = ""
        if raw and raw[0] in "?<>=":
            private = raw[0]
            raw = raw[1:]
        raw = raw.rstrip(" !\"#$%&'()*+,-./")
        if final == "m" and not private:
            # SGR: the one sequence that draws nothing and changes everything
            self._attrs = _apply_sgr(self._attrs, raw)
            return
        params = []
        for chunk in raw.split(";"):
            params.append(_param(chunk.split(":")[0]))
        if not params:
            params = [None]

        def p(index, default=1):
            """One parameter, with zero meaning "the default" — as it does.

            A count of zero is not a count of zero: VT-style sequences read
            `CSI 0 A` as one row, `CSI 0 P` as one character. Reading it
            literally made `CSI 0 A` move nothing at all. The sequences whose
            parameter really is a mode ask for `p(index, 0)`, so zero and the
            default are the same value there and this changes nothing for them.
            """
            if index < len(params) and params[index]:
                return params[index]
            return default

        if private == "?":
            if final in "hl":
                self._private_mode(params, final == "h")
            return
        if private:
            return

        if final == "A":
            self._move_up(p(0))
        elif final == "B":
            self._move_down(p(0))
        elif final == "C":
            self.cursor_col = min(self.cols - 1, self.cursor_col + p(0))
            self._pending_wrap = False
        elif final == "D":
            self.cursor_col = max(0, self.cursor_col - p(0))
            self._pending_wrap = False
        elif final == "E":
            self._move_down(p(0))
            self.cursor_col = 0
        elif final == "F":
            self._move_up(p(0))
            self.cursor_col = 0
        elif final in ("G", "`"):
            self.cursor_col = self._clamp_col(p(0) - 1)
            self._pending_wrap = False
        elif final == "d":
            self.cursor_row = self._clamp_row(p(0) - 1)
            self._pending_wrap = False
        elif final in ("H", "f"):
            self.cursor_row = self._clamp_row(p(0) - 1)
            self.cursor_col = self._clamp_col(p(1) - 1)
            self._pending_wrap = False
        elif final == "J":
            self._erase_display(p(0, 0))
        elif final == "K":
            self._erase_line(p(0, 0))
        elif final == "L":
            self._insert_lines(p(0))
        elif final == "M":
            self._delete_lines(p(0))
        elif final == "P":
            self._delete_chars(p(0))
        elif final == "@":
            self._insert_chars(p(0))
        elif final == "X":
            self._erase_chars(p(0))
        elif final == "S":
            self._scroll_up(p(0))
        elif final == "T":
            self._scroll_down(p(0))
        elif final == "b":
            # REP repeats the last GRAPHIC character. With none — at start-up,
            # or after a hard reset — a terminal does nothing; repeating the
            # blank `_last_char` was initialised to painted cells nobody asked
            # for, with the current background on them, and moved the cursor
            # past them.
            if self._last_char is not None:
                for _ in range(self._clamp_repeat(p(0))):
                    self._put(self._last_char)
        elif final == "I":
            for _ in range(min(p(0), self.cols)):
                self._tab()
        elif final == "Z":
            for _ in range(min(p(0), self.cols)):
                self._back_tab()
        elif final == "g":
            if p(0, 0) == 3:
                self._tabs.clear()
            else:
                self._tabs.discard(self.cursor_col)
        elif final == "r":
            top = p(0) - 1
            bottom = p(1, self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top = top
                self.scroll_bottom = bottom
            else:
                self.scroll_top = 0
                self.scroll_bottom = self.rows - 1
            self.cursor_row = 0
            self.cursor_col = 0
            self._pending_wrap = False
        elif final in "hl":
            if 4 in [x for x in params if x is not None]:
                self.insert_mode = final == "h"
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        # n, c, t and friends: consumed, nothing to draw

    def _private_mode(self, params, on):
        for mode in params:
            if mode == 1:
                self.application_cursor_keys = on
            elif mode == 7:
                self.autowrap = on
            elif mode == 25:
                self.cursor_visible = on
            elif mode == 2026:
                self._synchronized_update(on)
            elif mode in (47, 1047, 1049):
                self._switch_screen(on, save_cursor=(mode == 1049))

    def _synchronized_update(self, on):
        """DEC private mode 2026: the program brackets one whole repaint.

        This is the only thing a program can say about its own painting that a
        terminal is able to *observe*: between `ESC [ ? 2026 h` and
        `ESC [ ? 2026 l` the screen is by construction unfinished, and at the
        closing sequence it is by construction finished. Everything else a
        terminal can measure — silence, a settled burst — is a guess about a
        program that might simply be slow.

        WHAT THE CLOSING SEQUENCE VOUCHES FOR IS THE BRACKET, NOT THE SCREEN.
        "The repaint I opened is complete" is a statement about the whole
        screen only for a program that draws nothing outside a bracket, and
        that is a property of the program, not of the sequence. So two more
        counts sit beside `synchronized_updates`, which on its own is the
        weakest of the three and must never end a wait by itself:

        * `unbracketed_paints` is how much the program has drawn while no
          bracket was open. A close is proof of the screen only when this has
          not moved since the wait began — otherwise the screen carries
          painting the program never said anything about, and an empty bracket
          flushed alongside it ends the wait on a repaint that had not
          started. That was reachable: paint, then `h` `l`, and the frame came
          back stamped with the strongest label the harness has.
        * `synchronized_faults` counts 2026 sequences that a program
          bracketing its repaints cannot emit: a close with nothing open, and
          an open inside an open. They are ignored, as a real terminal ignores
          them, but they are not *nothing*: the likeliest source is bracket
          bytes arriving as CONTENT — a log line, a fixture, prose quoting the
          sequence, rendered into a pane — and those are byte for byte the
          program's own sequence. Once a stream carries them, no 2026 claim in
          it can be read, and neither can the text plane, since the emulator
          has been taking that content for control all along.

        A bracket that encloses no painting is *not* by itself a fault, and
        deliberately so: a repaint that found nothing to change still opened
        and closed one, and it is telling the truth — there was nothing to
        draw and the screen is whole. Nothing distinguishes it from a program
        about to draw unbracketed, which is why the discipline, not the
        emptiness, is what gets checked.

        WHICH LEAVES THE QUESTION OF WHAT AN EMPTY BRACKET IS WORTH, and
        `screen_vouched` is the answer. A close whose bracket enclosed
        painting is the program saying "the screen I have just drawn is
        whole": it covers the screen. A close whose bracket enclosed nothing
        says "a repaint that drew nothing is over", which is worth exactly the
        proof that was already there — so it carries `screen_vouched` forward
        in both directions rather than granting it. Anything that touches the
        screen with no bracket open withdraws it.

        That is what stopped the empty bracket from certifying content it did
        not enclose. A program that had painted its whole screen outside every
        bracket, and answered a keystroke with `ESC[?2026h ESC[?2026l`, was
        handed `synchronised` for a screen no statement of its own covered —
        the very screen its own exit reports as `abandoned`.
        """
        if on:
            if self.synchronized_update:
                self.synchronized_faults += 1
                return
            self.synchronized_update = True
            self.synchronized_opens += 1
            self._bracket_paint_mark = self.bracketed_paints
            return
        if not self.synchronized_update:
            self.synchronized_faults += 1
            return
        self.synchronized_update = False
        self.synchronized_updates += 1
        if self.bracketed_paints > self._bracket_paint_mark:
            self.screen_vouched = True

    def _painted(self):
        """Record that something was drawn — called from every operation that
        changes what is on the screen, and from nowhere else.

        Cursor motion, attribute changes and mode switches deliberately do not
        call it: they change how the *next* thing is drawn, not the screen, so
        a bracket containing only those enclosed no repaint and painting only
        those outside one costs a program nothing.
        """
        if self.synchronized_update:
            self.bracketed_paints += 1
            return
        self.unbracketed_paints += 1
        self.screen_vouched = False

    def _switch_screen(self, to_alt, save_cursor):
        if to_alt:
            if self.in_alt_screen:
                return
            self._painted()
            self._saved_screen = (
                [row[:] for row in self._grid],
                [row[:] for row in self._attr_grid],
                self._overflow[:],
                self.cursor_row,
                self.cursor_col,
            )
            self._grid = [self._blank_row() for _ in range(self.rows)]
            self._attr_grid = [self._blank_attr_row() for _ in range(self.rows)]
            self._overflow = [_NO_OVERFLOW] * self.rows
            self.in_alt_screen = True
        else:
            if not self.in_alt_screen:
                return
            self._painted()
            self.in_alt_screen = False
            # What the program drew on the alternate screen is about to be
            # replaced by the primary screen it was given. A terminal throws
            # that away; a judge needs it, because a TUI's exit path — endwin
            # on SIGTERM, endwin from curses.wrapper on a crash — runs
            # `?1049l` and would otherwise leave a blank frame as the only
            # evidence of what the program had on screen when it died.
            self._retired_alt = (
                [row[:] for row in self._grid],
                [row[:] for row in self._attr_grid],
                self._overflow[:],
                self.rows,
                self.cols,
            )
            if self._saved_screen is None:
                return
            grid, attr_grid, overflow, row, col = self._fit_saved_screen(
                self._saved_screen, self.rows, self.cols
            )
            self._saved_screen = None
            self._grid = grid
            self._attr_grid = attr_grid
            self._overflow = overflow
            if save_cursor:
                self.cursor_row = self._clamp_row(row)
                self.cursor_col = self._clamp_col(col)

    # -- cursor / scrolling ----------------------------------------------

    def _clamp_repeat(self, count):
        """A repeat count reduced to one that leaves the identical screen.

        Once every cell holds the same character, each further `cols` repeats
        put the screen and the cursor back exactly where they were: the rows
        that scroll away are indistinguishable from the rows that replace
        them. Writing the screen twice over reaches that state from any
        starting cursor position, so anything past it is taken modulo the
        width — the clamp is an equivalence, not an approximation.
        """
        settled = 2 * self.rows * self.cols
        if count <= settled or self.cols <= 0:
            return count
        return settled + (count - settled) % self.cols

    def _clamp_row(self, row):
        return max(0, min(self.rows - 1, row))

    def _clamp_col(self, col):
        return max(0, min(self.cols - 1, col))

    def _move_up(self, count):
        limit = self.scroll_top if self.cursor_row >= self.scroll_top else 0
        self.cursor_row = max(limit, self.cursor_row - count)
        self._pending_wrap = False

    def _move_down(self, count):
        limit = (
            self.scroll_bottom
            if self.cursor_row <= self.scroll_bottom
            else self.rows - 1
        )
        self.cursor_row = min(limit, self.cursor_row + count)
        self._pending_wrap = False

    def _save_cursor(self):
        self._saved_cursor = (self.cursor_row, self.cursor_col)

    def _restore_cursor(self):
        if self._saved_cursor:
            self.cursor_row = self._clamp_row(self._saved_cursor[0])
            self.cursor_col = self._clamp_col(self._saved_cursor[1])
        else:
            self.cursor_row = self.cursor_col = 0
        self._pending_wrap = False

    def _index(self):
        if self.cursor_row == self.scroll_bottom:
            self._scroll_up(1)
        elif self.cursor_row < self.rows - 1:
            self.cursor_row += 1

    def _reverse_index(self):
        if self.cursor_row == self.scroll_top:
            self._scroll_down(1)
        elif self.cursor_row > 0:
            self.cursor_row -= 1

    def _region_height(self):
        return self.scroll_bottom - self.scroll_top + 1

    def _scroll_up(self, count):
        self._painted()
        # Scrolling the region away twice leaves the same blank region as
        # scrolling it away once, so a count past the region height is the
        # same screen — and a count of two hundred million is a hung harness,
        # because feed() runs inside a drain that only checks its deadline
        # between reads.
        for _ in range(min(count, self._region_height())):
            del self._grid[self.scroll_top]
            del self._attr_grid[self.scroll_top]
            del self._overflow[self.scroll_top]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._attr_grid.insert(self.scroll_bottom, self._blank_attr_row())
            self._overflow.insert(self.scroll_bottom, _NO_OVERFLOW)

    def _scroll_down(self, count):
        self._painted()
        for _ in range(min(count, self._region_height())):
            del self._grid[self.scroll_bottom]
            del self._attr_grid[self.scroll_bottom]
            del self._overflow[self.scroll_bottom]
            self._grid.insert(self.scroll_top, self._blank_row())
            self._attr_grid.insert(self.scroll_top, self._blank_attr_row())
            self._overflow.insert(self.scroll_top, _NO_OVERFLOW)

    def _record_wrap(self):
        """The cursor ran off the right margin and the row continued below."""
        self._overflow[self.cursor_row] = RowOverflow(
            continued=self.cols, lost=self._overflow[self.cursor_row].lost
        )
        self._index()
        self.cursor_col = 0

    def _record_margin_loss(self, width=1):
        """Content the right margin swallowed because autowrap was off.

        Counted in cells, so a wide character that cannot fit costs two: the
        row is that much wider than the screen ever showed.
        """
        record = self._overflow[self.cursor_row]
        self._overflow[self.cursor_row] = RowOverflow(
            continued=record.continued, lost=record.lost + width
        )

    # -- erasing / editing -----------------------------------------------

    def _blank_cell(self, row, col):
        self._grid[row][col] = _BLANK
        self._attr_grid[row][col] = self._blank_attrs()

    def _break_wide_pair(self, row_index, col):
        """A double-width glyph is one character in two columns.

        Writing over either half destroys the glyph, so a terminal blanks both
        — xterm does, and every emulator that keeps a grid has to. Editing the
        halves independently left a frame showing a glyph the terminal was not
        showing, and, worse, a row whose *display* width no longer matched the
        screen: `assert_within_width()` passed a row eleven columns wide on a
        ten-column screen, because the orphaned placeholder still made the row
        the right number of cells. Every column a `Frame` reports for such a
        row is off by one from the screen as well.

        This is the O(1) half of the repair, for the one cell a write lands
        on; `_heal_wide_row` is the version for an edit that shifts a row.
        """
        if not 0 <= col < self.cols:
            return
        row = self._grid[row_index]
        if row[col] == _WIDE_PLACEHOLDER:
            if col:
                self._blank_cell(row_index, col - 1)
        elif col + 1 < self.cols and row[col + 1] == _WIDE_PLACEHOLDER:
            self._blank_cell(row_index, col + 1)

    def _heal_wide_row(self, row_index):
        """Blank every half of a double-width cell whose other half is gone.

        An edit that shifts a row — ICH, DCH, insert mode — or a resize that
        cuts a column off the right edge can separate a pair anywhere in the
        row, so this is the version that walks it.
        """
        row = self._grid[row_index]
        for col in range(self.cols):
            cell = row[col]
            if cell == _WIDE_PLACEHOLDER:
                if col == 0 or _char_width(row[col - 1]) != 2:
                    self._blank_cell(row_index, col)
            elif _char_width(cell) == 2 and (
                    col + 1 >= self.cols or row[col + 1] != _WIDE_PLACEHOLDER):
                self._blank_cell(row_index, col)

    def _clear_row(self, row, start=0, end=None):
        self._painted()
        end = self.cols if end is None else end
        blank_attrs = self._blank_attrs()
        # an erase that begins or ends inside a double-width cell takes the
        # other half of it with it
        self._break_wide_pair(row, start)
        if end - 1 > start:
            self._break_wide_pair(row, end - 1)
        for col in range(start, end):
            self._grid[row][col] = _BLANK
            self._attr_grid[row][col] = blank_attrs
        if start == 0 and end == self.cols:
            self._overflow[row] = _NO_OVERFLOW

    def _erase_display(self, mode):
        if mode == 0:
            self._clear_row(self.cursor_row, self.cursor_col)
            self._overflow[self.cursor_row] = _NO_OVERFLOW
            for row in range(self.cursor_row + 1, self.rows):
                self._clear_row(row)
        elif mode == 1:
            self._clear_row(self.cursor_row, 0, self.cursor_col + 1)
            for row in range(0, self.cursor_row):
                self._clear_row(row)
        elif mode == 2:
            for row in range(self.rows):
                self._clear_row(row)
        else:
            # ED 3 erases the *scrollback*, which this screen does not model,
            # and any other parameter is not an erase at all. Blanking the
            # visible screen for either was a repaint nobody asked for: a TUI
            # that clears its scrollback on start-up lost the screen it had
            # just drawn.
            return
        self._pending_wrap = False

    def _erase_line(self, mode):
        if mode == 0:
            self._clear_row(self.cursor_row, self.cursor_col)
            self._overflow[self.cursor_row] = _NO_OVERFLOW
        elif mode == 1:
            self._clear_row(self.cursor_row, 0, self.cursor_col + 1)
        else:
            self._clear_row(self.cursor_row)
        self._pending_wrap = False

    def _in_region(self):
        return self.scroll_top <= self.cursor_row <= self.scroll_bottom

    def _insert_lines(self, count):
        if not self._in_region():
            return
        self._painted()
        # past the foot of the region every further insert only pushes a blank
        # row out, so the screen stops changing: clamping is equivalent
        for _ in range(min(count, self.scroll_bottom - self.cursor_row + 1)):
            del self._grid[self.scroll_bottom]
            del self._attr_grid[self.scroll_bottom]
            del self._overflow[self.scroll_bottom]
            self._grid.insert(self.cursor_row, self._blank_row())
            self._attr_grid.insert(self.cursor_row, self._blank_attr_row())
            self._overflow.insert(self.cursor_row, _NO_OVERFLOW)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_lines(self, count):
        if not self._in_region():
            return
        self._painted()
        for _ in range(min(count, self.scroll_bottom - self.cursor_row + 1)):
            del self._grid[self.cursor_row]
            del self._attr_grid[self.cursor_row]
            del self._overflow[self.cursor_row]
            self._grid.insert(self.scroll_bottom, self._blank_row())
            self._attr_grid.insert(self.scroll_bottom, self._blank_attr_row())
            self._overflow.insert(self.scroll_bottom, _NO_OVERFLOW)
        self.cursor_col = 0
        self._pending_wrap = False

    def _delete_chars(self, count):
        self._painted()
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        col = self.cursor_col
        keep = self.cols - col
        # no clamp needed and none pretended: the slice below is empty once
        # `count` reaches the end of the row, and nothing here is allocated
        # per count the way `_insert_chars` would be
        row[col:] = (row[col + count:] + [_BLANK] * self.cols)[:keep]
        attr_row[col:] = (
            attr_row[col + count:] + [self._blank_attrs()] * self.cols
        )[:keep]
        self._heal_wide_row(self.cursor_row)
        self._pending_wrap = False

    def _insert_chars(self, count):
        self._painted()
        row = self._grid[self.cursor_row]
        attr_row = self._attr_grid[self.cursor_row]
        col = self.cursor_col
        keep = self.cols - col
        # inserting more blanks than the row can hold blanks the rest of the
        # row and nothing else — and building the list first would allocate
        # once per count
        count = min(count, keep)
        row[col:] = ([_BLANK] * count + row[col:])[:keep]
        attr_row[col:] = ([self._blank_attrs()] * count + attr_row[col:])[:keep]
        self._heal_wide_row(self.cursor_row)
        self._pending_wrap = False

    def _erase_chars(self, count):
        end = min(self.cols, self.cursor_col + count)
        self._clear_row(self.cursor_row, self.cursor_col, end)
        self._pending_wrap = False


# ---------------------------------------------------------------------------
# key encoding
# ---------------------------------------------------------------------------

_NAMED_KEYS = {
    "enter": "\r",
    "return": "\r",
    "cr": "\r",
    "newline": "\n",
    "tab": "\t",
    "backtab": "\x1b[Z",
    "shift-tab": "\x1b[Z",
    "esc": "\x1b",
    "escape": "\x1b",
    "space": " ",
    "backspace": "\x7f",
    "delete": "\x1b[3~",
    "del": "\x1b[3~",
    "insert": "\x1b[2~",
    "pageup": "\x1b[5~",
    "pgup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "pgdn": "\x1b[6~",
    "lt": "<",
    "gt": ">",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}

# normal (DECCKM off) and application (DECCKM on) forms
_CURSOR_KEYS = {
    "up": ("\x1b[A", "\x1bOA"),
    "down": ("\x1b[B", "\x1bOB"),
    "right": ("\x1b[C", "\x1bOC"),
    "left": ("\x1b[D", "\x1bOD"),
    "home": ("\x1b[H", "\x1bOH"),
    "end": ("\x1b[F", "\x1bOF"),
}

_TOKEN_RE = re.compile(r"<([^<>]+)>")


def _named_key(name: str, application_cursor: bool) -> str:
    key = name.strip().lower()
    if key in _CURSOR_KEYS:
        return _CURSOR_KEYS[key][1 if application_cursor else 0]
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if key.startswith("c-") and len(key) == 3:
        return chr(ord(key[2].upper()) & 0x1F)
    if key.startswith("^") and len(key) == 2:
        return chr(ord(key[1].upper()) & 0x1F)
    raise ValueError(
        "unknown key %r — known names: %s, <C-x>, <lt>"
        % (name, ", ".join(sorted(set(_NAMED_KEYS) | set(_CURSOR_KEYS))))
    )


def encode_keys(keys, application_cursor: bool = False) -> str:
    """Turn a key script into the characters a terminal would send.

    Ordinary characters pass through; `<Name>` becomes that key's sequence. See
    the module docstring for the list of names.
    """
    if not isinstance(keys, str):
        return "".join(encode_keys(part, application_cursor) for part in keys)
    out = []
    pos = 0
    for match in _TOKEN_RE.finditer(keys):
        out.append(keys[pos:match.start()])
        out.append(_named_key(match.group(1), application_cursor))
        pos = match.end()
    out.append(keys[pos:])
    return "".join(out)


# ---------------------------------------------------------------------------
# TerminalSession
# ---------------------------------------------------------------------------


def _set_winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


# How many bytes are still queued for the program to read. Absent on a platform
# that does not define it, in which case the delivery barrier has no signal and
# says so rather than guessing.
_FIONREAD = getattr(termios, "FIONREAD", None)

# How long the delivery barrier waits between looks at that queue. It bounds
# how late the barrier notices the keys were taken, and so how much of the
# program's answer it can swallow before the response wait gets a chance at it.
_DELIVERY_POLL = 0.002


class TerminalSession:
    """A program running under a pty of a fixed size, with a screen attached.

    Usable as a context manager; `close()` is always safe to call twice.
    """

    def __init__(
        self,
        argv,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        env=None,
        cwd=None,
        term: str = DEFAULT_TERM,
        settle: float = 0.25,
        idle: float = 0.08,
        timeout: float = 5.0,
        redraw: float = 0.75,
        paint: float = 0.2,
        escdelay: str = "25",
    ):
        self.argv = list(argv)
        self.rows = rows
        self.cols = cols
        self.cwd = str(cwd) if cwd else None
        self.settle = settle
        self.idle = idle
        self.timeout = timeout
        self.redraw = redraw
        # How long a program has to have stopped writing before a frame counts
        # as a screen it finished: a pause shorter than this is part of the
        # same repaint. Longer than `idle` on purpose — `idle` says a burst has
        # settled, `paint` says the screen is done.
        self.paint = paint
        self.screen = Screen(rows, cols)
        self.pid = None
        self.master_fd = None
        self.exit_code = None
        self.initial_attrs = None
        self._slave_fd = None
        self._closed = False

        environment = dict(os.environ if env is None else env)
        # ncurses prefers COLUMNS/LINES over the pty size; they would defeat the
        # explicit size this harness exists to guarantee.
        environment.pop("COLUMNS", None)
        environment.pop("LINES", None)
        if term is not None:
            environment["TERM"] = term
        elif "TERM" not in environment:
            environment["TERM"] = DEFAULT_TERM
        if escdelay is not None:
            environment.setdefault("ESCDELAY", escdelay)
        self.env = environment

    # -- lifecycle -------------------------------------------------------

    def start(self) -> Frame:
        """Fork the program under a pty and return the first painted frame."""
        if self.pid is not None:
            raise RuntimeError("session already started")
        master_fd, slave_fd = pty.openpty()
        _set_winsize(slave_fd, self.rows, self.cols)
        try:
            self.initial_attrs = termios.tcgetattr(master_fd)
        except termios.error:  # pragma: no cover - platform fallback
            self.initial_attrs = termios.tcgetattr(slave_fd)
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child process
            try:
                os.close(master_fd)
                os.setsid()
                try:
                    fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
                except OSError:
                    pass
                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)
                if slave_fd > 2:
                    os.close(slave_fd)
                if self.cwd:
                    os.chdir(self.cwd)
                os.execvpe(self.argv[0], self.argv, self.env)
            except BaseException:
                os._exit(127)
        self.pid = pid
        self.master_fd = master_fd
        # The slave stays open in the parent so that termios can be inspected
        # after the child exits, and so reads never differ between macOS (EOF)
        # and Linux (EIO) when the child goes away.
        self._slave_fd = slave_fd
        os.set_blocking(master_fd, False)
        self._drain(wait_for_first=True)
        return self.frame()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except AssertionError:
            if exc_type is not None:
                # The body already carries a finding. Replacing it with a note
                # about the program's escape sequences hides the thing the
                # test was written to catch.
                return False
            raise
        return False

    @property
    def is_running(self) -> bool:
        if self.pid is None or self.exit_code is not None:
            return False
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            self.exit_code = -1
            return False
        if pid == 0:
            return True
        self.exit_code = self._status_to_code(status)
        return False

    @staticmethod
    def _status_to_code(status):
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    def wait(self, timeout: float = None):
        """Wait for the program to exit; return its exit code."""
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            self._drain(settle=0.02, idle=0.02, timeout=remaining)
            if not self.is_running:
                self._drain(settle=0.05, idle=0.05, timeout=_EXIT_DRAIN)
                return self.exit_code
        raise AssertionError(
            "program %r did not exit within %.1fs\n%s"
            % (self.argv, timeout, self.frame().text)
        )

    def _wait_exit(self, timeout: float) -> bool:
        """Wait for the program to go away, reading its output all the while.

        The reading matters: a curses program's exit path writes its endwin
        cleanup to the terminal, and blocks there for ever if nobody drains it.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_running:
                return True
            if self.master_fd is None:
                time.sleep(0.01)
            else:
                self._drain(settle=0.02, idle=0.02, timeout=0.05)
        return not self.is_running

    def close(self, timeout: float = 2.0):
        """Stop the program if it is still running and release the pty."""
        if self._closed:
            return self.exit_code
        try:
            if self.pid is not None and self.is_running:
                self.signal(signal.SIGTERM)
                if not self._wait_exit(timeout / 2):
                    self.signal(signal.SIGKILL)
                    self._wait_exit(timeout / 2)
        finally:
            self._closed = True
            for fd in (self.master_fd, self._slave_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self.master_fd = None
            self._slave_fd = None
        # macOS keeps a killed session leader in an exiting state until the pty
        # is closed, so the last reap happens after the fds are gone.
        if self.pid is not None and self.exit_code is None:
            self._wait_exit(timeout)
        self._refuse_unbalanced_brackets()
        return self.exit_code

    def _refuse_unbalanced_brackets(self):
        """Fail the session if the program's DEC 2026 brackets did not balance.

        This is the backstop for the one shape the wait cannot catch in time.
        A close arriving as *content* — a pane rendering a log line, a fixture
        or a baton that quotes the sequence — is byte for byte the program's
        own close, so at the instant it arrives there is nothing to read but
        its bytes, and a frame goes back stamped `synchronised`. The
        program's real close then follows with nothing open, and that is
        observable: this stream's brackets are not a program bracketing
        repaints, and no 2026 claim in it, including ones already handed over,
        means what it appeared to mean.

        A frame already returned cannot be un-returned. The run can: the
        session refuses to end, so nothing captured from this program is
        quietly kept as evidence. Every frame it hands over after the
        imbalance shows says `unsound` on its face as well.
        """
        faults = self.screen.synchronized_faults
        if not faults:
            return
        raise AssertionError(
            self.frame()._message(
                "the program's DEC 2026 brackets did not balance: %d sequence%s"
                " that a program bracketing its own repaints cannot send (a"
                " close with nothing open, or an open inside an open). The"
                " likeliest source is bracket bytes reaching the terminal as"
                " CONTENT — a log line, a fixture, prose quoting the sequence,"
                " rendered into a pane — and those are byte for byte the"
                " program's own sequence. So no frame from this session can be"
                " read as proof that a repaint had finished, and its text plane"
                " is suspect too: the emulator has been taking that content for"
                " control. Escape what the program renders."
                % (faults, "" if faults == 1 else "s")
            )
        )

    def termios_attrs(self):
        """Current terminal attributes, for restore checks after exit.

        Read from the master side: macOS revokes the slave fd once the session
        leader exits, which is exactly when a quit-restores-the-terminal check
        wants to look.
        """
        for fd in (self.master_fd, self._slave_fd):
            if fd is None:
                continue
            try:
                return termios.tcgetattr(fd)
            except termios.error:
                continue
        raise RuntimeError("no usable pty fd — session is closed")

    # -- capture ---------------------------------------------------------

    def frame(self, label: str = None, paint_end: str = None) -> Frame:
        """The current screen, without waiting for more output."""
        return self.screen.frame(
            label=label or " ".join(self.argv[-1:]), paint_end=paint_end
        )

    def last_alt_frame(self, label: str = None):
        """What the program had on the alternate screen when it gave it back.

        A curses program's exit path — endwin on SIGTERM, endwin from
        `curses.wrapper` on a crash — sends `?1049l`, which puts the primary
        screen back. `frame()` then shows the shell line, or nothing at all,
        and the screen the program died holding is gone. This returns that
        screen, or None if the program never took the alternate screen.
        """
        return self.screen.last_alt_frame(label)

    def read(self, settle: float = None, timeout: float = None) -> Frame:
        """Drain pending output and return the resulting frame."""
        self._drain(settle=settle, timeout=timeout)
        return self.frame()

    def send(self, keys, settle: float = None, expect: str = None,
             timeout: float = None, regex: bool = False,
             quiet: float = None) -> Frame:
        """Send a key script, wait for the program to act on it, return the frame.

        The returned frame is the screen *after* the keystroke, not before it:

        1. the keys are written, then waited out of the pty's input queue, which
           is how the program reading them becomes an observation rather than an
           assumption — see `_await_delivery`;
        2. the program's answer is waited for. `expect` names text the repaint
           must show and is the strongest form available — the wait ends when
           it appears *and* the repaint has ended, and it fails loudly with the
           frame if the text never appears. Without `expect` the wait ends at
           the first byte the program writes and the repaint settling after it,
           bounded by `redraw` for a keystroke that draws nothing at all.

        What `expect` does *not* claim, and the reason `Frame.paint_end`
        exists: unless the program brackets its repaint in DEC 2026 or exits,
        "it stopped writing for `paint` seconds" is the end of the wait, and a
        program that pauses longer than that inside one repaint is quiet and
        half-painted at the same instant. `quiet=` raises that window for one
        call; `frame.assert_finished()` refuses a frame that was not proved.

        `settle` overrides the silent bound for one call; `timeout` bounds both
        the delivery barrier and `expect`.
        """
        text = encode_keys(keys, self.screen.application_cursor_keys)
        return self.send_bytes(
            text.encode("utf-8"), settle=settle, expect=expect, timeout=timeout,
            regex=regex, quiet=quiet,
        )

    def send_bytes(self, data, settle: float = None, expect: str = None,
                   timeout: float = None, regex: bool = False,
                   quiet: float = None) -> Frame:
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        if isinstance(data, str):
            data = data.encode("utf-8")
        try:
            os.write(self.master_fd, data)
        except OSError as error:
            # Every other failure path in this module hands over the screen;
            # a write that failed because the program died mid-test is
            # unreadable without it.
            raise AssertionError(
                self.frame()._message(
                    "could not write %r to the program: %s%s"
                    % (
                        data,
                        error,
                        "" if self.is_running else " (the program has exited)",
                    )
                )
            ) from error
        self._await_delivery(data, timeout=timeout)
        if expect is not None:
            # `wait_for` takes its DEC 2026 baseline here, *after* the delivery
            # barrier: a bracket the program closed while the keys were still
            # queued was written before it read them, so it is not this
            # repaint and must not end the wait for it.
            return self.wait_for(expect, timeout=timeout, regex=regex,
                                 quiet=quiet)
        window = self.redraw if settle is None else settle
        self._await_response(window)
        return self.frame()

    def _input_pending(self):
        """Bytes written to the program that it has not read yet, or None.

        Read from the *slave* fd: its input queue is the one the program reads
        from, and it is the same on macOS and Linux. None means there is no
        signal to be had — the pty is gone, the platform has no `FIONREAD`, or
        the line discipline cannot answer (BSD counts only complete lines in
        canonical mode, so a half-typed line reads as zero). A missing signal
        never fails a test; it only means this barrier does not fire.
        """
        fd = self._slave_fd
        if fd is None or _FIONREAD is None:
            return None
        try:
            return struct.unpack("i", fcntl.ioctl(fd, _FIONREAD, b"\0" * 4))[0]
        except (OSError, ValueError):
            return None

    def _await_delivery(self, sent, timeout: float = None) -> None:
        """Wait until the program has taken `sent` out of the pty input queue.

        This is the one positive signal a terminal can read without the
        program's cooperation, and it rules out the whole class of frames
        captured before the program ever saw the key. A program that is alive
        and still has not read after `timeout` raises: a TUI that has stopped
        reading its input is a finding, not a frame.

        Delivery says nothing about the answer, and this barrier deliberately
        reports nothing about it. What it does do is clear the pty of output
        that *cannot* be the answer: everything readable while the keys are
        still queued was written before the program took them. Consuming it
        here keeps a program that is slow to read from blocking on a full
        output buffer, and keeps the response wait from mistaking it for the
        repaint. The instant the queue is empty the pty is left alone, so
        whatever the program writes next is still there for `_await_response`
        to find — which is what keeps a prompt program cheap without a flag
        guessing at causality.
        """
        timeout = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + timeout
        while True:
            pending = self._input_pending()
            if not pending:
                return
            if not self.is_running:
                return
            now = time.monotonic()
            if now >= deadline:
                raise AssertionError(
                    self.frame()._message(
                        "the program has not read the %d byte(s) sent to it (%r) "
                        "within %.1fs — it is not reading its input"
                        % (pending, sent, timeout)
                    )
                )
            self._consume_while_queued(min(_DELIVERY_POLL, deadline - now))

    def _consume_while_queued(self, budget: float) -> bool:
        """Read output that the program wrote before it read the keys.

        The queue is checked again immediately before the read, because the
        program may have taken the keys while this call sat in `select`: bytes
        that become readable once the queue is empty are left in the pty rather
        than swallowed, since they may be the answer and the response wait is
        where that is decided.

        The ordering is what makes the exclusion sound rather than a guess. If
        the input queue is non-empty at the moment output becomes readable, the
        program has not taken the keys yet, so that output was written before
        the read and cannot be a response to it.
        """
        if self.master_fd is None:
            return False
        try:
            ready, _, _ = select.select([self.master_fd], [], [], max(budget, 0.0))
        except (OSError, ValueError, TypeError):
            return False
        if not ready or not self._input_pending():
            return False
        return self._read_ready()

    def _read_once(self, budget: float) -> bool:
        """One select-and-read of whatever the program has written."""
        if self.master_fd is None:
            return False
        try:
            ready, _, _ = select.select([self.master_fd], [], [], max(budget, 0.0))
        except (OSError, ValueError, TypeError):
            return False
        if not ready:
            return False
        return self._read_ready()

    def _read_ready(self) -> bool:
        """Take what is readable on the master and feed it to the screen."""
        try:
            data = os.read(self.master_fd, 65536)
        except BlockingIOError:
            return False
        except OSError:
            return False
        if not data:
            return False
        self.screen.feed(data)
        return True

    def _await_response(self, window: float) -> bool:
        """Wait for the program's answer to something it has just been sent.

        Returns as soon as output arrives and then settles, so a program that
        answers at once pays only its own latency — this is a bounded positive
        wait, not a sleep. `window` bounds the silent case: a keystroke that
        legitimately draws nothing must not cost the full timeout. Returns
        whether anything was drawn.
        """
        slice_ = max(self.idle, 0.05)
        deadline = time.monotonic() + window
        while True:
            # The drain's own default cap is the SESSION timeout, and a
            # program that never stops writing — a clock, a progress tick, a
            # log tail — never gives it the idle gap it returns on. So this
            # wait ran to `self.timeout` however small a `window` the caller
            # asked for: a `send(settle=0.2)` against a chatty child took ten
            # seconds. A wait may not outlast its own budget.
            if self._drain(settle=slice_, idle=self.idle,
                           timeout=max(deadline - time.monotonic(), 0.0)):
                return True
            if not self.is_running:
                # Nothing more is coming; take whatever the exit path wrote.
                self._drain(settle=0.02, idle=0.02, timeout=_EXIT_DRAIN)
                return False
            if time.monotonic() >= deadline:
                return False

    def _paint_baseline(self):
        """What a wait for the end of a repaint measures its 2026 evidence
        against: brackets already opened, painting already done inside one,
        and painting already done outside any bracket.

        Both are counts rather than flags so that a baseline taken at the
        right moment — after the delivery barrier — cannot be satisfied by
        something the program did before it read the keys.

        `synchronized_faults` is deliberately NOT baselined. A fault is not an
        event in one repaint that a later repaint can be clear of: it is the
        discovery that this program puts bracket bytes on the wire as content,
        and that is true of every frame it has drawn and will draw. So it
        applies to the whole session, in both directions from where it was
        found. Neither is `screen_vouched`, which is not an event at all but
        the state of the screen right now.

        `synchronized_opens` and `bracketed_paints` are here so that a bracket
        which was ALREADY OPEN when the wait began cannot end it on its own.
        Its close is a statement about a repaint that started before the
        keystroke existed, and closing it the instant the keys were read
        handed back the pre-keystroke screen stamped `synchronised`. Between
        them these two say which: a bracket opened since the baseline is this
        wait's repaint whatever it drew, and one that straddles the baseline
        speaks for this wait only where it enclosed painting done since.
        """
        return (self.screen.unbracketed_paints,
                self.screen.synchronized_opens,
                self.screen.bracketed_paints)

    def _exit_ending(self):
        """What the program's exit is worth as a statement about the screen.

        An exit proves one thing: nothing more is coming. That is real
        information — it is why exiting was read as the end of a repaint — and
        it is not the question. "The program has stopped writing" is equally
        true of a child that finished its repaint and of one killed halfway
        through it, and no observation a terminal can make separates them. The
        only thing that can is what the program itself said while it painted.

        So an exit is proof only for a program that never painted a glyph
        outside a DEC 2026 bracket it closed. Then the screen is bracketed
        repaints and nothing else, every one of them vouched for, and the exit
        adds the last missing piece: no further repaint will start. Four
        readings, weakest first:

        * `unsound` — the brackets did not balance somewhere in this session,
          so nothing the program appeared to say about its painting can be
          read, and neither can its text plane. The exit does not repair that.
        * `torn` — it died INSIDE a bracket it had opened. It said "one whole
          frame begins" and never said it ended: the one screen a terminal
          knows is half a repaint.
        * `abandoned` — it is gone, no bracket open, but it painted outside
          one. Whether the last thing it drew was a finished screen or the
          first half of one is exactly what cannot be seen. Not proof, and
          this is the reading the exit path used to skip: it was `torn` or
          proof, so the programs that could prove NOTHING — the ones that
          never bracket at all — were the ones handed proof.
        * `exited` — gone, no bracket open, and it never painted outside one.
          Proof.

        `unbracketed_paints` is read for the whole session here, not from the
        wait's baseline, and that difference is deliberate. A closed bracket
        is an event: it vouches for one repaint, so what matters is painting
        since the wait began. An exit is not an event about one repaint — it
        is the end of the program, and the screen it leaves is everything the
        program ever drew. A glyph painted outside a bracket ten repaints ago
        is still on that screen and still unvouched for. So this asks a
        property of the program, the way `synchronized_faults` does.
        """
        if self.screen.synchronized_faults:
            return PAINT_UNSOUND
        if self.screen.synchronized_update:
            return PAINT_TORN
        if self.screen.unbracketed_paints:
            return PAINT_ABANDONED
        if not self.screen.screen_vouched:
            # Nothing the program did — this is the harness's own `resize()`
            # having deleted cells the program had drawn. The program cannot
            # answer for a screen it did not leave, and it has exited, so it
            # never will. `abandoned` is what that is: gone, and nothing it
            # ever said covers what is on the screen.
            return PAINT_ABANDONED
        return PAINT_EXITED

    def _await_paint_end(self, window: float = None, quiet: float = None,
                         since=None) -> Frame:
        """Read until the program has finished the screen; return that frame.

        Six endings, and the frame records which one it was, because they do
        not carry the same weight:

        * the program **closed a DEC 2026 bracket** that speaks for this
          wait. Four things have to hold, all of them load-bearing, every one
          of them once missing: the close is clean (it matched an open of the
          program's, `synchronized_updates`); nothing has been painted outside
          a bracket since the `since` baseline (`unbracketed_paints`, or an
          empty bracket flushed beside fresh painting ends a wait on a repaint
          that had not started); the bracket was opened since the baseline, or
          else enclosed painting done since it (`synchronized_opens`,
          `bracketed_paints`, or a bracket open before the keystroke closes on
          the pre-keystroke screen); and the screen is one a statement of the
          program's still covers (`screen_vouched`, or an empty bracket
          certifies a screen no bracket ever enclosed). Then the sequence
          means "this repaint is whole", the wait ends the instant it arrives,
          and the frame is proof;
        * the program **exited** with no repaint open, having never painted
          outside one, onto a screen the harness has not cut cells out of
          since. Nothing further can arrive and everything on the screen was
          vouched for, so that is proof too;
        * the program **exited having painted outside a bracket**:
          `abandoned`. It stopped writing, which a child killed mid-repaint
          also does, and it never said any screen of its was whole. Not proof;
        * the program **exited inside a bracket it had opened**: `torn`. The
          screen is certainly half a repaint, and the program never said
          otherwise. It is handed back as evidence of exactly that, and it is
          not proof;
        * the program's brackets **did not balance**: `unsound`. It sent a
          2026 sequence no repaint-bracketing program sends, which is what
          bracket bytes arriving as content look like, so nothing it appeared
          to say can be read. The wait stops there — waiting for the close of
          a bracket that may never have been opened is waiting on nothing —
          and the session refuses to end on it, because a frame handed back
          before the imbalance showed cannot be un-handed;
        * the program **went quiet** for `quiet` (default `paint`, 0.2s). This
          is the strongest signal available without the program's cooperation
          and it is *not* proof: a program that pauses longer than `quiet`
          inside one repaint is quiet and half-painted at the same instant,
          and nothing a terminal can observe separates the two. `quiet=` moves
          the boundary; it does not remove it.

        The sixth outcome is a failure, not a frame: a program still writing
        when `window` (default `redraw`) runs out is *known* to have handed
        over a partial screen, and returning it as though the repaint had
        finished is exactly the false pass this wait exists to prevent. Raise
        `redraw=` for a program that legitimately paints for that long.
        """
        quiet = self.paint if quiet is None else quiet
        window = self.redraw if window is None else window
        loose, opens, enclosed = (
            self._paint_baseline() if since is None else since
        )
        started = time.monotonic()
        deadline = started + window
        last = started
        while True:
            now = time.monotonic()
            unsound = bool(self.screen.synchronized_faults)
            if not self.screen.synchronized_update:
                # No bracket is open, so a bracket opened since the baseline —
                # or painted into since it — has been CLOSED since it: the one
                # thing that clears that flag is a clean close. So the count of
                # closes is not asked for here as well. A leg that asked for it
                # too was carrying a conjunct that could not fail: two sweep
                # mutations weakened it and the suite did not move.
                if (self.screen.unbracketed_paints == loose
                        and (self.screen.synchronized_opens > opens
                             or self.screen.bracketed_paints > enclosed)
                        and self.screen.screen_vouched):
                    return self.frame(
                        paint_end=PAINT_UNSOUND if unsound
                        else PAINT_SYNCHRONISED)
                if now - last >= quiet:
                    return self.frame(
                        paint_end=PAINT_UNSOUND if unsound else PAINT_QUIET)
            elif unsound:
                # Inside a bracket, and the brackets are not readable: the one
                # that is "open" may be one the program never opened, so its
                # close is a sequence with no meaning behind it and waiting
                # out `redraw` for it buys nothing. With no bracket open the
                # quiet path below is short and settles the screen first, so
                # it is left to do that.
                return self.frame(paint_end=PAINT_UNSOUND)
            if not self.is_running:
                # Nothing more can come; take whatever the exit path wrote.
                # That is one real fact and it is not the one a caller wants:
                # see `_exit_ending` for what an exit is allowed to claim.
                self._drain(settle=0.02, idle=0.02, timeout=_EXIT_DRAIN)
                return self.frame(paint_end=self._exit_ending())
            if now >= deadline:
                break
            if self._read_once(min(quiet, deadline - now)):
                last = time.monotonic()
        raise AssertionError(
            self.frame(paint_end=PAINT_UNFINISHED)._message(
                "the program was still writing after %.2fs, so this screen is "
                "part of a repaint it had not finished%s. Raise redraw= past "
                "the time it takes to paint, or have it bracket the repaint in "
                "DEC 2026."
                % (
                    window,
                    " (it never stopped for the %.2fs quiet window)" % quiet,
                )
            )
        )

    def wait_for(self, needle: str, timeout: float = None, regex: bool = False,
                 quiet: float = None) -> Frame:
        """Wait until the screen shows `needle`; return the frame it finished.

        The needle triggers the capture; it is not the capture. A program paints
        a screen in pieces, so the instant the needle's own cells land the rest
        of the screen may still be the previous frame — and text that survives
        the repaint (a pane heading) is on screen before the program has drawn
        anything at all. So once the needle is there the wait continues until
        the program has stopped writing, and the frame returned is the one it
        finished. See `_await_paint_end`.

        "Finished" is the program's word where it gives one and the harness's
        guess where it does not — see `_await_paint_end`, and read
        `frame.paint_end` when it matters which. `quiet=` widens the guess for
        one call.

        The DEC 2026 baseline is taken here, at the top of the wait, so that a
        bracket the program had already closed cannot end it: only an update
        it completes from now on says anything about the repaint being waited
        for. Taking it any later would miss a whole bracketed repaint the
        needle poll had already read in.

        Fails with the last frame in the message if the needle never appears.
        """
        timeout = self.timeout if timeout is None else timeout
        since = self._paint_baseline()
        deadline = time.monotonic() + timeout
        rx = re.compile(needle) if regex else None
        while True:
            frame = self.frame()
            found = frame.search(needle) is not None if rx else frame.contains(needle)
            if found:
                return self._await_paint_end(quiet=quiet, since=since)
            if time.monotonic() >= deadline:
                raise AssertionError(
                    frame._message(
                        "%r never appeared within %.1fs%s"
                        % (
                            needle,
                            timeout,
                            "" if self.is_running else " (program has exited)",
                        )
                    )
                )
            self._drain(settle=0.05, idle=0.05, wait_for_first=False,
                        timeout=max(deadline - time.monotonic(), 0.0))

    def resize(self, rows: int, cols: int, settle: float = None,
               expect: str = None, timeout: float = None,
               regex: bool = False, quiet: float = None) -> Frame:
        """Resize the pty and deliver SIGWINCH, then wait for the redraw.

        A signal leaves nothing in the input queue, so there is no delivery
        barrier to be had here: `expect` — text the program paints at the new
        size — is the only sound signal that the redraw has happened. Without
        it the wait ends at the first byte of the redraw, bounded by `redraw`
        for a program that does not repaint at all.
        """
        if self.master_fd is None:
            raise RuntimeError("session is not running")
        self.rows = rows
        self.cols = cols
        _set_winsize(self.master_fd, rows, cols)
        if self._slave_fd is not None:
            # macOS revokes the slave fd the moment the child session leader
            # exits, so this one fails with EBADF on a session whose program
            # has already gone — the same revocation `termios_attrs()` copes
            # with. The master carries the size either way, and a resize is a
            # thing a judge does to take evidence at another size: it must not
            # turn into an OSError out of the harness.
            try:
                _set_winsize(self._slave_fd, rows, cols)
            except OSError:
                pass
        self.screen.resize(rows, cols)
        self.signal(signal.SIGWINCH)
        if expect is not None:
            return self.wait_for(expect, timeout=timeout, regex=regex,
                                 quiet=quiet)
        self._await_response(self.redraw if settle is None else settle)
        return self.frame()

    def signal(self, sig):
        """Send a signal to the program's process group."""
        if self.pid is None:
            return
        try:
            os.killpg(os.getpgid(self.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                pass

    # -- reading ---------------------------------------------------------

    def _drain(self, settle=None, idle=None, timeout=None, wait_for_first=False):
        """Read output until it goes quiet.

        Returns once no byte has arrived for `idle` seconds. `timeout` caps a
        program that keeps talking; `wait_for_first` keeps waiting up to the cap
        for the very first byte (interpreter start-up).
        """
        if self.master_fd is None:
            return False
        settle = self.settle if settle is None else settle
        idle = self.idle if idle is None else idle
        timeout = self.timeout if timeout is None else timeout
        idle = min(idle, settle)
        started = time.monotonic()
        hard_deadline = started + timeout
        got_data = False
        last = started
        while True:
            now = time.monotonic()
            if now >= hard_deadline:
                return got_data
            if got_data or not wait_for_first:
                if now - last >= (idle if got_data else settle):
                    return got_data
            budget = min(idle, hard_deadline - now)
            try:
                ready, _, _ = select.select([self.master_fd], [], [], max(budget, 0.0))
            except (OSError, ValueError, TypeError):
                return got_data
            if not ready:
                continue
            try:
                data = os.read(self.master_fd, 65536)
            except BlockingIOError:
                continue
            except OSError:
                return got_data
            if not data:
                return got_data
            self.screen.feed(data)
            got_data = True
            last = time.monotonic()


# ---------------------------------------------------------------------------
# one-shot helper
# ---------------------------------------------------------------------------


def run_frames(
    argv,
    keys=(),
    rows: int = DEFAULT_ROWS,
    cols: int = DEFAULT_COLS,
    wait_for: str = None,
    **kwargs,
):
    """Run `argv`, send each key script in `keys`, return every frame.

    `frames[0]` is the first paint; `frames[n]` is the screen after `keys[n-1]`.
    `wait_for` makes the first frame deterministic by waiting for some text the
    program paints on start-up.

    Every step is synchronised the way `TerminalSession.send()` is. A step given
    as `(keys, expect)` waits for `expect` to appear before its frame is taken,
    which is the strongest form available for a transition the program is slow
    to paint — read `frame.paint_end` for what the wait it ended on is worth.
    """
    frames = []
    with TerminalSession(argv, rows=rows, cols=cols, **kwargs) as term:
        if wait_for:
            frames.append(term.wait_for(wait_for))
        else:
            frames.append(term.frame())
        for step in keys:
            if isinstance(step, (tuple, list)):
                script, expect = step
                frames.append(term.send(script, expect=expect))
            else:
                frames.append(term.send(step))
    return frames
