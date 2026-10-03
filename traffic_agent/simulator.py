from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from .core import ROOT, DIRECTIONS, InputError, constraints, cycle_seconds, read_json, validate_plan

_simulation_lock = threading.RLock()


def sumo_paths():
    import sumo
    home = Path(sumo.SUMO_HOME)
    suffix = '.exe' if os.name == 'nt' else ''
    return home, home / 'bin' / ('sumo' + suffix), home / 'bin' / ('netconvert' + suffix)


def sumo_environment():
    home, _, _ = sumo_paths()
    env = os.environ.copy()
    relative = home.relative_to(ROOT).as_posix()
    env.update(SUMO_HOME=relative, PROJ_LIB=relative + '/data/proj', PROJ_DATA=relative + '/data/proj')
    return env


def build_network():
    _, _, netconvert = sumo_paths()
    target = ROOT / 'sumo' / 'campus.net.xml'
    sources = [ROOT / 'sumo' / ('campus.' + ext + '.xml') for ext in ('nod', 'edg', 'con')]
    if target.exists() and target.stat().st_mtime >= max(p.stat().st_mtime for p in sources):
        return target
    command = [netconvert.relative_to(ROOT).as_posix(), '--node-files', sources[0].relative_to(ROOT).as_posix(), '--edge-files', sources[1].relative_to(ROOT).as_posix(), '--connection-files', sources[2].relative_to(ROOT).as_posix(), '--walkingareas', 'true', '--no-turnarounds', 'true', '--xml-validation', 'never', '--output-file', target.relative_to(ROOT).as_posix()]
    proc = subprocess.run(command, executable=str(netconvert), env=sumo_environment(), cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if proc.returncode:
        raise RuntimeError('SUMO 路网生成失败：' + proc.stderr[-3000:])
    return target


def network_signals(net):
    root = ET.parse(net).getroot()
    tl = root.find("tlLogic[@id='J']")
    if tl is None:
        raise RuntimeError('路网中没有 J 信号灯')
    length = len(tl.find('phase').attrib['state'])
    links = list(root.findall("connection[@tl='J']"))
    crossing_edges = {e.attrib['id'] for e in root.findall("edge[@function='crossing']")}
    crossing_length = max(float(l.attrib['length']) for e in root.findall("edge[@function='crossing']") for l in e.findall('lane'))
    center = root.find("junction[@id='J']")
    groups = {'ns': set(), 'ew': set(), 'ped': set()}
    for link in links:
        origin = link.attrib['from']
        target = link.attrib['to']
        group = 'ns' if origin in ('n_in', 's_in') else 'ew' if origin in ('e_in', 'w_in') else 'ped' if origin in crossing_edges or target in crossing_edges else None
        if group:
            groups[group].add(int(link.attrib['linkIndex']))
            if 'linkIndex2' in link.attrib:
                groups[group].add(int(link.attrib['linkIndex2']))
    if any(not indexes for indexes in groups.values()):
        raise RuntimeError('信号连接分组不完整：' + str(groups))
    if (groups['ns'] & groups['ew']) or ((groups['ns'] | groups['ew']) & groups['ped']):
        raise RuntimeError('信号分组发生冲突')
    def state(group, symbol='G'):
        return ''.join(symbol if i in groups.get(group, ()) else 'r' for i in range(length))
    return {
        'ns_green': state('ns'), 'ns_yellow': state('ns', 'y'),
        'ew_green': state('ew'), 'ew_yellow': state('ew', 'y'),
        'pedestrian_walk': state('ped'), 'all_red': 'r' * length,
        'crossing_edges': crossing_edges, 'crossing_length': crossing_length,
        'groups': {key: sorted(value) for key, value in groups.items()},
        'center': (float(center.attrib['x']), float(center.attrib['y'])),
    }


def simulate(scenario, plan, folder: Path, capture=False, timeout_seconds=180):
    with _simulation_lock:
        return _simulate(scenario, plan, folder, capture, timeout_seconds)


def _simulate(scenario, plan, folder, capture, timeout_seconds):
    import traci
    import traci.constants as tc
    rules = constraints()
    legality = validate_plan(plan, rules)
    if not legality['passed']:
        raise InputError('仿真前拒绝非法配时：' + '; '.join(legality['errors']))
    net = build_network()
    signals = network_signals(net)
    required_clearance = signals['crossing_length'] / 1.3 + 2
    if rules['pedestrian_clearance_seconds'] < required_clearance:
        raise InputError(f'行人清空时间不足：当前路网至少需要 {required_clearance:.1f} 秒（项目步行速度假设）')
    manifest = read_json(folder / 'demand.json')
    _, binary, _ = sumo_paths()
    label = 'traffic-' + folder.name + '-' + str(time.monotonic_ns())
    runpath = folder / ('sumo-' + plan['id'])
    runpath.mkdir(exist_ok=True)
    command = [binary.relative_to(ROOT).as_posix(), '--net-file', net.relative_to(ROOT).as_posix(), '--route-files', (folder / 'demand.rou.xml').relative_to(ROOT).as_posix(), '--seed', str(scenario['seed']), '--step-length', '1', '--no-step-log', 'true', '--duration-log.disable', 'true', '--xml-validation', 'never', '--time-to-teleport', '-1', '--collision.action', 'warn', '--tripinfo-output', (runpath / 'tripinfo.xml').relative_to(ROOT).as_posix(), '--tripinfo-output.write-unfinished', 'true', '--error-log', (runpath / 'warnings.log').relative_to(ROOT).as_posix(), '--pedestrian.striping.dawdling', '0']
    (runpath / 'command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding='utf-8')
    start = time.monotonic()
    conn = None
    process = None
    stderr_file = (runpath / 'process-stderr.log').open('w', encoding='utf-8')
    # Phases explicitly prohibit concurrent vehicle and crossing greens.
    phases = [
        ('ns_green', plan['ns_green']), ('ns_yellow', rules['yellow_seconds']), ('all_red', rules['all_red_seconds']),
        ('ew_green', plan['ew_green']), ('ew_yellow', rules['yellow_seconds']), ('all_red', rules['all_red_seconds']),
        ('pedestrian_walk', plan['pedestrian_walk']), ('all_red', rules['pedestrian_clearance_seconds']),
    ]
    vehicle_loss = {item_id: 0. for item_id in manifest['vehicles']}
    vehicle_wait = {item_id: 0. for item_id in manifest['vehicles']}
    person_wait = {item_id: 0. for item_id in manifest['persons']}
    departed_vehicles, departed_persons, arrived_vehicles, arrived_persons = set(), set(), set(), set()
    queue_max = dict.fromkeys(DIRECTIONS, 0)
    throughput, collisions, extension_seconds = 0, 0, 0
    timeline, snapshots = [], []
    end = scenario['duration_seconds'] + scenario['drain_seconds']
    phase_index, phase_end = 0, phases[0][1]
    try:
        from sumolib.miscutils import getFreeSocketPort
        port = getFreeSocketPort()
        process = subprocess.Popen(command + ['--remote-port', str(port)], executable=str(binary), env=sumo_environment(), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=stderr_file, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            conn = traci.connect(port=port, host='127.0.0.1', proc=process, numRetries=5)
        except Exception as exc:
            stderr_file.flush()
            detail = (runpath / 'process-stderr.log').read_text(encoding='utf-8', errors='replace')
            raise RuntimeError(f'SUMO 启动失败（退出码 {process.poll()}）：{detail[-2000:]}') from exc
        conn.trafficlight.setRedYellowGreenState('J', signals[phases[0][0]])
        for direction in DIRECTIONS:
            conn.lane.subscribe(direction.lower() + '_in_1', [tc.LAST_STEP_VEHICLE_HALTING_NUMBER])
        while conn.simulation.getTime() < end:
            if time.monotonic() - start > timeout_seconds:
                raise TimeoutError(f'SUMO 仿真超过 {timeout_seconds} 秒，结果未记为成功')
            t = int(conn.simulation.getTime())
            if t >= phase_end:
                next_index = (phase_index + 1) % len(phases)
                if phases[next_index][0] in ('ns_green', 'ew_green'):
                    occupying = any(v.get(tc.VAR_ROAD_ID) in signals['crossing_edges'] for v in conn.person.getAllSubscriptionResults().values())
                    if occupying:
                        phase_end += 1
                        extension_seconds += 1
                    else:
                        phase_index = next_index
                        phase_end = t + phases[phase_index][1]
                        conn.trafficlight.setRedYellowGreenState('J', signals[phases[phase_index][0]])
                else:
                    phase_index = next_index
                    phase_end = t + phases[phase_index][1]
                    conn.trafficlight.setRedYellowGreenState('J', signals[phases[phase_index][0]])
            conn.simulationStep()
            t = int(conn.simulation.getTime())
            collisions += conn.simulation.getCollidingVehiclesNumber()
            for item_id in conn.simulation.getDepartedIDList():
                departed_vehicles.add(item_id)
                conn.vehicle.subscribe(item_id, [tc.VAR_TIMELOSS, tc.VAR_SPEED, tc.VAR_POSITION, tc.VAR_ROAD_ID])
                vehicle_loss[item_id] = max(0., t - manifest['vehicles'][item_id]['depart'])
            for item_id in conn.simulation.getDepartedPersonIDList():
                departed_persons.add(item_id)
                conn.person.subscribe(item_id, [tc.VAR_SPEED, tc.VAR_POSITION, tc.VAR_ROAD_ID])
            arrived_vehicles.update(conn.simulation.getArrivedIDList())
            arrived_persons.update(conn.simulation.getArrivedPersonIDList())
            if t <= scenario['duration_seconds']:
                throughput += len(conn.simulation.getArrivedIDList())
            vehicle_results = conn.vehicle.getAllSubscriptionResults()
            person_results = conn.person.getAllSubscriptionResults()
            for item_id, values in vehicle_results.items():
                vehicle_loss[item_id] = values[tc.VAR_TIMELOSS]
                if values[tc.VAR_SPEED] < .1:
                    vehicle_wait[item_id] += 1
            for item_id, values in person_results.items():
                if values[tc.VAR_SPEED] < .1:
                    person_wait[item_id] += 1
            queues = {d: int(conn.lane.getSubscriptionResults(d.lower() + '_in_1')[tc.LAST_STEP_VEHICLE_HALTING_NUMBER]) for d in DIRECTIONS}
            for d, q in queues.items():
                queue_max[d] = max(queue_max[d], q)
            if t % 2 == 0:
                timeline.append({'time': t, 'queues': queues, 'phase': phases[phase_index][0], 'countdown': max(0, phase_end - t), 'arrived': len(arrived_vehicles)})
                if capture:
                    snapshots.append({'time': t, 'phase': phases[phase_index][0], 'countdown': max(0, phase_end - t), 'vehicles': [{'id': i, 'x': round(v[tc.VAR_POSITION][0] - signals['center'][0], 2), 'y': round(v[tc.VAR_POSITION][1] - signals['center'][1], 2), 'speed': round(v[tc.VAR_SPEED], 2)} for i, v in vehicle_results.items()], 'persons': [{'id': i, 'x': round(v[tc.VAR_POSITION][0] - signals['center'][0], 2), 'y': round(v[tc.VAR_POSITION][1] - signals['center'][1], 2)} for i, v in person_results.items()]})
            if t >= scenario['duration_seconds'] and conn.simulation.getMinExpectedNumber() == 0:
                break
        final_time = int(conn.simulation.getTime())
        version = conn.getVersion()[1]
    finally:
        if conn is not None:
            conn.close(wait=True)
        elif process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        stderr_file.close()
    # SUMO tripinfo persists complete timeLoss, insertion delay, and unfinished trips.
    for info in ET.parse(runpath / 'tripinfo.xml').getroot().findall('tripinfo'):
        item_id = info.attrib['id']
        vehicle_loss[item_id] = float(info.attrib['timeLoss']) + float(info.attrib.get('departDelay', '0'))
        vehicle_wait[item_id] = float(info.attrib['waitingTime']) + float(info.attrib.get('departDelay', '0'))
    for item_id in set(manifest['vehicles']) - departed_vehicles:
        vehicle_loss[item_id] = max(0., final_time - manifest['vehicles'][item_id]['depart'])
        vehicle_wait[item_id] = vehicle_loss[item_id]
    for item_id in set(manifest['persons']) - departed_persons:
        person_wait[item_id] = max(0., final_time - manifest['persons'][item_id]['depart'])
    approach_wait = {d: round(max((vehicle_wait[i] for i, item in manifest['vehicles'].items() if item['direction'] == d), default=0), 4) for d in DIRECTIONS}
    def mean(values):
        return round(sum(values) / len(values), 4) if values else 0.
    metrics = {
        'mean_vehicle_delay': mean(list(vehicle_loss.values())), 'mean_vehicle_wait': mean(list(vehicle_wait.values())),
        'mean_pedestrian_wait': mean(list(person_wait.values())), 'max_pedestrian_wait': round(max(person_wait.values(), default=0), 4),
        'max_queue': max(queue_max.values()), 'approach_max_queue': queue_max, 'approach_max_wait': approach_wait,
        'throughput': throughput, 'arrived_vehicles': len(arrived_vehicles), 'vehicle_count': len(manifest['vehicles']),
        'pedestrian_count': len(manifest['persons']), 'arrived_persons': len(arrived_persons),
        'unfinished_vehicles': len(manifest['vehicles']) - len(arrived_vehicles), 'unfinished_persons': len(manifest['persons']) - len(arrived_persons),
        'collision_count': collisions, 'pedestrian_clearance_extension_seconds': extension_seconds,
    }
    return {
        'engine': 'SUMO / TraCI', 'sumo_version': version, 'scenario_id': scenario['id'], 'seed': scenario['seed'], 'plan': plan,
        'planned_cycle_seconds': cycle_seconds(plan, rules), 'metrics': metrics, 'simulation_seconds': final_time,
        'demand_sha256': manifest['sha256'], 'network_sha256': hashlib.sha256(net.read_bytes()).hexdigest(),
        'metric_definition': '车辆延误=SUMO tripinfo timeLoss+departDelay，分母包含全部生成需求；行人等待=SUMO person 静止秒数；通过量=前600秒完成车辆。',
        'safety': {'fixed_yellow': rules['yellow_seconds'], 'fixed_all_red': rules['all_red_seconds'], 'pedestrian_clearance_minimum': rules['pedestrian_clearance_seconds'], 'crossing_length': round(signals['crossing_length'], 3), 'occupancy_interlock': True, 'signal_groups': signals['groups']},
        'timeseries': timeline, 'snapshots': snapshots,
    }
