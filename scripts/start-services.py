"""Start only this project's local processes, with local runtimes and logs."""
import argparse
import json
import os
import subprocess
import time
import urllib.request
from traffic_agent import agh, core

parser = argparse.ArgumentParser()
parser.add_argument('--dashboard-only', action='store_true')
args = parser.parse_args()
runtime = core.ROOT / '.runtime'
runtime.mkdir(exist_ok=True)
(core.ROOT / '.cache/temp').mkdir(parents=True, exist_ok=True)
processes_file = runtime / 'services.json'
previous = core.read_json(processes_file) if processes_file.exists() else {}
processes = dict(previous)

def reachable(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=2) as response:
            return response.status == 200
    except Exception:
        return False

def start(name, port, command):
    if reachable(port):
        if name == 'dashboard':
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
                identity = json.load(response)
            if identity != {'service': 'campus-signal-dashboard', 'root': str(core.ROOT)}:
                raise RuntimeError(f'Port {port} belongs to a different service')
        elif previous.get(name, {}).get('command') != command:
            raise RuntimeError(f'Port {port} is occupied without this project service record')
        print(f'{name}: http://127.0.0.1:{port} (already available)', flush=True)
        return
    out = (runtime / f'{name}-stdout.log').open('a', encoding='utf-8')
    err = (runtime / f'{name}-stderr.log').open('a', encoding='utf-8')
    process = subprocess.Popen(command, cwd=core.ROOT, env=agh.environment(), stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
    out.close()
    err.close()
    processes[name] = {'pid': process.pid, 'port': port, 'started_at': core.utc_now(), 'command': command}
    core.write_json(processes_file, processes)
    for _ in range(60):
        if reachable(port):
            print(f'{name}: http://127.0.0.1:{port}', flush=True)
            return
        if process.poll() is not None:
            raise RuntimeError(f'{name} exited with {process.returncode}; see .runtime/{name}-stderr.log')
        time.sleep(.5)
    raise RuntimeError(f'{name} did not become ready; see .runtime/{name}-stderr.log')

start('dashboard', 8765, [str(agh.PYTHON), '-m', 'traffic_agent.dashboard'])
if not args.dashboard_only:
    if not agh.ENTRY.exists():
        raise RuntimeError('AGH is not built yet; run scripts/build-agh.ps1')
    start('agh-web', 4177, [str(agh.NODE), str(agh.ENTRY), 'serve'])
