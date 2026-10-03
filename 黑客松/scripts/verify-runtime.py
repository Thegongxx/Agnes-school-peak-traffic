"""Check the running project and archived actual AGH results without creating model requests."""
import io
import json
import urllib.request
import zipfile
from traffic_agent import core

checks = []
def record(name, passed, detail=''):
    checks.append({'name': name, 'passed': bool(passed), 'detail': detail})
    print(json.dumps(checks[-1], ensure_ascii=False), flush=True)

for port in (8765, 4177):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/', timeout=5) as response:
        record(f'http_{port}', response.status == 200)
with urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=5) as response:
    health = json.load(response)
    record('project_service_identity', health == {'service': 'campus-signal-dashboard', 'root': str(core.ROOT)})

completed = []
for path in (core.ROOT / '.runtime/jobs').glob('*/status.json'):
    job = core.read_json(path)
    if job.get('status') != 'completed' or not job.get('run_id'):
        continue
    folder = core.run_folder(job['run_id'])
    comp = core.read_json(folder / 'comparison.json')
    events = [json.loads(line) for line in (path.parent / 'agh-events.jsonl').read_text(encoding='utf-8').splitlines()]
    calls = [e['data']['name'].rsplit('_735753bc_', 1)[-1] for e in events if e['type'] == 'tool/call']
    required = {'load_scenario', 'run_baseline', 'search_timing_plan', 'run_candidate', 'verify_and_compare'}
    record(f"agh_{job['scenario_id']}", required.issubset(calls) and any(e['type'] == 'turn/end' for e in events) and job.get('native_trace_exported'), {'job_id': job['job_id'], 'run_id': job['run_id'], 'accepted': comp['passed'], 'improvement_percent': comp['improvement_percent']})
    with urllib.request.urlopen('http://127.0.0.1:8765/api/export?run_id=' + job['run_id'], timeout=20) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
        expected = {'run/input.json', 'run/comparison.json', 'comparison.csv', 'report.md', 'agh-native-session.jsonl', 'agh/approvals.jsonl'}
        record('export_' + job['scenario_id'], expected.issubset(archive.namelist()) and archive.testzip() is None)
    history = core.read_json(folder / 'search-history.json')
    record('bounded_retries_' + job['scenario_id'], len(history) <= comp['baseline']['constraints']['max_retries'] + 1 if 'constraints' in comp['baseline'] else len(history) <= 3, len(history))
    completed.append(job['scenario_id'])
record('at_least_one_real_AGH_run', bool(completed), completed)
try:
    request = urllib.request.Request('http://127.0.0.1:8765/api/run', data=b'{"scenario_id":"peak"}', headers={'Origin':'https://untrusted.example','Content-Type':'application/json'})
    urllib.request.urlopen(request, timeout=5)
    record('reject_external_origin', False)
except urllib.error.HTTPError as exc:
    record('reject_external_origin', exc.code == 403)
record('usage_document', (core.ROOT / '使用说明.md').is_file())
summary = {'created_at': core.utc_now(), 'checks': checks, 'all_checks_passed': all(c['passed'] for c in checks)}
core.write_json(core.ROOT / '.runtime/validation/runtime.json', summary)
if not summary['all_checks_passed']:
    raise SystemExit(1)
