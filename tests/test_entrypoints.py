"""ACC-CMD-001..003 — the two ways into Relay Control.

The skill installs by `git clone ... ~/.claude/skills/relay` and nothing after
that, so there are exactly two things a supervisor can reach:

* **`relay-control`** — an executable at the repository root. It resolves its
  own location, execs the TUI, and opens the relay found from the working
  directory or the one named on the command line (ACC-CMD-001, ACC-CMD-002).
* **`/relay-control`** — a skill. A slash command is a prompt for the model and
  cannot hand the terminal to a curses program
  (`.relay/research/slash-command-mechanism.md`), so the skill renders a
  *snapshot* into the conversation and prints the line that opens the live view
  (ACC-CMD-003).

Four rules this file follows:

* **Nothing is asserted against a hardcoded figure.** Every count, phase, leg
  id and title in a snapshot assertion is parsed out of the relay's own
  `legs.json` / `state.json` / `dashboard.json` at assert time. A previous
  leg's evidence went stale when a live relay advanced, and the agent-service
  fixture has been refreshed once already.
* **The launch line is not read, it is run.** `test_the_launch_line_opens_this
  _relay` takes the line out of the snapshot, feeds it to `sh` from an
  unrelated working directory, and asserts the frame that comes back is that
  relay's. A line that is merely *shaped* right is not evidence.
* **The clone is a real clone.** ACC-CMD-001 is about a copy of this repository
  living somewhere else entirely, so the tests that certify it copy the
  entrypoint and `scripts/` into `tmp_path` and run *that*, from a working
  directory outside it, with `PYTHONPATH` stripped out of the environment.
* **The harness comes from `tests/test_chrome.py`.** Two copies of `_utf8_env()`
  is two places for a locale bug to hide.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import TerminalSession  # noqa: E402

from test_chrome import (  # noqa: E402
    FIXTURES, READY, REPO, STANDARD, UTF8_ENV, WIDE, sweep,
)

import relay_model  # noqa: E402
from relay_control import chrome  # noqa: E402
from relay_control import theme as theme_tokens  # noqa: E402

#: The two files this leg ships.
ENTRYPOINT = REPO / "relay-control"
SKILL = REPO / "skills" / "relay-control" / "SKILL.md"

def entrypoint_module():
    """`relay-control`, imported as a module.

    It has no `.py` extension, because what a user types is `relay-control`,
    so it is loaded by path. Importing it is how the decisions that have no
    fixture — an empty attention list, a stream with no encoding, a count map
    missing a key — can be put to the question at all: none of them is
    reachable through a relay `build()` will produce.
    """
    loader = importlib.machinery.SourceFileLoader(
        "relay_control_entrypoint", str(ENTRYPOINT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


ENTRY = entrypoint_module()

#: Headings the snapshot draws. Asserted as needles rather than as a whole
#: rendering, so a wording change to one section cannot fail every test here.
TITLE_LINE = "Relay Control - snapshot"
ACTIVE_HEADING = "Active leg"
ATTENTION_HEADING = "Attention ("
LAUNCH_HEADING = "Open the live view"

#: The skill must never say it launched anything. A slash command cannot, so a
#: skill that claims to has told the user something false about their terminal.
LAUNCH_CLAIMS = re.compile(
    r"\b(i (have )?(just )?(launched|opened|started)"
    r"|launching (it|the tui|relay control)"
    r"|(the )?tui is (now )?(running|open)"
    r"|opening (it|the tui|relay control) (now|for you))",
    re.IGNORECASE,
)

#: A directory under /tmp that is a real directory and is not a relay, and one
#: that does not exist at all. Prefixed with this leg's id per the brief.
NOT_A_RELAY = "/tmp/entrypoints-not-a-relay"
NO_SUCH_PATH = "/tmp/entrypoints-no-such-relay-directory"


# --------------------------------------------------------------------------
# running the entrypoint
# --------------------------------------------------------------------------


def env_without_pythonpath(**extra):
    """A child environment with nothing of this test run's import state in it.

    `PYTHONPATH` is what the entrypoint must *not* need: a user who cloned the
    skill and typed the path has none of it. Stripping it here is what makes a
    pass mean something.
    """
    env = {key: value for key, value in UTF8_ENV.items() if key != "PYTHONPATH"}
    env.update(extra)
    return env


def run(args, cwd=None, env=None, text=True):
    """The entrypoint (or any argv), run to completion with its output captured."""
    argv = [str(part) for part in args]
    return subprocess.run(
        argv, cwd=None if cwd is None else str(cwd),
        env=env_without_pythonpath() if env is None else env,
        capture_output=True, text=text, timeout=60,
    )


def snapshot_of(relay_dir=None, entrypoint=None, cwd=None, env=None, extra=()):
    """`--snapshot` output, asserted to have succeeded."""
    args = [entrypoint or ENTRYPOINT, "--snapshot"]
    args.extend(extra)
    if relay_dir is not None:
        args.append(relay_dir)
    done = run(args, cwd=cwd, env=env)
    assert done.returncode == 0, done.stderr or done.stdout
    assert "Traceback" not in done.stderr, done.stderr
    return done.stdout


def tui(args, cwd=None, env=None, size=WIDE, entrypoint=None, **kwargs):
    """A started `TerminalSession` running the entrypoint, painted once.

    Returns once `q Quit` is on screen, so the first frame a caller takes is a
    screen the program finished rather than one caught mid-repaint.
    """
    rows, cols = size
    argv = [str(entrypoint or ENTRYPOINT)] + [str(part) for part in args]
    term = TerminalSession(
        argv, rows=rows, cols=cols,
        env=env_without_pythonpath() if env is None else env,
        cwd=None if cwd is None else str(cwd), **kwargs,
    )
    term.start()
    term.wait_for(READY)
    return term


def frame_of(args, **kwargs):
    """One finished frame of the TUI as the entrypoint launched it."""
    term = tui(args, **kwargs)
    try:
        return term.frame()
    finally:
        term.close()


def refusal(args, cwd=None, env=None):
    """`(exit code, what reached the terminal)` for a run that must not open.

    Run under a pty on purpose: the check is about what a supervisor sees in
    their own terminal, and a piped run is a different program state (no tty).
    """
    rows, cols = STANDARD
    argv = [str(ENTRYPOINT)] + [str(part) for part in args]
    term = TerminalSession(
        argv, rows=rows, cols=cols,
        env=env_without_pythonpath() if env is None else env,
        cwd=None if cwd is None else str(cwd),
    )
    term.start()
    code = term.wait(timeout=15)
    # Logical lines, not screen rows: a message longer than the terminal is
    # wrapped by the terminal, and counting the rows would report the program
    # as having printed two lines when it printed one.
    text = "\n".join(line for _, line in term.frame().logical_lines())
    term.close()
    return code, text


# --------------------------------------------------------------------------
# a clone somewhere else, and a project to run it from
# --------------------------------------------------------------------------


def clone(tmp_path, name="relay"):
    """A copy of the shipped runtime at `<tmp>/skills/<name>`.

    ACC-CMD-001 is a claim about a *clone*: the entrypoint has to find its own
    `scripts/` and not this repository's. Copying only what ships proves the
    entrypoint needs nothing else.
    """
    root = tmp_path / "skills" / name
    root.mkdir(parents=True)
    shutil.copy2(ENTRYPOINT, root / ENTRYPOINT.name)
    shutil.copytree(REPO / "scripts", root / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__"))
    return root


def project(tmp_path, fixture="agent-service", name="work"):
    """A working directory with a relay under it, as a real project has."""
    root = tmp_path / name
    root.mkdir(parents=True)
    shutil.copytree(FIXTURES / fixture, root / ".relay")
    return root


# --------------------------------------------------------------------------
# figures, read from the relay's own files at assert time
# --------------------------------------------------------------------------


def _load(relay_dir, name):
    path = Path(relay_dir) / name
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def title_of(relay_dir):
    """What this relay is called, read from its files rather than from the model.

    Same fallback chain `chrome.relay_title()` implements — a title the coach
    wrote, then the relay's name, then the directory. Written out here so the
    header is checked against the relay and not against itself.
    """
    title = _load(relay_dir, "dashboard.json").get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    name = _load(relay_dir, "legs.json").get("relay")
    if isinstance(name, str) and name.strip():
        return name.strip()
    parts = [part for part in str(relay_dir).split(os.sep) if part]
    while parts and parts[-1].startswith("."):
        parts.pop()
    return parts[-1] if parts else "Relay Control"


def leg_counts_of(relay_dir):
    """The five leg figures, counted out of `legs.json`.

    Only the status vocabulary is shared with the model — "what does `done`
    mean" is one decision and not two.
    """
    counts = dict.fromkeys(
        ("total", "completed", "running", "pending", "cancelled"), 0)
    for leg in _load(relay_dir, "legs.json").get("legs", []):
        if not isinstance(leg, dict):
            continue
        counts["total"] += 1
        status = relay_model.normalise_status(leg.get("status"))
        if status in counts:
            counts[status] += 1
    return counts


def check_counts_of(relay_dir):
    """The five check figures, counted out of `state.json`."""
    counts = dict.fromkeys(
        ("total", "passed", "failed", "blocked", "pending"), 0)
    checks = _load(relay_dir, "state.json").get("checks")
    for check in (checks or {}).values():
        if not isinstance(check, dict):
            continue
        counts["total"] += 1
        status = check.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def phase_of(relay_dir):
    """The phase `state.json` declares, or None where it declares none."""
    phase = _load(relay_dir, "state.json").get("phase")
    return phase.strip() if isinstance(phase, str) and phase.strip() else None


def running_leg_of(relay_dir):
    """The id of the leg this relay's own `legs.json` marks running."""
    for leg in _load(relay_dir, "legs.json").get("legs", []):
        if not isinstance(leg, dict):
            continue
        if relay_model.normalise_status(leg.get("status")) == "running":
            return leg.get("id")
    return None


def attention_of(relay_dir):
    """The attention items, from the model.

    Taken from `build()` and not from `dashboard.json` deliberately: which
    signals exist, and how a bare string becomes an item, is a derivation the
    model owns and `tests/test_relay_model.py` certifies. What is checked
    against it here is the snapshot's own property — that it carried every one
    of them, in order, with its action.
    """
    return relay_model.build(str(relay_dir))["attention"]


def launch_line(text):
    """The shell line the snapshot printed under `Open the live view`."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith(LAUNCH_HEADING):
            for candidate in lines[index + 1:]:
                if candidate.strip():
                    return candidate.strip()
    raise AssertionError("no launch line under %r in:\n%s"
                         % (LAUNCH_HEADING, text))


def flatten(text):
    """One line of text, so a needle cannot be missed by falling across a wrap."""
    return " ".join(text.split())


def section(text, heading):
    """The lines under `heading`, up to the next unindented heading."""
    lines = text.splitlines()
    out = []
    seen = False
    for line in lines:
        if not seen:
            seen = line.strip().startswith(heading)
            continue
        if line and not line[0].isspace():
            break
        out.append(line)
    if not seen:
        raise AssertionError("no %r section in:\n%s" % (heading, text))
    return out


# --------------------------------------------------------------------------
# ACC-CMD-001 — the shell entrypoint launches the live TUI
# --------------------------------------------------------------------------


def test_the_entrypoint_is_an_executable_file_at_the_repository_root():
    assert ENTRYPOINT.is_file(), "no relay-control at %s" % REPO
    mode = ENTRYPOINT.stat().st_mode
    assert mode & 0o111 == 0o111, (
        "relay-control is not executable by everyone: %s" % oct(mode))


def test_git_carries_the_executable_bit_so_a_clone_is_runnable():
    """A clone is `git clone`, and only the index's mode bit survives one.

    A file that happens to be `chmod +x` in this working tree and is recorded
    as `100644` is a file every user of the skill has to `chmod` themselves,
    which is precisely the post-install step ACC-CMD-001 forbids.
    """
    done = run(["git", "ls-files", "-s", "--", "relay-control"], cwd=REPO)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), (
        "relay-control is not in git's index; stage it so its mode is recorded")
    assert done.stdout.split()[0] == "100755", done.stdout


def test_the_entrypoint_opens_the_relay_found_from_the_working_directory(tmp_path):
    work = project(tmp_path)
    frame = frame_of([], cwd=work)
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_the_relay_is_found_from_a_directory_deep_under_the_project(tmp_path):
    """`find_relay()` walks up, so a supervisor need not be at the project root."""
    work = project(tmp_path)
    deep = work / "src" / "api" / "handlers"
    deep.mkdir(parents=True)
    frame = frame_of([], cwd=deep)
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_a_clone_elsewhere_opens_a_relay_it_is_nowhere_near(tmp_path):
    """The ACC-CMD-001 evidence: a clone at `~/.claude/skills/relay`.

    The working directory is a project with no relationship to the clone, and
    the environment carries no `PYTHONPATH` — so nothing but the entrypoint's
    own resolution of its own location can make this work.
    """
    root = clone(tmp_path)
    work = project(tmp_path)
    frame = frame_of([], cwd=work, entrypoint=root / "relay-control")
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_the_clone_needs_no_shell_configuration(tmp_path):
    """No alias, no PATH edit, no settings.json — a bare environment is enough."""
    root = clone(tmp_path)
    work = project(tmp_path)
    bare = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LC_ALL": UTF8_ENV.get("LC_ALL", "en_US.UTF-8"),
    }
    frame = frame_of([], cwd=work, env=bare, entrypoint=root / "relay-control")
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_the_entrypoint_resolves_itself_through_a_symlink(tmp_path):
    """`~/bin/relay-control -> the clone` still finds the clone's `scripts/`.

    A user who does put it on their PATH does it with a symlink, and a script
    that resolved `__file__` without following one would look for `scripts/`
    beside the *link*.
    """
    root = clone(tmp_path)
    work = project(tmp_path)
    link = tmp_path / "bin"
    link.mkdir()
    (link / "relay-control").symlink_to(root / "relay-control")
    frame = frame_of([], cwd=work, entrypoint=link / "relay-control")
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_a_hostile_pythonpath_cannot_shadow_the_clones_own_modules(tmp_path):
    """The clone's `scripts/` goes in front of whatever the user already had."""
    root = clone(tmp_path)
    work = project(tmp_path)
    hostile = tmp_path / "hostile"
    (hostile / "relay_control").mkdir(parents=True)
    (hostile / "relay_model.py").write_text(
        "raise ImportError('the hostile relay_model was imported')\n")
    (hostile / "relay_control" / "__init__.py").write_text(
        "raise ImportError('the hostile relay_control was imported')\n")
    env = env_without_pythonpath(PYTHONPATH=str(hostile))
    frame = frame_of([], cwd=work, env=env, entrypoint=root / "relay-control")
    assert frame.contains(title_of(work / ".relay")), frame.text


def test_the_entrypoint_exits_zero_when_the_tui_is_quit(tmp_path):
    """It execs the TUI rather than wrapping it, so `q` is the whole exit path."""
    work = project(tmp_path)
    term = tui([], cwd=work)
    try:
        term.send("q")
        assert term.wait(timeout=15) == 0
    finally:
        term.close()


# --------------------------------------------------------------------------
# ACC-CMD-002 — an explicit relay directory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["positional", "--relay-dir"])
def test_both_spellings_target_the_named_relay(tmp_path, spelling):
    """Run from a directory with no relay of its own, so only the argument can
    be what opened this one."""
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    relay = FIXTURES / "agent-service"
    args = [relay] if spelling == "positional" else ["--relay-dir", relay]
    frame = frame_of(args, cwd=elsewhere)
    assert frame.contains(title_of(relay)), frame.text


def test_an_explicit_relay_beats_the_one_under_the_working_directory(tmp_path):
    """A supervisor watching another project's relay from inside their own."""
    work = project(tmp_path, fixture="running-impl")
    named = FIXTURES / "agent-service"
    frame = frame_of([named], cwd=work)
    assert frame.contains(title_of(named)), frame.text
    assert not frame.contains(title_of(work / ".relay")), frame.text


def test_the_named_spelling_wins_over_the_positional_one(tmp_path):
    """Same precedence the TUI's own parser has, so the two cannot disagree."""
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    named = FIXTURES / "agent-service"
    other = FIXTURES / "running-impl"
    frame = frame_of([other, "--relay-dir", named], cwd=elsewhere)
    assert frame.contains(title_of(named)), frame.text


def test_a_relay_at_a_path_with_a_space_in_it_opens(tmp_path):
    """The relay this leg is verified against lives under `AI internal`."""
    awkward = tmp_path / "AI internal" / "agent service"
    awkward.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / "agent-service", awkward)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    frame = frame_of([awkward], cwd=elsewhere)
    assert frame.contains(title_of(awkward)), frame.text


def test_a_project_root_resolves_to_the_relay_underneath_it(tmp_path):
    """`relay-control ~/some/project` means the relay in it, not the project."""
    work = project(tmp_path)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    frame = frame_of([work], cwd=elsewhere)
    assert frame.contains(title_of(work / ".relay")), frame.text


# -- ACC-ROBUST-004: a message, not a traceback ----------------------------


def _not_a_relay():
    os.makedirs(NOT_A_RELAY, exist_ok=True)
    return NOT_A_RELAY


@pytest.mark.parametrize("case", ["directory", "missing", "file", "fixture"])
def test_a_path_that_is_not_a_relay_is_one_line_and_a_non_zero_exit(tmp_path, case):
    """Four ways to name something that is not a relay, one answer to all four.

    `fixture` is `tests/fixtures/empty`, which holds a `.gitkeep` and nothing
    else. It is refused for the same reason `/tmp` is, because it is the same
    thing: a directory with no relay file in it and no `.relay` in its name.
    An empty directory the user *called* `.relay` is a different case and
    opens — `test_an_empty_relay_directory_still_opens`.
    """
    if case == "directory":
        target = _not_a_relay()
    elif case == "fixture":
        target = FIXTURES / "empty"
    elif case == "missing":
        target = NO_SUCH_PATH
        assert not os.path.exists(target)
    else:
        target = tmp_path / "notes.txt"
        target.write_text("not a relay\n")
    code, text = refusal([target])
    assert code != 0, text
    assert "Traceback" not in text, text
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1, "expected one explanatory line, got:\n%s" % text
    assert str(target) in lines[0], lines[0]


def test_running_outside_any_relay_is_a_message_not_a_traceback(tmp_path):
    code, text = refusal([], cwd=tmp_path)
    assert code != 0, text
    assert "Traceback" not in text, text
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1, "expected one explanatory line, got:\n%s" % text


def test_the_refusal_is_the_same_whether_it_is_piped_or_in_a_terminal():
    """A piped run must not answer about the terminal when the relay is wrong.

    The TUI's own dumb-terminal refusal is true of a pipe *and* useless: it
    sends a supervisor to fix their `TERM` when what is wrong is the path they
    typed. The entrypoint settles the relay before it hands the terminal over.
    """
    target = _not_a_relay()
    done = run([ENTRYPOINT, target])
    assert done.returncode != 0
    assert "Traceback" not in done.stderr, done.stderr
    assert target in done.stderr, done.stderr
    piped = [line.strip() for line in done.stderr.splitlines() if line.strip()]
    assert len(piped) == 1, done.stderr
    _, text = refusal([target])
    on_screen = [line.strip() for line in text.splitlines() if line.strip()]
    assert on_screen == piped, (piped, on_screen)


def test_a_directory_whose_name_merely_ends_in_relay_is_not_one(tmp_path):
    """`.relay` is the name, not the suffix.

    An empty `notes.relay/` beside a project is a directory somebody named,
    and opening the TUI on it would draw a relay that does not exist — the
    same lie `/tmp` would tell.
    """
    target = tmp_path / "notes.relay"
    target.mkdir()
    code, text = refusal([target])
    assert code != 0, text
    assert str(target) in text, text


def test_an_empty_relay_directory_still_opens(tmp_path):
    """A relay the coach has created and not yet written to is a relay.

    `.relay/` by name is the one directory that needs nothing in it — a fresh
    run is exactly that for its first minute, and refusing it would refuse the
    moment a supervisor most wants to watch.
    """
    work = tmp_path / "fresh"
    (work / ".relay").mkdir(parents=True)
    frame = frame_of([], cwd=work)
    assert frame.contains(title_of(work / ".relay")), frame.text


# --------------------------------------------------------------------------
# ACC-CMD-003 — the snapshot the skill renders
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", ["agent-service", "running-impl", "all-done"])
def test_the_snapshot_carries_the_relays_own_leg_and_check_figures(fixture):
    relay = FIXTURES / fixture
    text = snapshot_of(relay)
    legs = leg_counts_of(relay)
    checks = check_counts_of(relay)
    # Read out of the summary block and not out of the whole snapshot: a leg
    # goal or an attention item is coach prose and may begin with any word.
    head = section(text, TITLE_LINE)
    legs_line = [line for line in head if line.strip().startswith("Legs")]
    checks_line = [line for line in head if line.strip().startswith("Checks")]
    assert len(legs_line) == 1 and len(checks_line) == 1, text
    for key, value in legs.items():
        needle = "%d total" % value if key == "total" else "%d %s" % (value, key)
        assert needle in legs_line[0], (needle, legs_line[0])
    for key, value in checks.items():
        needle = "%d total" % value if key == "total" else "%d %s" % (value, key)
        assert needle in checks_line[0], (needle, checks_line[0])
    # In the order the states are worth reading, not in whatever order a dict
    # iterated: done, in flight, waiting, abandoned.
    for line, order in ((legs_line[0], ("completed", "running", "pending",
                                        "cancelled")),
                        (checks_line[0], ("passed", "failed", "blocked",
                                          "pending"))):
        at = [line.index(name) for name in order]
        assert at == sorted(at), (order, line)


@pytest.mark.parametrize("fixture", ["agent-service", "running-impl", "all-done"])
def test_the_snapshot_names_the_relay_and_its_phase(fixture):
    relay = FIXTURES / fixture
    text = snapshot_of(relay)
    assert title_of(relay) in text, text
    phase = phase_of(relay)
    assert phase is not None, "fixture %s declares no phase" % fixture
    phase_line = [line for line in section(text, TITLE_LINE)
                  if line.strip().startswith("Phase")]
    assert len(phase_line) == 1, text
    assert phase in phase_line[0], (phase, phase_line[0])


def test_the_head_block_labels_the_project_and_the_relay_directory(tmp_path):
    """Two paths, both said. A snapshot pasted into a conversation is read by
    someone with three relays open, and `Wave 2 …` is a title two of them
    could share."""
    work = project(tmp_path)
    head = section(snapshot_of(cwd=work), TITLE_LINE)
    rows = {}
    for line in head:
        found = re.match(r"^  ([A-Za-z]+) +(\S.*)$", line)
        if found:
            rows.setdefault(found.group(1), found.group(2))
    assert rows.get("project") == str(work), rows
    assert rows.get("relay") == str(work / ".relay"), rows


def test_the_summary_labels_line_their_values_up():
    """A column a reader's eye can run down. Four figures in four places is a
    table; four figures at four indents is a paragraph of numbers."""
    head = section(snapshot_of(FIXTURES / "agent-service"), TITLE_LINE)
    labelled = [line for line in head
                if re.match(r"^  [A-Za-z]+ {2,}\S", line)]
    assert len(labelled) >= 5, head
    columns = {len(line) - len(line.split("  ")[-1] or line) for line in labelled}
    starts = {re.match(r"^  [A-Za-z]+ +", line).end() for line in labelled}
    assert len(starts) == 1, (starts, labelled)
    assert columns  # the split above is only meaningful for a padded row


def test_the_snapshot_names_the_stage_the_relay_declares():
    """`state.json` names the stage by id; `legs.json` is where it has a name."""
    relay = FIXTURES / "agent-service"
    stage_id = _load(relay, "state.json").get("currentStage")
    assert isinstance(stage_id, str) and stage_id, "fixture declares no stage"
    named = [stage.get("name") for stage in _load(relay, "legs.json").get("stages", [])
             if isinstance(stage, dict) and stage.get("id") == stage_id]
    assert named and named[0], "the fixture's stage has no name in legs.json"
    line = [row for row in section(snapshot_of(relay), TITLE_LINE)
            if row.strip().startswith("Stage")]
    assert len(line) == 1, line
    assert stage_id in line[0] and named[0] in line[0], line


def test_a_relay_that_declares_no_stage_has_no_empty_stage_row(tmp_path):
    """An absent stage is a row that is not drawn, never a label with nothing
    beside it — the reader cannot tell a blank from a bug."""
    relay = tmp_path / "no-stage"
    shutil.copytree(FIXTURES / "running-impl", relay)
    state = json.loads((relay / "state.json").read_text())
    state.pop("currentStage", None)
    (relay / "state.json").write_text(json.dumps(state))
    head = section(snapshot_of(relay), TITLE_LINE)
    assert not [row for row in head if row.strip().startswith("Stage")], head


def test_the_active_leg_carries_its_stage_kind_and_goal(tmp_path):
    relay = tmp_path / "detailed"
    shutil.copytree(FIXTURES / "running-impl", relay)
    legs = json.loads((relay / "legs.json").read_text())
    running = None
    for leg in legs["legs"]:
        if relay_model.normalise_status(leg.get("status")) == "running":
            leg["goal"] = ("REBUILD the widget seam so that the two readers "
                           "agree about what a leg is, and so that neither of "
                           "them has to know how the other one counts")
            leg["kind"] = "fix"
            running = leg
    assert running, "fixture has no running leg"
    (relay / "legs.json").write_text(json.dumps(legs))
    body = section(snapshot_of(relay), ACTIVE_HEADING)
    flat = flatten("\n".join(body))
    assert running["id"] in flat, body
    assert running["stage"] in flat, body
    assert "fix" in flat, body
    assert running["goal"] in flat, body
    # Wrapped, not one very long row: this is pasted into a conversation.
    assert max(chrome.cell_width(line) for line in body) <= 80, body
    # The id, then at least two rows of goal: it was folded, not printed long.
    assert len([line for line in body if line.strip()]) >= 3, body


def test_the_active_leg_says_when_the_relay_recorded_no_goal():
    """The agent-service fixture's judge legs carry no goal, and a section that
    silently drew nothing there would read as the goal being blank."""
    relay = FIXTURES / "agent-service"
    body = "\n".join(section(snapshot_of(relay), ACTIVE_HEADING))
    assert "no goal recorded" in body, body


def test_the_snapshot_wraps_its_prose_to_something_a_reader_can_read():
    """It is pasted into a conversation, not into a terminal that will wrap it.

    Only prose is bounded: a relay's absolute path and the launch line are one
    token each and are worth more whole than folded.
    """
    text = snapshot_of(FIXTURES / "agent-service")
    prose_lines = (section(text, ACTIVE_HEADING)
                   + section(text, ATTENTION_HEADING))
    widths = [chrome.cell_width(line) for line in prose_lines]
    assert max(widths) <= 80, [line for line in prose_lines
                               if chrome.cell_width(line) > 80]
    assert max(widths) > 60, "the prose is folded far narrower than it reads"


def test_the_attention_items_are_separated_from_one_another():
    body = section(snapshot_of(FIXTURES / "agent-service"), ATTENTION_HEADING)
    labels = [index for index, line in enumerate(body)
              if re.match(r"^  \[[a-z ]+\] \S", line)]
    assert len(labels) > 1, body
    for index in labels[1:]:
        assert not body[index - 1].strip(), (index, body)


def test_the_snapshot_names_the_running_leg():
    relay = FIXTURES / "agent-service"
    running = running_leg_of(relay)
    assert running, "fixture has no running leg"
    body = "\n".join(section(snapshot_of(relay), ACTIVE_HEADING))
    assert running in body, body


def test_a_relay_with_no_running_leg_says_so_rather_than_dropping_the_section():
    """An absent fact is stated. A missing section reads as a rendering bug."""
    relay = FIXTURES / "all-done"
    assert running_leg_of(relay) is None
    body = "\n".join(section(snapshot_of(relay), ACTIVE_HEADING)).strip()
    assert body, "the Active leg section is empty"
    assert "none" in body.lower(), body


def test_the_snapshot_carries_every_attention_item_in_order():
    relay = FIXTURES / "agent-service"
    items = attention_of(relay)
    assert len(items) > 1, "fixture has too few attention items to order"
    text = snapshot_of(relay)
    body = "\n".join(section(text, ATTENTION_HEADING))
    positions = []
    for item in items:
        label = item.get("label") or ""
        assert label in body, (label, body)
        positions.append(body.index(label))
    assert positions == sorted(positions), positions


def test_an_attention_item_carries_its_action_and_only_that_item(tmp_path):
    """The action is what a supervisor does next. An item without one has no
    action line at all — a blank arrow would read as an instruction to do
    nothing, which is not the same as nobody having said."""
    relay = tmp_path / "actioned"
    shutil.copytree(FIXTURES / "running-impl", relay)
    (relay / "dashboard.json").write_text(json.dumps({"attention": [
        {"level": "bad", "label": "STALLED",
         "text": "a check has failed six rounds",
         "action": "pause, then re-scope"},
        {"level": "note", "label": "NOTE", "text": "nobody has to do anything"},
    ]}))
    items = attention_of(relay)
    assert [bool(item.get("action")) for item in items] == [True, False], items
    rows = section(snapshot_of(relay), ATTENTION_HEADING)
    body = "\n".join(rows)
    assert "pause, then re-scope" in body, body
    assert body.count("->") == 1, body
    # `bad` and `note` are three and four characters. A bracket column that
    # moved between them would step every item's text in and out with it.
    brackets = {line.index("]") for line in rows if re.match(r"^  \[[a-z]", line)}
    assert len(brackets) == 1, (brackets, rows)


@pytest.mark.parametrize("fixture", ["agent-service", "running-impl", "all-done"])
def test_the_attention_heading_counts_the_relays_own_items(fixture):
    """The heading states how many there are, so a cut list cannot read whole."""
    relay = FIXTURES / fixture
    items = attention_of(relay)
    text = snapshot_of(relay)
    assert "Attention (%d)" % len(items) in text, text
    body = "\n".join(section(text, ATTENTION_HEADING))
    for item in items:
        assert (item.get("label") or "") in body, (item, body)


def test_the_snapshot_resolves_the_relay_from_the_working_directory(tmp_path):
    work = project(tmp_path)
    text = snapshot_of(cwd=work)
    assert title_of(work / ".relay") in text, text


def test_the_snapshot_and_the_tui_open_the_same_relay(tmp_path):
    """One resolution, used twice — a snapshot that described another relay
    than the line under it opens would be worse than no snapshot."""
    work = project(tmp_path)
    text = snapshot_of(cwd=work)
    assert str((work / ".relay").resolve()) in text, text
    frame = frame_of([], cwd=work)
    assert frame.contains(title_of(work / ".relay")), frame.text


# -- the launch line -------------------------------------------------------


def test_the_launch_line_opens_this_relay(tmp_path):
    """Not read — run. From a working directory with no relay anywhere above it.

    This is the whole of ACC-CMD-003's second half: the line has to be one a
    supervisor can paste, and the only proof of that is pasting it.
    """
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    relay = FIXTURES / "agent-service"
    line = launch_line(snapshot_of(relay))
    rows, cols = WIDE
    term = TerminalSession(
        ["/bin/sh", "-c", line], rows=rows, cols=cols,
        env=env_without_pythonpath(), cwd=str(elsewhere),
    )
    term.start()
    try:
        term.wait_for(READY)
        assert term.frame().contains(title_of(relay)), term.frame().text
    finally:
        term.close()


def test_the_launch_line_quotes_a_path_a_shell_would_otherwise_split(tmp_path):
    """The relay this leg is verified against is under `AI internal`."""
    awkward = tmp_path / "AI internal" / "agent service"
    awkward.parent.mkdir(parents=True)
    shutil.copytree(FIXTURES / "agent-service", awkward)
    line = launch_line(snapshot_of(awkward))
    assert shlex.split(line)[-1] == str(awkward), line
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    rows, cols = WIDE
    term = TerminalSession(
        ["/bin/sh", "-c", line], rows=rows, cols=cols,
        env=env_without_pythonpath(), cwd=str(elsewhere),
    )
    term.start()
    try:
        term.wait_for(READY)
        assert term.frame().contains(title_of(awkward)), term.frame().text
    finally:
        term.close()


def test_the_launch_line_names_the_entrypoint_that_printed_it(tmp_path):
    """A clone's snapshot prints the *clone's* path, not this repository's —
    and quotes it, because `~/.claude/skills` is not the only place a clone
    ever lives."""
    root = clone(tmp_path, name="relay skill")
    relay = FIXTURES / "agent-service"
    line = launch_line(snapshot_of(relay, entrypoint=root / "relay-control"))
    assert shlex.split(line)[0] == str(root / "relay-control"), line


def test_the_launch_line_is_absolute_on_both_halves(tmp_path):
    """A relative path in it would open a different relay from another shell."""
    work = project(tmp_path)
    line = launch_line(snapshot_of(".relay", cwd=work))
    parts = shlex.split(line)
    assert len(parts) == 2, line
    assert all(os.path.isabs(part) for part in parts), line


def test_the_snapshot_sections_come_in_the_order_they_are_read_in():
    """Who and how much, then what is happening, then what needs a human, then
    how to see it live. A reader who stops after the first screen has the
    figures; a reader who reads to the end has the command."""
    text = snapshot_of(FIXTURES / "agent-service")
    at = [text.index(needle) for needle in
          (TITLE_LINE, "  Phase", ACTIVE_HEADING, ATTENTION_HEADING,
           LAUNCH_HEADING)]
    assert at == sorted(at), at


def test_the_snapshot_never_claims_to_have_launched_anything():
    flat = flatten(snapshot_of(FIXTURES / "agent-service"))
    found = LAUNCH_CLAIMS.search(flat)
    assert not found, "the snapshot claims a launch: %r" % (found and found.group(0))
    assert "Nothing has been launched" in flat, flat


# -- the snapshot is a reader of the model and of nothing else -------------


def test_the_entrypoint_reads_no_relay_file_of_its_own():
    """ACC-TUI-007's rule, kept where the package's own sweep cannot reach.

    A second reader is a second answer, and the snapshot exists precisely to
    say the same things the TUI says.
    """
    findings, nodes = sweep([ENTRYPOINT])
    assert not findings, findings
    assert nodes > 150, "the sweep walked only %d AST nodes" % nodes


def test_the_reader_sweep_would_catch_a_planted_reader(tmp_path):
    planted = tmp_path / "relay-control"
    planted.write_text(
        ENTRYPOINT.read_text()
        + "\n\ndef _planted():\n"
        "    return open('legs.json').read()\n")
    findings, _ = sweep([planted])
    assert findings, "the sweep passed a file that opens legs.json"


def test_untrusted_prose_reaches_the_snapshot_with_its_control_characters_marked(
        tmp_path):
    """Coach prose is untrusted, and a snapshot is pasted into a terminal too.

    An escape sequence in a leg goal must reach the reader as a visible mark,
    not as a cursor movement and not as a silent deletion (ACC-DATA-007).
    """
    relay = tmp_path / "poisoned"
    shutil.copytree(FIXTURES / "running-impl", relay)
    legs = json.loads((relay / "legs.json").read_text())
    marker = "GOAL\x1b[2JWITH\x07AN\x1bESCAPE"
    for leg in legs["legs"]:
        if relay_model.normalise_status(leg.get("status")) == "running":
            leg["goal"] = marker
    (relay / "legs.json").write_text(json.dumps(legs))
    text = snapshot_of(relay)
    assert "GOAL" in text and "ESCAPE" in text, text
    for ordinal in chrome.CONTROL_ORDINALS:
        assert chr(ordinal) not in text.replace("\n", ""), (
            "control character %#x survived into the snapshot" % ordinal)
    assert theme_tokens.GLYPHS["control"] in text, text

    # Down a pipe that cannot carry the mark it becomes the theme's ASCII
    # spelling rather than an escape — the mark exists to be *seen*.
    plain = snapshot_of(relay, env=env_without_pythonpath(
        PYTHONIOENCODING="ascii", LC_ALL="C"))
    assert theme_tokens.ASCII_GLYPHS["control"] in plain, plain
    assert theme_tokens.GLYPHS["control"] not in plain, plain
    assert "GOAL" in plain and "ESCAPE" in plain, plain


def test_an_output_encoding_that_cannot_carry_the_prose_does_not_traceback(
        tmp_path):
    """`PYTHONIOENCODING=ascii` is a real terminal, and an em dash is real prose."""
    relay = tmp_path / "wide-prose"
    shutil.copytree(FIXTURES / "running-impl", relay)
    legs = json.loads((relay / "legs.json").read_text())
    for leg in legs["legs"]:
        if relay_model.normalise_status(leg.get("status")) == "running":
            leg["goal"] = "an em dash — and a CJK run 中文字 in one goal"
    (relay / "legs.json").write_text(json.dumps(legs, ensure_ascii=False))
    env = env_without_pythonpath(PYTHONIOENCODING="ascii", LC_ALL="C")
    done = run([ENTRYPOINT, "--snapshot", relay], env=env)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr, done.stderr
    assert LAUNCH_HEADING in done.stdout, done.stdout
    # Escaped, not dropped. A goal that quietly lost four characters reads as
    # a goal its author wrote that way, which is the lie this repository keeps
    # refusing (`chrome.sanitise`'s docstring says so at length).
    assert "\\u2014" in done.stdout, done.stdout


def test_a_snapshot_of_a_path_that_is_not_a_relay_is_the_same_refusal():
    target = _not_a_relay()
    done = run([ENTRYPOINT, "--snapshot", target])
    assert done.returncode != 0
    assert "Traceback" not in done.stderr, done.stderr
    assert target in done.stderr, done.stderr
    assert len([line for line in done.stderr.splitlines() if line.strip()]) == 1


# -- the pieces with no fixture, called directly ---------------------------


def test_a_count_map_missing_a_figure_reads_zero_and_not_a_traceback():
    """`legCounts` carries all five keys today. The snapshot still has to be
    the thing that says so rather than the thing that assumes it."""
    assert ENTRY.counts({}, ("passed", "failed")) == "0 total: 0 passed, 0 failed"
    assert ENTRY.counts({"total": 3, "passed": 1}, ("passed", "failed")) == \
        "3 total: 1 passed, 0 failed"


def test_a_stream_that_will_not_say_what_it_encodes_gets_the_ascii_mark():
    """The conservative answer, because the cost of being wrong is one-sided:
    an ASCII mark on a UTF-8 terminal is legible, and a UTF-8 mark on an ASCII
    one is an exception in the middle of a snapshot."""
    class Anonymous:
        pass

    assert ENTRY.control_mark(Anonymous()) == theme_tokens.ASCII_GLYPHS["control"]
    assert ENTRY.control_mark(Anonymous()) != theme_tokens.GLYPHS["control"]


def test_an_empty_attention_list_renders_a_heading_and_nothing_else():
    """No relay `build()` produces one — it always synthesises at least a calm
    item — so this is the only place the empty case can be stated."""
    lines = ENTRY.attention_lines([], "?")
    assert lines[0] == "Attention (0)"
    assert not [line for line in lines[1:] if line.strip()]


# --------------------------------------------------------------------------
# ACC-CMD-003 — the skill itself
# --------------------------------------------------------------------------


def frontmatter(text):
    """The skill's YAML header as `{key: value}`; stdlib only, so line-wise."""
    lines = text.splitlines()
    assert lines and lines[0].strip() == "---", "no frontmatter"
    fields = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            return fields
        if line[:1].isspace() and key:
            fields[key] += " " + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    raise AssertionError("frontmatter never closed")


def shell_blocks(text):
    """Every fenced ```bash block in the skill, in order."""
    return re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.DOTALL)


def test_the_skill_file_exists_where_a_skill_directory_goes():
    assert SKILL.is_file(), "no SKILL.md at %s" % SKILL
    assert SKILL.parent.name == "relay-control", SKILL


def test_the_skill_declares_its_name_and_a_description():
    fields = frontmatter(SKILL.read_text())
    assert fields.get("name") == "relay-control", fields
    assert len(fields.get("description", "")) > 40, fields


def test_the_skill_runs_the_snapshot_command_as_written(tmp_path):
    """The block in the skill is executed verbatim, with only the override the
    skill itself documents supplied. A command that has drifted from the
    entrypoint fails here rather than in a user's session."""
    work = project(tmp_path)
    blocks = shell_blocks(SKILL.read_text())
    assert blocks, "the skill names no command"
    env = env_without_pythonpath(RELAY_CONTROL=str(ENTRYPOINT))
    done = subprocess.run(
        ["/bin/sh", "-e", "-c", blocks[0]], cwd=str(work), env=env,
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert TITLE_LINE in done.stdout, done.stdout
    assert LAUNCH_HEADING in done.stdout, done.stdout
    assert title_of(work / ".relay") in done.stdout, done.stdout


def test_the_skill_never_claims_to_have_launched_the_tui():
    found = LAUNCH_CLAIMS.search(flatten(SKILL.read_text()))
    assert not found, "the skill claims a launch: %r" % (found and found.group(0))


def test_the_skill_says_out_loud_that_it_cannot_launch_the_tui():
    """The research that reshaped this area has to survive in the artefact.

    Without it the next reader tries to make the slash command open curses,
    which is the two days ACC-CMD-001's amendment exists to save.
    """
    text = SKILL.read_text().lower()
    assert "cannot" in text and "terminal" in text, text
    assert "slash command" in text or "slash-command" in text, text


def test_the_skill_tells_the_model_to_reproduce_the_output_rather_than_summarise():
    text = SKILL.read_text().lower()
    assert "verbatim" in text, text
    assert "summar" in text, text


def test_the_skill_documents_how_it_is_installed():
    """A skill directory nested inside another skill is never loaded, so the
    one install step there is has to be written down where it is needed."""
    text = SKILL.read_text()
    assert "~/.claude/skills/relay-control" in text, text


def test_the_readme_documents_both_ways_in():
    """Documented as a command a reader can run, not as a filename in passing."""
    text = (REPO / "README.md").read_text()
    blocks = re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL)
    assert any("relay-control" in block for block in blocks), \
        "the README shows no runnable relay-control line"
    assert "`/relay-control`" in text, \
        "the README does not name the /relay-control skill"
