# Viewer report-generation and PMS job lifecycle.
import io
import json
import os
import subprocess
import threading
import time

from submission import parse_submission, report_date
from viewer_files import safe_join

jobs = {}                   # 보고서 생성 작업. 화면이 주기적으로 물어본다
jobs_lock = threading.Lock()


def made_since(root, area, since):
    """그 시각 뒤에 새로 쓰인 산출물. 빠진 날을 채우는 실행은 여럿을 쓴다."""
    base = os.path.join(root, area)
    out = []
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            if not name.endswith('.md'):
                continue
            full = os.path.join(dirpath, name)
            if os.path.getmtime(full) > since:
                out.append(os.path.relpath(full, root).replace(os.sep, '/'))
    out.sort()
    return out


def newest_under(root, area, since):
    """작업이 끝난 뒤 무엇이 만들어졌는지 찾는다."""
    base = os.path.join(root, area)
    best, best_at = None, since
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            if not name.endswith('.md'):
                continue
            full = os.path.join(dirpath, name)
            at = os.path.getmtime(full)
            if at > best_at:
                best, best_at = full, at
    return best


def job_phase(root, since):
    """지금 어느 대목인지. 러너는 진행을 알려 주지 않으므로 산출물로 읽는다.

    수집이 끝나면 raw\\ 에 그 구간의 파일이 먼저 생기고, 보고서는 그 뒤에
    쓰인다. 초만 세는 카드로는 1~3분 동안 멈춘 것인지 알 수 없다.
    """
    return 'write' if newest_under(root, 'raw', since) else 'collect'


# 러너가 "한 번에 하나만"을 지키려고 두는 자리. 45분은 러너가 낡은 잠금으로
# 보고 치우는 기준과 같은 값이다.
LOCK_STALE = 45 * 60


def scheduled_job(root):
    """작업 스케줄러가 돌리는 실행. 화면이 띄운 것이 아니라 목록에 없다.

    이것이 없으면 17:30에 보고서가 만들어지는 동안 화면은 아무 말도 하지
    않고, 그 사이에 누른 만들기는 "건너뜀"으로만 끝난다.
    """
    lock = os.path.join(root, 'runlog', '.running')
    try:
        started = os.path.getmtime(lock)
    except OSError:
        return None
    if time.time() - started > LOCK_STALE:
        return None
    mode = 'daily'
    try:
        with io.open(lock, encoding='utf-8-sig') as fh:
            parts = fh.read().split()
        if len(parts) > 1 and parts[1] in ('daily', 'weekly'):
            mode = parts[1]
    except (OSError, ValueError):
        pass
    return {'id': 'scheduled', 'mode': mode, 'state': 'running', 'external': True,
            'phase': job_phase(root, started), 'seconds': int(time.time() - started),
            'path': None, 'paths': []}


bins_cache = None
bins_lock = threading.RLock()      # 같은 스레드가 두 번 잠글 수 있게


def resolve_bins(root):
    """실제로 무엇을 실행하게 되는지 러너와 같은 방법으로 찾는다.

    config.json의 *_bin은 자동 탐색이 실패할 때만 적는 값이라 보통 비어 있다.
    그대로 두면 설정 화면이 "아무것도 잡히지 않았다"로 읽힌다.
    찾는 데 몇 초 걸리므로 한 번만 하고 들고 있는다.
    """
    global bins_cache
    # 한 번에 하나만 찾는다. 설정 화면을 빠르게 두 번 열면 파워셸이 두 번 뜬다.
    with bins_lock:
        if bins_cache is not None:
            return bins_cache
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resolve-bins.ps1')
        found = {}
        try:
            done = subprocess.run(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', script, '-Root', root],
                cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=90,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            found = json.loads(done.stdout.decode('utf-8', 'replace').strip() or '{}')
        except Exception:
            found = {}
        bins_cache = found
        return found


def start_job(root, mode):
    """러너를 띄운다. 모드는 둘 중 하나로 고정한다.

    이미 도는 것이 있으면 그 작업을 돌려준다. 러너는 한 번에 하나만 돌도록
    잠금을 걸어 두었으므로, 두 번째를 띄워도 아무것도 쓰지 않고 물러난다.
    단추를 연타하면 그 물러난 실행이 화면에 카드로 남는다.
    """
    if mode not in ('daily', 'weekly'):
        return None
    with jobs_lock:
        for job in jobs.values():
            if job['mode'] == mode and job['state'] == 'running' and job['proc'].poll() is None:
                return job['id']
    runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'run-report.ps1')
    started = time.time()
    proc = subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
         '-WindowStyle', 'Hidden', '-File', runner, '-Mode', mode, '-NoToast'],
        cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    job_id = '%s-%d' % (mode, int(started * 1000))
    with jobs_lock:
        jobs[job_id] = {'id': job_id, 'mode': mode, 'started': started,
                        'proc': proc, 'state': 'running', 'path': None, 'paths': []}
    return job_id


PMS_STATE = {0: 'done', 10: 'opened', 2: 'login', 3: 'config', 4: 'playwright', 5: 'env'}


def start_pms(root, rel, mode='fill'):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pms.ps1')
    logdir = os.path.join(root, 'runlog', time.strftime('%Y-%m'))
    if not os.path.isdir(logdir):
        os.makedirs(logdir)
    log = os.path.join(logdir, 'pms.log')
    argv = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script]
    rows = None
    if mode == 'login':
        argv.append('-Login')
    elif mode == 'fetch':
        argv.append('-Fetch')
    else:
        target = safe_join(root, rel) if rel else None
        if not target:
            return None, '보고서를 찾지 못했습니다'
        with io.open(target, encoding='utf-8') as fh:
            parsed = parse_submission(fh.read())
        parsed['date'] = report_date(rel)
        folder = os.path.join(root, 'pms', 'rows')
        if not os.path.isdir(folder):
            os.makedirs(folder)
        rows = os.path.join(folder, parsed['date'] + '.json')
        with io.open(rows, 'w', encoding='utf-8', newline='') as fh:
            fh.write(json.dumps(parsed, ensure_ascii=False, indent=1))
        argv += ['-Fill', rows]
    started = time.time()
    fh = open(log, 'ab')
    head = '[%s] %s %s' % (time.strftime('%H:%M:%S'), mode, rel or '')
    fh.write(head.encode('utf-8') + os.linesep.encode('ascii'))
    proc = subprocess.Popen(argv, cwd=root, stdout=fh, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    job_id = 'pms-%s-%d' % (mode, int(started * 1000))
    with jobs_lock:
        jobs[job_id] = {'id': job_id, 'mode': 'pms' if mode == 'fill' else 'pms-' + mode,
                        'started': started, 'proc': proc, 'state': 'running',
                        'path': None, 'paths': [], 'log': log, 'note': []}
    return job_id, None


def pms_note(log):
    """스크립트가 마지막에 한 말. 카드에 그대로 보여 준다 - 안내가 로그에만
    남으면 사람은 보지 않는다."""
    try:
        with io.open(log, encoding='utf-8', errors='replace') as fh:
            lines = [l.rstrip() for l in fh if l.strip()]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        if line.startswith('['):
            break
        out.append(line)
    return list(reversed(out))[-6:]


def drop_job(job_id):
    """닫은 작업을 목록에서 뺀다.

    화면에서만 지우면 다음 폴링이 서버 목록을 보고 카드를 다시 만든다.
    도는 중인 작업은 닫지 않는다 - 지켜볼 것이 남아 있다.
    """
    with jobs_lock:
        job = jobs.get(job_id)
        if not job or job['state'] == 'running':
            return False
        del jobs[job_id]
        return True


def job_status(root):
    out = []
    mine_running = False
    with jobs_lock:
        for job in jobs.values():
            if job['state'] == 'running':
                code = job['proc'].poll()
                pms = job['mode'].startswith('pms')
                if code is None:
                    # PMS 채우기는 보고서를 쓰는 실행이 아니다. 예약 실행 줄을
                    # 가리는 판단에 끼어들지 않게 여기서는 세지 않는다.
                    if not pms:
                        mine_running = True
                elif pms:
                    job['state'] = PMS_STATE.get(code, 'failed')
                    job['note'] = pms_note(job.get('log'))
                else:
                    made = made_since(root, job['mode'], job['started'])
                    # 2는 러너가 "다른 실행이 도는 중이라 물러났다"고 말하는 값이다.
                    # 쓴 것이 없다는 점은 실패와 같지만 실패가 아니다.
                    if code == 2 and not made:
                        job['state'] = 'skipped'
                    else:
                        job['state'] = 'done' if (code == 0 and made) else 'failed'
                    job['paths'] = made
                    job['path'] = made[0] if made else None
            row = {'id': job['id'], 'mode': job['mode'], 'state': job['state'],
                   'seconds': int(time.time() - job['started']),
                   'path': job['path'], 'paths': job.get('paths') or [],
                   'note': job.get('note') or []}
            if job['state'] == 'running':
                row['phase'] = job_phase(root, job['started'])
            out.append(row)
    # 내가 띄운 실행이 도는 중이면 잠금도 그것의 것이라 두 번 보일 이유가 없다
    if not mine_running:
        ext = scheduled_job(root)
        if ext:
            out.append(ext)
    return out
