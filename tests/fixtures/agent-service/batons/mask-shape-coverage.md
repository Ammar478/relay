# mask-shape-coverage

## Implemented

`services/agent-service/src/utils/credential_value.py` — one shared predicate, rewritten.

**The rule:** a value is a real credential iff, after `strip()`, it is a non-empty string in which
*information-bearing characters are a strict majority*.

```python
ASCII_MASK_CHARACTER = "*"

def _carries_credential_information(character: str) -> bool:
    if character.isspace() or character == ASCII_MASK_CHARACTER:
        return False
    return character.isascii() or character.isalnum()

def is_a_real_credential(value):
    ... strip, reject non-str / empty ...
    carrying = sum(1 for c in stripped if _carries_credential_information(c))
    return carrying * 2 > len(stripped)
```

`MASK_CHARACTERS = "*•·●∙"` is gone. Nothing else in the repo referenced it (grep, repo-wide).

**Reasoning — two independent problems, one predicate.**

1. *Character class.* The old constant was an enumeration, so every new look-alike glyph was a new
   bug. The replacement asks what a character can possibly contribute instead of listing bad glyphs:
   a credential travels over HTTP headers and Snowflake APIs, so its characters are printable ASCII
   (base64url, `-`, `_`, `.`, `:`, `=`). Therefore **non-ASCII characters that are not alphanumeric
   carry no credential information** — that single clause covers `•·●∙`, `∗` U+2217, `＊` U+FF0A,
   `⁎` U+204E, `○` U+25CB, and every glyph nobody has thought of yet, plus the invisible ones
   (`​`, `‌`, `‍`, `﻿`), which `str.strip()` does not remove. Non-ASCII
   *alphanumerics* (`naïve-pässwörd-1`, CJK) stay bearing, so a non-English passphrase is safe.
   ASCII `*` is named explicitly because it is the one mask glyph that is also typeable ASCII.
   Interior whitespace is non-bearing too — that is what stops `"​   ​"` from passing by
   riding on three spaces.

2. *Partial reveal (`••••abcd`).* No character set can catch this — the four alphanumerics are
   genuine. The distinguishing fact is a proportion, and the principled form of it is:
   **a mask that reveals more than it hides is not a mask.** Masking exists to hide the majority of
   the secret; a UI that renders more real characters than mask characters has leaked the credential
   and is not a rendering anyone ships. So the majority test is not a tuned threshold — it is the
   definition of masking. Every real partial reveal clears it easily: the convention is the last 4
   characters, so `••••abcd` (exactly half → refused, ties count as mask), `••••••••abcd`,
   `<224 bullets>abcd`, and the first-four variant `abcd••••` are all refused.

**Deliberately NOT used, and why:**

- *"Any mask character disqualifies."* Contradicts the existing, passing assertion that
  `"pat*with*stars*inside"` is real (`test_one_credential_rule.py::REAL_VALUES`,
  `test_create_rejects_a_mask.py::test_a_credential_that_merely_contains_a_mask_character_is_accepted`).
  Mutation M5 below adopts that rule and shows it fails 8 assertions — it locks an admin out.
- *A minimum length floor* (from the `_validate_pat` / 4096 / ~228-char dev-value hints). Rejected on
  YAGNI + lock-out risk: it catches nothing the majority rule misses, and the suites already treat
  10-character values (`"pat-test-x"`) as real, so any floor is a new way to refuse a live rotation.
  Length is only used as an *upper* bound, which `_validate_pat` already owns.

**Tests** (`services/agent-service/tests/`, the only other thing touched):

- New `tests/unit/test_mask_shape_coverage.py` (50 assertions): partial reveals (leading, trailing,
  long-mask, whitespace-padded, `*`-based); the exact-half boundary (`••••abcd` refused /`•••abcd`
  accepted); look-alikes including five glyphs deliberately *not* mentioned in the leg brief
  (`◍ ⬤ ❋ ⁕`, mixed `＊•∗○`) to prove the class is not an enumeration; invisible values; a
  no-regression block for every shape refused before this leg; non-strings; and
  `TestEveryRealCredentialStaysAccepted` — 11 real shapes incl. JWT-style, Snowflake
  `ver:1-hint:...`-style, 228-char dev-length, the 4096 store limit, accented, and 1-char.
- `tests/unit/test_one_credential_rule.py`: **added** 9 shapes to `NOT_CREDENTIAL_VALUES`
  (`••••abcd`, `****abcd`, `∗∗∗`, `＊＊＊`, `⁎⁎⁎`, `○○○`, `​`). Additive only — no existing
  value or assertion was changed or removed. Because that list also feeds the call-site delegation
  test and the PG `_store_credential` test, the new shapes are now proven at every call site and
  proven never to reach the vault.

Counts after: `tests/pg` 428 passed + 1 xfailed (unchanged) · `tests/` minus pg 5 failed / 1626
passed (the same 5 pre-existing `tests/db/test_backend_selection.py` psycopg failures, +70 new
passes, none lost) · `tests/security` 620 passed (unchanged).

## Left undone

- Nothing in scope. No other `src/` file touched; the three call sites already import the shared
  predicate and needed no change.
- `mypy` is not installed in `.venv`, so the CI type-check was not run locally. ruff/black/isort all
  pass on the three files.
- Not addressed by design (documented above, not a defect): a hypothetical UI that reveals *more*
  characters than it masks (e.g. `•••abcdefgh`) reads as real. That rendering would itself be a
  credential leak, and catching it would require a length or leading-run heuristic that risks
  refusing real credentials.

## Commands run

All from `services/agent-service` with `.venv/bin/python`.

1. `PYTHONPATH=<pgoverlay> PGTEST_REQUIRED=1 .venv/bin/python -m pytest tests/pg -p no:warnings --tb=short`
   → **428 passed, 1 xfailed** in 57.60s (matches baseline).
2. `.venv/bin/python -m pytest tests/ --ignore=tests/pg -p no:warnings --tb=no`
   → **5 failed, 1626 passed**. The 5 are the pre-existing
   `tests/db/test_backend_selection.py` psycopg failures — same 5 node ids, count did not grow.
3. `.venv/bin/python -m pytest tests/security -p no:warnings --tb=no` → **620 passed** (baseline).
4. `ruff check` / `black --check` / `isort --check-only --diff` on
   `src/utils/credential_value.py`, `tests/unit/test_mask_shape_coverage.py`,
   `tests/unit/test_one_credential_rule.py` → all clean.
5. `.venv/bin/python -m pytest tests/unit -p no:warnings --tb=no` after restoring every mutation
   → **845 passed**.

### Mutations (each applied to `src/utils/credential_value.py`, run against the five credential test
files — 252 tests — then reverted)

| # | Mutation | Result |
|---|---|---|
| M1 | Restore the pre-leg rule verbatim (`bool(stripped.strip(MASK_CHARACTERS))`) | **37 failed**, 215 passed |
| M2 | Tie counts as a credential (`carrying * 2 > len` → `>=`) | **10 failed**, 242 passed — incl. `••••abcd`, `abcd••••`, `＊＊＊＊abcd`, the boundary test |
| M3 | Non-ASCII glyphs treated as bearing (drop the `isascii()/isalnum()` clause) | **89 failed**, 163 passed |
| M4 | Interior whitespace treated as bearing (drop `.isspace()`) | **1 failed**, 251 passed — `"​   ​"` |
| M5 | Over-strict: any mask character disqualifies (`carrying == len`) | **8 failed**, 244 passed — `pat*with*stars*inside` fails at the rule, at the PG vault store, and at the create router; this is the assertion that proves a real credential is still accepted |

Every mutation was caught. After the last revert: `git diff --stat` shows only
`src/utils/credential_value.py` (+11/−2) and `tests/unit/test_one_credential_rule.py` (+7), with
`tests/unit/test_mask_shape_coverage.py` untracked — i.e. the source file is byte-identical to the
implemented version.

## Issues discovered

1. **No existing test contradicts the new rule** — checked explicitly. The one that constrains it is
   `"pat*with*stars*inside"` in `test_one_credential_rule.py::REAL_VALUES` and in
   `test_create_rejects_a_mask.py::TestARealCredentialStillCreatesAnAgent`. It rules out the
   simplest possible rule ("a credential contains no mask character") and forced the proportion
   approach. Flagging it as the coach's decision point: if that value is *not* considered a real
   credential shape by the product, the far stricter zero-mask rule becomes available and
   `••••abcdefgh` gets caught too — but it would then refuse any credential containing `*`.
2. `src/services/admin_db_service.py:394` still wraps the shared predicate in a static method
   `_is_a_real_credential`. It delegates (verified by
   `test_one_credential_rule.py::test_the_service_helper_delegates_rather_than_reimplements`), so it
   is not a second rule — but it is a second *name* for the rule and a place a future edit could
   fork it. Out of scope for this leg (not my file); worth a cleanup leg.
3. `_validate_pat` in `src/vault/client.py:76` still applies only presence/length checks, so a mask
   reaching the vault layer directly would be stored. Everything today routes through
   `is_a_real_credential` first, so this is defence-in-depth, not a live bug. Not my file.
4. `str.strip()` does not remove zero-width characters — worth knowing anywhere else the codebase
   treats `.strip()` as "normalise user input".

## Procedure followed?

Yes.

- Read the one source file and every call site + test that constrains it before writing anything.
- Kept ONE shared predicate; no second rule anywhere; three call sites unchanged.
- Zero comments, zero docstrings in the code written.
- No workaround, no hardcoded list of known-bad strings — the character class is a property test and
  the reveal test is a proportion.
- No existing test weakened or deleted; the only edit to an existing test file is 9 *added* values
  to a refusal list. The one existing test that constrains the rule is reported above, not edited.
- Only `src/utils/credential_value.py` and `tests/` touched.
- Every assertion mutation-checked (5 mutations, all caught), restore verified with `git diff --stat`.
- No live environments, no `.md` or scratchpad committed, branch `feat/wave2-cutover-reconciled`
  (not develop/stage/main).
