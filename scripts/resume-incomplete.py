"""Resume a saved AGH task that ended before using its two allowed corrections."""
import argparse
import re
from traffic_agent import agh, core

parser = argparse.ArgumentParser()
parser.add_argument('job_id')
args = parser.parse_args()
if not re.fullmatch(r'[a-f0-9]{16}', args.job_id):
    parser.error('Invalid job ID')
folder = agh.JOBS / args.job_id
saved = core.read_json(folder / 'status.json')
current = agh.status()['current_job']
if current and current['status'] == 'running':
    raise RuntimeError('Another AGH task is running')
run = core.run_folder(saved['run_id'])
comparison = core.read_json(run / 'comparison.json')
history = core.read_json(run / 'search-history.json')
if comparison['passed'] or len(history) > core.read_json(run / 'input.json')['constraints']['max_retries']:
    raise RuntimeError('No incomplete correction remains')
saved.update(status='running', message='继续剩余修正，使用原 AGH 会话及原始基线')
core.write_json(folder / 'status.json', saved)
core.write_json(agh.JOBS / 'current.json', saved)
agh._execute(saved, folder, '', resume=True)
print(core.read_json(folder / 'status.json')['message'])
