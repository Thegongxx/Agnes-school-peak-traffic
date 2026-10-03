from __future__ import annotations

import csv
import io
import json
import mimetypes
import os
import sys
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from . import agh, core

WEB = core.ROOT / 'web'


def state(scenario_id=''):
    result = {'runtime': agh.status(), 'scenarios': core.read_json(core.ROOT / 'config/scenarios.json'), 'constraints': core.constraints(), 'latest': None, 'calls': [], 'agh_trace': None}
    latest_file = core.ROOT / '.runtime/latest.json'
    run_id = core.read_json(latest_file)['run_id'] if latest_file.exists() else ''
    if scenario_id:
        matches = []
        for input_file in core.RUNS.glob('*/input.json'):
            if core.read_json(input_file)['scenario']['id'] == scenario_id:
                matches.append(input_file)
        run_id = max(matches, key=lambda p: p.stat().st_mtime).parent.name if matches else ''
    if run_id:
        folder = core.run_folder(run_id)
        latest = {'run_id': run_id, 'input': core.read_json(folder / 'input.json')}
        for name in ('baseline', 'selected', 'comparison'):
            path = folder / f'{name}.json'
            if path.exists():
                latest[name] = core.summarise_result(core.read_json(path))
        if (folder / 'search-history.json').exists():
            latest['search_history'] = core.read_json(folder / 'search-history.json')
        result['latest'] = latest
        request_id = latest['input'].get('request_id', '')
        if len(request_id) == 16 and all(c in '0123456789abcdef' for c in request_id):
            job = agh.JOBS / request_id
            if (job / 'status.json').exists():
                trace = {'job': core.read_json(job / 'status.json'), 'session_id': None, 'events': [], 'model': None, 'summary': ''}
                if (job / 'session.json').exists():
                    trace['session_id'] = core.read_json(job / 'session.json')['session_id']
                if (job / 'agh-events.jsonl').exists():
                    for line in (job / 'agh-events.jsonl').read_text(encoding='utf-8').splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue  # A streaming writer may not have finished its last line.
                        detail = event.get('data', {})
                        if event['type'] == 'request/header':
                            trace['model'] = detail.get('model')
                        if event['type'] in ('tool/call', 'approval/asked', 'approval/decided', 'verifier/signal', 'turn/end'):
                            trace['events'].append({'seq': event['seq'], 'type': event['type'], 'time': event.get('ts'), 'name': detail.get('name'), 'tool_use_id': detail.get('toolUseId'), 'reason': detail.get('reason')})
                        if event['type'] == 'assistant/message':
                            trace['summary'] = '\n'.join(c.get('text', '') for c in detail.get('content', []) if c.get('type') == 'text')
                result['agh_trace'] = trace
        if core.CALLS.exists():
            for line in core.CALLS.read_text(encoding='utf-8').splitlines()[-300:]:
                event = json.loads(line)
                event_run = event.get('result', {}).get('run_id') or event.get('arguments', {}).get('run_id')
                if event_run == run_id:
                    result['calls'].append(event)
    return result


def export_report(run_id):
    folder = core.run_folder(run_id)
    memory = io.BytesIO()
    with zipfile.ZipFile(memory, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in folder.rglob('*'):
            if path.is_file():
                archive.write(path, 'run/' + path.relative_to(folder).as_posix())
        for path in (core.ROOT / 'sumo').glob('*'):
            if path.is_file():
                archive.write(path, 'sumo/' + path.name)
        if (folder / 'agh-evidence.json').exists():
            evidence = core.read_json(folder / 'agh-evidence.json')
            native_trace = agh.JOBS / evidence['job_id'] / 'agh-session.jsonl'
            if native_trace.exists():
                archive.write(native_trace, 'agh-native-session.jsonl')
            for name in ('agh-events.jsonl', 'approvals.jsonl', 'status.json', 'prompt.txt'):
                path = agh.JOBS / evidence['job_id'] / name
                if path.exists():
                    archive.write(path, 'agh/' + name)
        if core.CALLS.exists():
            events = [json.loads(line) for line in core.CALLS.read_text(encoding='utf-8').splitlines()]
            events = [e for e in events if (e.get('result', {}).get('run_id') or e.get('arguments', {}).get('run_id')) == run_id]
            archive.writestr('tool-receipts.jsonl', '\n'.join(json.dumps(e, ensure_ascii=False) for e in events))
        if (folder / 'comparison.json').exists():
            comp = core.read_json(folder / 'comparison.json')
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(['metric', 'baseline', 'candidate', 'difference'])
            for key, diff in comp['differences'].items():
                writer.writerow([key, comp['baseline']['metrics'][key], comp['candidate']['metrics'][key], diff])
            archive.writestr('comparison.csv', '\ufeff' + out.getvalue())
            trace_note = '本包包含 AGH 原生会话与逐次审批记录。' if 'agh-native-session.jsonl' in archive.namelist() else '本次仅有工具验算记录，尚无 AGH 原生会话证据。'
            archive.writestr('report.md', f"# 校园路口仿真比较\n\n数据来源：{comp['data_source']}\n\n引擎：SUMO / TraCI\n\n运行编号：{run_id}\n\n校验状态：{comp['status']}\n\n车辆平均延误改善：{comp['improvement_percent']}%\n\n失败原因：{'; '.join(comp['reasons']) or '无'}\n\n完整输入与指标见 run/input.json 和 run/comparison.json。{trace_note}\n")
    return memory.getvalue()


class Handler(BaseHTTPRequestHandler):
    def json_response(self, value, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        try:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == '/api/health':
                return self.json_response({'service': 'campus-signal-dashboard', 'root': str(core.ROOT)})
            if url.path == '/api/state':
                return self.json_response(state(query.get('scenario_id', [''])[0]))
            if url.path == '/api/replay':
                folder = core.run_folder(query.get('run_id', [''])[0])
                name = query.get('plan', ['baseline'])[0]
                if name not in ('baseline', 'selected'):
                    raise core.InputError('未知方案')
                return self.json_response(core.read_json(folder / f'{name}.json'))
            if url.path == '/api/export':
                run_id = query.get('run_id', [''])[0]
                data = export_report(run_id)
                self.send_response(200)
                self.send_header('Content-Type', 'application/zip')
                self.send_header('Content-Disposition', f'attachment; filename=campus-traffic-{run_id}.zip')
                self.end_headers()
                self.wfile.write(data)
                return
            file = (WEB / (url.path.lstrip('/') or 'index.html')).resolve()
            if not file.is_relative_to(WEB.resolve()) or not file.is_file():
                return self.send_error(404)
            self.send_response(200)
            content_type = mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
            self.send_header('Content-Type', content_type + '; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(file.read_bytes())
        except Exception as exc:
            self.json_response({'error': str(exc)}, 400)

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin not in ('http://127.0.0.1:8765', 'http://localhost:8765'):
                return self.json_response({'error': '请求来源不允许'}, 403)
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                raise core.InputError('请求大小无效')
            body = json.loads(self.rfile.read(size))
            if self.path == '/api/run':
                return self.json_response(agh.start_job(body.get('scenario_id', '')), 202)
            if self.path == '/api/approval':
                return self.json_response(agh.answer_approval(body.get('job_id'), body.get('approval_id'), body.get('option_id')))
            self.send_error(404)
        except Exception as exc:
            self.json_response({'error': str(exc)}, 400)

    def log_message(self, format, *args):
        if '/api/' not in str(args[0]):
            sys.stderr.write((format % args) + '\n')


if __name__ == '__main__':
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    print('校园路口仪表板：http://127.0.0.1:8765', flush=True)
    server.serve_forever()
