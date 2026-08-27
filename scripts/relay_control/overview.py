"""The four-pane Overview (ACC-TUI-003) and what the four panes say.

The *arrangement* is `tui-skeleton`'s: Active Leg on the left, Legs top-right,
Progress Log bottom-right, Active Runner full width along the bottom, separated
by rules, collapsing to a single column below `chrome.NARROW_COLS`. It comes
from `chrome.overview_frame()`, which hands back a `Pane` per position or `None`
where the terminal was too small for one.

The *contents* are ACC-OVER-001..005, and this module owns them:

* **Active Leg** — the running leg's spec: id, stage name, goal, and the
  `Boundaries` and `Verification` lists (ACC-OVER-001). A judge leg carries
  neither list, and a pane that drew the headings anyway would be claiming the
  leg has boundaries nobody wrote. With no leg running at all the pane states
  the relay's phase and what is being waited on (ACC-OVER-002).
* **Legs** — every leg in plan order, the running one on a highlighted row, a
  `done/total` count in the header (ACC-OVER-003). The window *scrolls* to keep
  the running leg on screen: a relay 27 legs into 36 that drew its first
  fourteen would hide the one row a supervisor is looking for behind `+22 more`.
* **Progress Log** — the newest entries with a relative age column and the range
  the pane actually drew (ACC-OVER-004). An empty log reads `none`, never
  `1-0 of 0`.
* **Active Runner** — `#N`, the leg id, the elapsed time, and the leg's
  verification steps with each step's last known result (ACC-OVER-005).

Three rules here are regression checks, not decoration. Each one shipped once:

1. `1-0 of 0` for an empty log, and its twin `1-N of M` for a range the pane did
   not draw.
2. The Active Leg and Active Runner panes naming different legs. The model
   guarantees they cannot: `model["activeRunner"]` *is* the runner row of
   `model["activeLeg"]` (ACC-DATA-003). Both panes below take what the model
   hands them; neither looks a leg up by id, which is the defect itself.
3. A heading with nothing under it — `Boundaries` for a leg that has none, or a
   heading left as the last row a short pane could fit.

Nothing here reads a file, calls `build()`, or mutates the model; everything is
`model`. See `.relay/skills/pane-conventions.md`.
"""

from . import chrome, navigation
from . import theme as theme_tokens

TITLE = "Overview"

BINDINGS = (
    ("F", "Legs"),
    ("W", "Runners"),
    ("M", "Models"),
    ("C", "Contract"),
    ("Tab", "Next View"),
    ("q", "Quit"),
)


def draw(canvas, model, state):
    panes = chrome.overview_frame(canvas)
    for name, painter in (("active_leg", _draw_active_leg),
                          ("legs", _draw_legs),
                          ("log", _draw_log),
                          ("runner", _draw_runner)):
        pane = panes.get(name)
        if pane is not None:
            painter(pane, model)


# --------------------------------------------------------------------------
# body rows
#
# A pane whose body is several regions — a goal, then Boundaries, then
# Verification — cannot paginate each of them separately: every region would
# need a row to put its own `+N more` in, and the pane a supervisor reads at
# 80x24 has no rows to spare. So a region-shaped pane builds one list of rows
# first, in reading order, and truncates the list once.
# --------------------------------------------------------------------------


def _row(segments, heading=False):
    return {"segments": segments, "heading": heading}


_BLANK = {"segments": (), "heading": False}


def _fit(rows, height):
    """`(shown, hidden)` for a list of body rows.

    Three differences from `chrome.paginate`, all of which exist because these
    rows are prose rather than items:

    * the blank separators are the first thing to go. Whitespace is the
      cheapest row in a pane, and a short pane that spent one on a gap and then
      wrote `+12 more` would have hidden a line to keep a blank.
    * `hidden` counts only rows with something on them, for the same reason:
      `+3 more` promises three lines, so it may not count a separator.
    * a heading is never the last row shown. Cutting between `Boundaries` and
      its first item draws exactly the empty heading ACC-OVER-001 forbids, one
      row further down the pane. A heading is a label for the rows under it, so
      with no room for any of them it gives its row up to the content it was
      labelling rather than being kept and pointing at nothing.
    """
    if height <= 0:
        return [], _weight(rows)
    if len(rows) <= height:
        return list(rows), 0
    dense = [row for row in rows if row["segments"]]
    if len(dense) <= height:
        return dense, 0
    while True:
        shown = dense[:height - 1]              # one row kept for `+N more`
        if not shown or not shown[-1]["heading"]:
            return shown, len(dense) - len(shown)
        dense = [row for row in dense if row is not shown[-1]]


def _weight(rows):
    """How many of `rows` a reader would count as a line."""
    return sum(1 for row in rows if row["segments"])


def _draw_rows(pane, top, rows, height=None):
    """Draw body rows from `top` down, with `+N more` where they ran out."""
    height = pane.body_height - top if height is None else height
    shown, hidden = _fit(rows, height)
    for offset, row in enumerate(shown):
        if row["segments"]:
            pane.segments(top + offset, row["segments"])
    pane.more(hidden, row=top + len(shown))
    return top + len(shown) + (1 if hidden else 0)


# --------------------------------------------------------------------------
# ACC-OVER-001 / -002 — Active Leg
# --------------------------------------------------------------------------


def _draw_active_leg(pane, model):
    leg = model.get("activeLeg")
    if not leg:
        _draw_waiting(pane, model)
        return

    pane.header(leg.get("stage"))
    glyph, attr = pane.theme.status(leg.get("status") or "running")
    head = [(glyph + "  ", attr),
            (leg.get("id") or "(unnamed leg)", theme_tokens.EMPHASIS)]
    kind = leg.get("kind")
    if kind and kind != "impl":
        head.append(("  " + kind, theme_tokens.KIND))
    pane.segments(0, head)
    _draw_rows(pane, 1, _spec_rows(pane, leg))


def _spec_rows(pane, leg):
    """The Active Leg pane's body under the id: the leg's spec, in order.

    `Boundaries` and `Verification` appear only when the leg has them. A judge
    leg carries neither, and a pane that drew the headings for it would be
    inventing two empty lists (ACC-OVER-001).
    """
    width = pane.body_width
    rows = []
    stage_name = leg.get("stageName")
    if stage_name:
        rows.append(_row([("stage  ", theme_tokens.MUTED),
                          (stage_name, theme_tokens.BODY)]))

    goal = leg.get("goal")
    if goal:
        rows.append(_BLANK)
        rows.extend(_row([(line, theme_tokens.BODY)])
                    for line in chrome.wrap(goal, width))
    else:
        # Absent, not blank: `legs.json` recorded no goal for this leg, which
        # is a different thing from a pane that failed to draw one.
        rows.append(_BLANK)
        rows.append(_row([("no goal recorded for this leg",
                           theme_tokens.ABSENT)]))

    bullet = pane.theme.glyph("bullet") + " "
    for heading, items in (("Boundaries", leg.get("boundaries")),
                           ("Verification", leg.get("verification"))):
        if not items:
            continue
        rows.append(_BLANK)
        rows.append(_row([(heading, theme_tokens.PANE_TITLE)], heading=True))
        for item in items:
            lines = chrome.wrap(item, max(1, width - len(bullet)))
            for index, line in enumerate(lines):
                rows.append(_row([
                    (bullet if index == 0 else " " * len(bullet),
                     theme_tokens.MUTED),
                    (line, theme_tokens.BODY),
                ]))
    return rows


def _draw_waiting(pane, model):
    """ACC-OVER-002 — the phase, and what the relay is waiting on.

    Not `pane.empty()`: "nothing is running" is a state of the relay and not an
    empty list, and a supervisor looking at this pane wants to know which state
    it is and what would end it.
    """
    relay = model.get("relay") or {}
    phase = relay.get("phase") or "pending"
    stage = relay.get("currentStage") or {}
    pane.header(stage.get("id"))

    attr = pane.theme.phase(phase)
    pane.segments(0, [(pane.theme.glyph("dot") + "  ", attr),
                      (phase.upper(), attr)])
    rows = [_row([(line, theme_tokens.BODY)])
            for line in chrome.wrap(_waiting_on(model, phase), pane.body_width)]
    tally = _leg_tally(model)
    if tally:
        rows.append(_BLANK)
        rows.append(_row([(tally, theme_tokens.MUTED)]))
    _draw_rows(pane, 1, rows)


def _waiting_on(model, phase):
    """What ends this waiting state, in words, derived from the model."""
    counts = model.get("legCounts") or {}
    total = counts.get("total") or 0
    pending = counts.get("pending") or 0
    if phase == "complete":
        return "every leg has landed — nothing is being waited on"
    if phase == "judging":
        name = ((model.get("relay") or {}).get("currentStage") or {}).get("name")
        return ("waiting on the stage gate for %s" % name if name
                else "waiting on the stage gate")
    if phase == "blocked":
        return "waiting on a human call before another leg can start"
    if not total:
        return "no legs are planned yet"
    if pending:
        return ("waiting for a runner: %d of %d legs are still pending and "
                "none is marked running" % (pending, total))
    return "no leg is marked running"


def _leg_tally(model):
    """`36 legs: 27 done, 8 pending` — the buckets that have something in them."""
    counts = model.get("legCounts") or {}
    total = counts.get("total") or 0
    if not total:
        return None
    words = (("completed", "done"), ("running", "running"),
             ("pending", "pending"), ("failed", "failed"),
             ("cancelled", "cancelled"))
    parts = ["%d %s" % (counts.get(key) or 0, word) for key, word in words
             if counts.get(key)]
    return "%d legs: %s" % (total, ", ".join(parts)) if parts else "%d legs" % total


# --------------------------------------------------------------------------
# ACC-OVER-003 — Legs
# --------------------------------------------------------------------------


def _draw_legs(pane, model):
    counts = model.get("legCounts") or {}
    legs = model.get("legs") or []
    pane.header("%d/%d" % (counts.get("completed", 0), counts.get("total", 0)))
    if not legs:
        pane.empty("no legs planned yet")
        return

    active = next((index for index, leg in enumerate(legs)
                   if leg.get("isActive")), None)
    # `navigation.window()` is `_leg_window()`, moved there whole by
    # `navigation-and-filters` when the Legs, Runners and Contract views needed
    # the same window around a *selected* row. One list, one privileged row,
    # one answer; here the privileged row is the running leg.
    _start, shown, above, below = navigation.window(
        legs, pane.body_height, active)
    row = 0
    if above:
        pane.line(row, "+%d earlier" % above, theme_tokens.MUTED)
        row += 1
    for offset, leg in enumerate(shown):
        _draw_leg_row(pane, row + offset, leg)
    pane.more(below, row=row + len(shown))


def _draw_leg_row(pane, row, leg):
    """One leg: its status glyph, its id, and the highlight if it is running.

    The glyph keeps its own status attribute even on the highlighted row
    (ACC-TUI-006): the highlight says *where the relay is*, the glyph says what
    that leg's state is, and collapsing the two loses the second.
    """
    glyph, attr = pane.theme.status(leg.get("status") or "pending")
    text = leg.get("id") or "(unnamed leg)"
    if leg.get("isActive"):
        # Padded to the pane, so the highlight reads as a row rather than as a
        # word: `theme.SELECTED` is reverse video and stops where its text does.
        width = max(len(text), pane.body_width - len(glyph) - 2)
        pane.segments(row, [(glyph + "  ", attr),
                            (text.ljust(width), theme_tokens.SELECTED)])
    else:
        pane.segments(row, [(glyph + "  ", attr), (text, theme_tokens.BODY)])


# --------------------------------------------------------------------------
# ACC-OVER-004 — Progress Log
# --------------------------------------------------------------------------


def _draw_log(pane, model):
    entries = model.get("log") or []
    if not entries:
        # Never `1-0 of 0`: an empty log says so in words, in both places.
        pane.header("none")
        pane.empty("nothing recorded yet")
        return
    shown, hidden = chrome.paginate(entries, pane.body_height)
    # `1-0 of 12` is the same nonsense as `1-0 of 0`: a range the pane did not
    # draw. Too short to show a single entry says how many there are instead.
    pane.header("1-%d of %d" % (len(shown), len(entries)) if shown
                else "%d entries" % len(entries))
    for row, entry in enumerate(shown):
        # ACC-DATA-005's `age`, humanised. `None` means the model measured no
        # age for this entry, which is not the same as "just now".
        age = chrome.humanise_age(entry.get("age"))
        pane.segments(row, [
            ("%9s  " % (age or "unmeasured")[:9],
             theme_tokens.MUTED if age else theme_tokens.ABSENT),
            (entry.get("m") or "", pane.theme.attention(entry.get("level"))),
        ])
    pane.more(hidden)


# --------------------------------------------------------------------------
# ACC-OVER-005 — Active Runner
# --------------------------------------------------------------------------


def _draw_runner(pane, model):
    runner = model.get("activeRunner")
    if not runner:
        pane.header(None)
        pane.empty("no runner is on a leg right now")
        return

    # Taken from the model, never looked up: `activeRunner` *is* the runner row
    # of `activeLeg` (ACC-DATA-003), so this pane and the Active Leg pane cannot
    # name different legs. Re-deriving either by id is the original defect.
    number = runner.get("n")
    pane.header("#%s" % number if number is not None else None)
    pane.segments(0, [
        ("#%s  " % ("?" if number is None else number), theme_tokens.MUTED),
        (runner.get("leg") or "(unidentified leg)", theme_tokens.EMPHASIS),
    ])
    elapsed = chrome.humanise_duration(runner.get("duration"))
    pane.right(0, elapsed or "unmeasured",
               theme_tokens.MUTED if elapsed else theme_tokens.ABSENT)
    _draw_rows(pane, 1, _step_rows(pane, model, model.get("activeLeg") or {}))


def _step_rows(pane, model, leg):
    """The leg's verification steps, each with its last known result."""
    steps = leg.get("verification") or []
    if not steps:
        # A judge leg has no verification list. Say so in words rather than
        # draw a `Verification` heading over nothing (ACC-OVER-001's rule, one
        # pane over).
        return [_row([("no verification steps recorded for this leg",
                       theme_tokens.ABSENT)])]

    results = _judged_results(model)
    rows = [_row([("Verification", theme_tokens.PANE_TITLE)], heading=True)]
    for step in steps:
        rows.append(_step_row(pane, step, _step_result(step, results)))
    return rows


def _step_row(pane, step, result):
    """One step, with its result right-aligned on the same row."""
    label = result or "no result recorded"
    token = theme_tokens.MUTED if result else theme_tokens.ABSENT
    # The pair, never one half: a step's glyph and its colour are one fact
    # (ACC-TUI-006). A step with no result gets neither a status glyph nor a
    # status colour — `theme.ABSENT` is how "not measured" is spelled.
    glyph, attr = (pane.theme.status(result) if result
                   else (pane.theme.glyph("bullet"), theme_tokens.ABSENT))
    lead = glyph + " "
    text = chrome.clip(step, max(1, pane.body_width - len(lead) - len(label) - 1))
    gap = pane.body_width - len(lead) - len(text) - len(label)
    return _row([(lead, attr), (text, theme_tokens.BODY),
                 (" " * max(1, gap) + label, token)])


def _judged_results(model):
    """`{check id: last judged status}` for every check the relay has judged.

    A check nobody has judged yet has no result: `pending` with no `round` means
    "not looked at", which is not an outcome a step can report.
    """
    results = {}
    for check in model.get("checks") or []:
        cid, status = check.get("id"), check.get("status")
        if not cid or not status:
            continue
        if status == "pending" and check.get("round") is None:
            continue
        results[cid] = status
    return results


def _step_result(step, results):
    """The last known result for one verification step, or `None`.

    Nothing on disk records the outcome of *running* a step, so the only result
    a step can carry is one that was judged: a step naming a check takes that
    check's last judged status. A step that names none has no recorded result
    and says so — a step is never reported as passing because its leg did.
    """
    for cid, status in results.items():
        if cid in step:
            return status
    return None
