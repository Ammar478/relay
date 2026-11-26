"""Regressions observed during real Relay runs."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import relay_log
import relay_services
import relay_decide
from test_relay_execution import staged, model_of


@pytest.mark.parametrize('value', ['42854e2', '1234567', 'true', 'null'])
@pytest.mark.parametrize('key', ['commit', 'commitId', 'sessionId', 'legId'])
def test_opaque_identifiers_survive_json_like_values(key, value):
    assert relay_log._parse_fields([f'{key}={value}'])[key] == value


def test_other_fields_keep_structured_types():
    assert relay_log._parse_fields(['count=3', 'success=true', 'items=[1,2]',
                                    'commit="42854e2"']) == {
        'count': 3, 'success': True, 'items': [1, 2], 'commit': '42854e2'}


def test_commit_survives_actual_append_and_tail(tmp_path):
    script = str(ROOT / 'scripts/relay_log.py')
    subprocess.run([sys.executable, script, 'append', 'leg_completed',
                    '--relay-dir', str(tmp_path), '--field', 'commit=42854e2'],
                   check=True, capture_output=True, text=True)
    result = subprocess.run([sys.executable, script, 'tail', '--relay-dir', str(tmp_path)],
                            check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)['commit'] == '42854e2'
    assert json.loads((tmp_path / 'progress.jsonl').read_text())['commit'] == '42854e2'


def test_empty_services_template_works_with_real_cli(tmp_path):
    text = (ROOT / 'templates/services.yaml').read_text()
    services, errors = relay_services.parse(text)
    assert services == {} and errors == []
    (tmp_path / 'services.yaml').write_text(text)
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/relay_services.py'),
                             'check', '--relay-dir', str(tmp_path)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_verdict_template_passes_gate_only_after_real_ids_are_filled(tmp_path):
    verdict = json.loads((ROOT / 'templates/check-verdict.json').read_text())
    relay = staged(tmp_path, verdict)
    assert relay_decide.dispatch(model_of(relay), scope_revision=1)['blockers']
    verdict.update(markedBy='judge', evidence='evidence/check.txt', verifiedLegs=['build'])
    from test_relay_execution import state, CID
    state(relay, checks={CID: verdict})
    assert relay_decide.dispatch(model_of(relay), scope_revision=1)['dispatch'] == ['next']
