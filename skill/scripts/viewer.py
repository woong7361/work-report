# -*- coding: utf-8 -*-
"""보고서를 브라우저에서 읽고 고치고, 새로 만들고, 제출하러 갈 수 있게 연다.

`.md`를 여는 프로그램은 PC마다 다르고, 메모장으로 열리면 표가 깨진다.
그래서 브라우저로 연다. 다만 브라우저는 로컬 파일을 덮어쓰지 못하므로
저장을 받을 서버를 127.0.0.1에만 띄운다. 바깥에서는 접근할 수 없다.

    python viewer.py <보고서.md>
    python viewer.py <보고 폴더>      # 가장 최근 보고서를 연다
"""
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, unquote

import secrets as secret_store
from _log import log_error

IDLE_TIMEOUT = 2 * 60 * 60  # 이 시간 동안 아무 요청이 없으면 스스로 종료한다
last_seen = time.time()

# 왼쪽 목록에 보여줄 산출물과 이름
AREAS = (('daily', '일일 보고'), ('weekly', '주간 보고'),
         ('log', '한 일 목록'), ('raw', '수집 원본'))

# 보고 폴더의 custom\ 에 두면 보고서 양식과 문체를 바꾼다. 업데이트가 덮어쓰지 않는다.
CUSTOM = (('report-format.md', '보고서 양식', 'custom_format'),
          ('writing-rules.md', '글쓰기 문체', 'custom_rules'),
          ('my-reports.md', '내 보고서', 'custom_samples'))

EMPTY_DOC = ('# 아직 보고서가 없습니다' + chr(10) + chr(10) +
             '위의 **일일보고 만들기** 를 눌러 보세요.')

jobs = {}                   # 보고서 생성 작업. 화면이 주기적으로 물어본다
jobs_lock = threading.Lock()


def load_config(report_path):
    """보고 폴더의 config.json을 찾는다. 보고서는 <root>/<종류>/<월>/ 아래에 있다."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(report_path))))
    try:
        with open(os.path.join(root, 'config.json'), encoding='utf-8-sig') as fh:
            return json.load(fh), root
    except (OSError, ValueError):
        return {}, root


def list_reports(root):
    """산출물을 종류 → 월로 묶는다. 쌓여도 한 화면에 펼쳐지지 않게 한다."""
    groups = []
    for area, label in AREAS:
        base = os.path.join(root, area)
        if not os.path.isdir(base):
            continue
        months = []
        for month in sorted(os.listdir(base), reverse=True):
            mdir = os.path.join(base, month)
            if not os.path.isdir(mdir):
                continue
            items = [{'name': n[:-3], 'path': '%s/%s/%s' % (area, month, n)}
                     for n in sorted(os.listdir(mdir), reverse=True) if n.endswith('.md')]
            if items:
                months.append({'month': month, 'items': items})
        if months:
            groups.append({'area': area, 'label': label, 'months': months})
    return groups


def list_custom(root):
    """내 양식. 쓸지 말지는 설정의 토글이 정하고, 파일은 그때 생긴다."""
    cfg = read_config(root)
    out = []
    for name, label, flag in CUSTOM:
        path = os.path.join(root, 'custom', name)
        out.append({'name': label,
                    'path': 'custom/' + name,
                    'mine': bool(cfg.get(flag)),
                    'exists': os.path.isfile(path)})
    return out


def seed_custom(root, name):
    """기본값을 custom 폴더로 복사한다. 이미 있으면 건드리지 않는다."""
    if name not in [c[0] for c in CUSTOM]:
        return None
    target = os.path.join(root, 'custom', name)
    if os.path.isfile(target):
        return 'custom/' + name
    src = asset_sibling('templates', name)
    if not src:
        return None
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(src, encoding='utf-8') as fh:
        body = fh.read()
    with io.open(target, 'w', encoding='utf-8', newline='') as fh:
        fh.write(body)
    return 'custom/' + name


def safe_join(root, rel, exts=('.md',)):
    """보고 폴더 밖은 열지 않는다.

    읽기는 실행 기록(.log)까지 받는다 - 실패 알림을 누르면 그것을 보여 줘야 한다.
    쓰기는 .md로 묶는다. 같은 함수로 저장 경로를 정하므로 넓히면 실행 기록을
    덮어쓸 수 있게 된다.
    """
    full = os.path.normpath(os.path.join(root, rel.replace('/', os.sep)))
    if not full.lower().startswith(os.path.join(root, '').lower()):
        return None
    if not full.lower().endswith(exts) or not os.path.isfile(full):
        return None
    return full


def newest_report(root):
    """가장 최근 보고서. 일일 → 주간 → 한 일 목록 순으로 찾는다."""
    for area, _label in AREAS:
        base = os.path.join(root, area)
        if not os.path.isdir(base):
            continue
        found = []
        for dirpath, _dirs, names in os.walk(base):
            found += [os.path.join(dirpath, n) for n in names if n.endswith('.md')]
        if found:
            return max(found, key=os.path.getmtime)
    return None


def mark_running(root, port):
    """떠 있는 자리를 남긴다. 알림을 누를 때마다 서버가 새로 뜨지 않게 한다."""
    path = os.path.join(root, 'runlog', 'viewer.json')
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(json.dumps({'port': port, 'pid': os.getpid()}))
    except OSError:
        pass

    def clear():
        try:
            os.remove(path)
        except OSError:
            pass
    return clear


def find_guide():
    """화면에서 읽는 사용 설명서.

    GUIDE.md는 skill 폴더에 들어 있다. 복사로 설치하면 저장소의 README까지
    따라오지 않기 때문이다. 연결(junction)로 설치하면 이 파일의 겉보기 경로가
    skills 폴더 안이라 거슬러 올라갈 수 없으므로 실제 위치로 풀어서 찾는다.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    for name in ('GUIDE.md', 'README.md'):
        for up in ('..', os.path.join('..', '..')):
            path = os.path.normpath(os.path.join(here, up, name))
            if os.path.isfile(path):
                return path
    return None


def asset_sibling(folder, name):
    """skill 폴더 아래 지정한 폴더의 파일만 내준다."""
    if not name or name.startswith('.') or '/' in name or '\\' in name:
        return None
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.normpath(os.path.join(here, '..', folder, name))
    return path if os.path.isfile(path) else None


def asset_path(name):
    """skill/assets 안의 파일만 내준다. 글꼴과 아이콘이 거기 있다."""
    if not name or name.startswith('.') or '/' in name or '\\' in name:
        return None
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.normpath(os.path.join(here, '..', 'assets', name))
    return path if os.path.isfile(path) else None


ICON_PATH = asset_path('work-report.ico')


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


# PMS 폼 채우기. 보고서의 제출문 절을 읽어 폼의 작업 항목으로 바꾼다.
#
# 제출문을 원본으로 삼는 이유: 사람이 읽고 고치는 글이 하나여야 한다. 에이전트가
# 기계용 파일을 따로 쓰게 하면 그 둘이 어긋나고, 어느 쪽이 올라갔는지 알 수 없다.
# 모양은 양식 파일의 "제출문 절 쓰는 법"이 정한다.
RE_CATEGORY = re.compile(r'^\(([^)]{1,20})\)$')
RE_ISSUES = re.compile(r'^연결된\s*일감\s*[:：]\s*(.+)$')


def submission_block(text):
    """보고서에서 제출문 절의 줄만 떼어 낸다."""
    lines = (text or '').split(chr(10))
    start = -1
    for i, line in enumerate(lines):
        if line.startswith('## ') and '제출문' in line:
            start = i + 1
            break
    if start < 0:
        return []
    for i in range(start, len(lines)):
        if lines[i].startswith('## '):
            return lines[start:i]
    return lines[start:]


def parse_submission(text):
    """제출문 절을 폼에 넣을 모양으로.

    "(개발)" 같은 분류 줄이 덩어리를 가르고 그 아래가 본문이다. 분류 줄이
    없으면 전체를 분류 없는 한 덩어리로 둔다 - 분류를 지어내지 않고,
    채우는 쪽이 "폼 기본값이 남았다"고 알려 준다.
    """
    items, issues, cur = [], [], None
    for raw in submission_block(text):
        line = raw.strip()
        # 양식이 모양을 코드 블록으로 보여 주다 보니 제출문도 ``` 로 감싸 쓰는
        # 경우가 있다. 울타리는 글이 아니므로 폼에 넣지 않는다.
        if line.startswith('```') or line.startswith('~~~'):
            continue
        m = RE_CATEGORY.match(line)
        if m:
            cur = {'category': m.group(1).strip(), 'content': []}
            items.append(cur)
            continue
        m = RE_ISSUES.match(line)
        if m:
            issues += re.findall(r'(\d{1,7})', m.group(1))
            continue
        if line:
            if cur is None:
                cur = {'category': '', 'content': []}
                items.append(cur)
            cur['content'].append(raw.rstrip())
        elif cur is not None:
            cur['content'].append('')
    out = []
    for it in items:
        body = chr(10).join(it['content']).strip()
        if body:
            out.append({'category': it['category'], 'content': body})
    return {'items': out, 'linked_issue_ids': [int(x) for x in dict.fromkeys(issues)]}


def report_date(rel):
    m = re.search(r'(\d{4}-\d{2}-\d{2})', rel or '')
    return m.group(1) if m else time.strftime('%Y-%m-%d')


# pms.ps1 의 종료 코드를 화면 상태로. 사람이 다음에 할 일이 코드마다 다르다.
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


def setup_status(root):
    """지금 무엇이 되어 있고 무엇이 비었는지.

    차례대로 넘기는 마법사 대신 상태를 보여 준다. 사람마다 도착 지점이 다르고,
    이미 해 둔 것을 다시 묻는 화면은 길잡이가 아니라 방해다. 여기서는 사실만
    모으고, 무엇을 권할지는 화면이 정한다.
    """
    cfg = read_config(root)
    pms = cfg.get('pms') or {}
    counts = {}
    for area, _label in AREAS:
        base = os.path.join(root, area)
        n = 0
        for _dirpath, _dirs, names in os.walk(base):
            n += len([x for x in names if x.endswith('.md')])
        counts[area] = n

    # 가장 최근 일일보고와 거기 제출문이 있는지. "제출할 것이 있나"의 답이다.
    latest, has_submission = None, False
    daily = os.path.join(root, 'daily')
    found = []
    for dirpath, _dirs, names in os.walk(daily):
        found += [os.path.join(dirpath, n) for n in names if n.endswith('.md')]
    if found:
        newest = max(found, key=os.path.getmtime)
        latest = os.path.relpath(newest, root).replace(os.sep, '/')
        try:
            with io.open(newest, encoding='utf-8') as fh:
                has_submission = bool(parse_submission(fh.read())['items'])
        except OSError:
            pass

    # 로그인했는지는 띄워 보지 않으면 모른다. 쿠키가 남아 있는지로 "한 적 있다"까지만.
    profile = pms.get('profile') or os.path.join(root, 'browser')
    signed_in = os.path.isfile(os.path.join(profile, 'Default', 'Network', 'Cookies'))

    fetched = os.path.join(root, 'pms', 'my-daily-reports.json')
    harvest = {'count': 0, 'latest': None}
    try:
        with io.open(fetched, encoding='utf-8-sig') as fh:
            got = json.load(fh).get('reports') or []
        harvest = {'count': len(got), 'latest': got[0]['date'] if got else None}
    except (OSError, ValueError, KeyError, IndexError):
        pass

    try:
        import importlib.util
        playwright = importlib.util.find_spec('playwright') is not None
    except Exception:
        playwright = False

    issues = os.path.join(root, 'pms', 'open-issues.md')
    return {'reports': counts, 'latest_daily': latest, 'has_submission': has_submission,
            'custom': {name: bool(cfg.get(flag)) for name, _l, flag in CUSTOM},
            'seen': [x for x in (cfg.get('setup_seen') or []) if x in SEEN_KEYS],
            'author': cfg.get('author') or '',
            # 토큰은 있는지만 알린다. 값은 화면으로 돌려보내지 않는다.
            'pms': {'url': pms.get('url') or '', 'project': pms.get('project') or '',
                    'has_token': secret_store.has(root, 'pms_token'),
                    'issues_at': os.path.isfile(issues),
                    'signed_in': signed_in, 'playwright': playwright, 'harvest': harvest}}


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


# 화면에서 고칠 수 있는 항목만 받는다. 설치가 채우는 기계 정보
# (*_homes, *_dirs, skill_dirs)는 손으로 고치면 깨지므로 받지 않는다.
# 설정을 한 번 열어 봤는지. "내 양식"은 켜지 않는 것도 답이라서, 토글로는
# 끝났는지 알 수 없다. 본 적이 있으면 그 단계는 끝난 것으로 둔다.
SEEN_KEYS = ('custom',)

EDITABLE = {
    'author': str, 'agent': str, 'notify': bool, 'submit_url': str, 'submit_label': str,
    'mine_only': bool, 'redact': bool, 'backfill_days': int, 'retain_months': int,
    'custom_format': bool, 'custom_rules': bool, 'custom_samples': bool,
    'exclude_repos': list, 'exclude_paths': list,
    'claude_bin': str, 'codex_bin': str, 'python_bin': str,
    'max_prompt_chars': int, 'max_prompts_per_session': int,
}
WEEKLY_KEYS = {'end_day': str, 'span_days': int}
# PMS 일일보고 폼을 채우는 기능의 설정. 비어 있으면 그 기능만 꺼진 것처럼 동작한다.
# projects 는 지난 보고서를 받아올 프로젝트들이고, 비우면 project 하나만 본다.
# token 은 여기 없다. 금고(secrets.py)로 따로 간다 - config.json 은 에이전트가
# 읽는 폴더에 있고, 비밀값이 거기 있으면 읽힌다.
PMS_KEYS = {'url': str, 'project': str, 'projects': list, 'port': int,
            'profile': str, 'chrome_bin': str}


def read_config(root):
    try:
        with open(os.path.join(root, 'config.json'), encoding='utf-8-sig') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_config(root, incoming):
    """들어온 값 중 허용된 것만 반영하고 나머지는 그대로 둔다."""
    cfg = read_config(root)
    for key, kind in EDITABLE.items():
        if key not in incoming:
            continue
        value = incoming[key]
        try:
            if kind is bool:
                cfg[key] = bool(value)
            elif kind is int:
                cfg[key] = int(value)
            elif kind is list:
                cfg[key] = [str(v).strip() for v in value if str(v).strip()]
            else:
                cfg[key] = str(value)
        except (TypeError, ValueError):
            continue
    # 토글을 켠 순간 고칠 파일이 있어야 한다. 첫 실행을 기다리게 하지 않는다
    for name, _label, flag in CUSTOM:
        if cfg.get(flag):
            seed_custom(root, name)

    # 비밀값은 설정 파일이 아니라 금고로. 빈 값은 "그대로 둬라"는 뜻이라 무시한다.
    token = ((incoming.get('pms') or {}).get('token') or '').strip()
    if token:
        secret_store.put(root, 'pms_token', token)

    for group, allowed in (('weekly', WEEKLY_KEYS), ('pms', PMS_KEYS)):
        incoming_group = incoming.get(group) or {}
        if not incoming_group:
            continue
        cur = dict(cfg.get(group) or {})
        for key, kind in allowed.items():
            if key not in incoming_group:
                continue
            value = incoming_group[key]
            try:
                if kind is int:
                    cur[key] = int(value or 0)
                elif kind is list:
                    cur[key] = [str(v).strip() for v in value if str(v).strip()]
                else:
                    cur[key] = str(value)
            except (TypeError, ValueError):
                pass
        cfg[group] = cur
    path = os.path.join(root, 'config.json')
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
    return cfg


def html_escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def build_page(title, submit_url, submit_label, has_readme, pms_on=False):
    return (PAGE.replace('{{TITLE}}', html_escape(title))
            .replace('{{SUBMIT_URL}}', html_escape(submit_url or ''))
            .replace('{{SUBMIT_LABEL}}', html_escape(submit_label or '제출하러 가기'))
            .replace('{{HAS_README}}', 'true' if has_readme else 'false')
            .replace('{{PMS_ON}}', 'true' if pms_on else 'false'))


PAGE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><title>{{TITLE}}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="/favicon.ico">
<style>
/* ============================================================== 글꼴
   Pretendard. 한글과 라틴 문자의 크기가 맞아 숫자와 한글이 섞인 표에서
   줄이 흔들리지 않는다. skill/assets에 함께 들어 있어 인터넷 없이 뜬다. */
@font-face {
  font-family:'Pretendard';
  src:url('/assets/Pretendard.woff2') format('woff2-variations');
  font-weight:45 920;
  font-style:normal;
  font-display:swap;
}

/* ============================================================== 토큰
   화면에 쓰이는 값은 전부 여기서 나온다. 크기나 여백을 바꿀 일이 생기면
   규칙을 찾아다니지 말고 이 표를 고친다.

   색은 따뜻한 중성색 한 벌에 잉크색 하나만 더한 구성이다. 이 화면이 하루에
   하는 일은 문서 한 장을 읽히는 것인데, 색이 여럿이면 문서보다 화면이 먼저
   보인다. 강조색은 지금 고른 것과 지금 누를 것에만 쓴다. */
:root {
  --bg:#f5f4f1; --panel:#ffffff; --soft:#efede8; --line:#e2dfd8; --hair:#edeae4;
  --text:#1b1a17; --ink:#302e29; --muted:#6a655c; --faint:#97918a;
  --accent:#3a5a8c; --accent-soft:#eaeff7; --accent-line:#c6d5e8;
  --solid:#262420; --solid-hi:#3a372f; --on-solid:#ffffff;
  --ok:#3f7a4a; --bad:#a9442f; --warn:#8a6a1f;
  --scroll:#cfcbc2; --scroll-hover:#aea89d;

  --font:'Pretendard',-apple-system,"Segoe UI","Malgun Gothic",system-ui,sans-serif;
  --font-mono:ui-monospace,"Cascadia Mono",Consolas,monospace;

  /* 글자 크기 - 여섯 단만 쓴다 */
  --fs-100:12px;     /* 꼬리표, 보조 설명 */
  --fs-200:13.5px;   /* 목록, 단추, 라벨 */
  --fs-300:15px;     /* 본문 */
  --fs-400:16.5px;   /* 작은 제목 */
  --fs-500:19px;     /* 절 제목 */
  --fs-600:27px;     /* 문서 제목 */
  --fs-mono:13.5px;

  --lh-tight:1.35;
  --lh-body:1.75;
  --fw-normal:400;
  --fw-medium:530;
  --fw-bold:650;

  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px;
  --sp-5:20px; --sp-6:24px; --sp-8:32px;

  /* 종이는 모서리를 깎지 않는다. 둥근 카드가 늘어서면 문서가 위젯처럼 보인다 */
  --r-sm:3px; --r-md:5px; --r-lg:7px; --r-pill:999px;
  --shadow-1:0 1px 1px rgba(28,25,20,.05);
  --shadow-2:0 10px 30px rgba(28,25,20,.16);

  --rail:244px;
  --topbar:52px;
  --measure:820px;
  --label:172px;
}

/* 어두운 화면. OS를 따르되 위 막대의 단추로 손수 고를 수 있다 */
:root[data-theme="dark"] {
  --bg:#131210; --panel:#1b1a17; --soft:#232120; --line:#302d28; --hair:#262320;
  --text:#edeae4; --ink:#d9d5cd; --muted:#9c968b; --faint:#6f6a61;
  --accent:#7ea4d6; --accent-soft:#1a222c; --accent-line:#32465e;
  --solid:#e9e5dd; --solid-hi:#fffcf6; --on-solid:#1b1a17;
  --ok:#6fbb7c; --bad:#dd8c78; --warn:#d2ab57;
  --scroll:#3b3832; --scroll-hover:#524d45;
  --shadow-1:0 1px 1px rgba(0,0,0,.4);
  --shadow-2:0 10px 30px rgba(0,0,0,.55);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg:#131210; --panel:#1b1a17; --soft:#232120; --line:#302d28; --hair:#262320;
    --text:#edeae4; --ink:#d9d5cd; --muted:#9c968b; --faint:#6f6a61;
    --accent:#7ea4d6; --accent-soft:#1a222c; --accent-line:#32465e;
    --solid:#e9e5dd; --solid-hi:#fffcf6; --on-solid:#1b1a17;
    --ok:#6fbb7c; --bad:#dd8c78; --warn:#d2ab57;
    --scroll:#3b3832; --scroll-hover:#524d45;
    --shadow-1:0 1px 1px rgba(0,0,0,.4);
    --shadow-2:0 10px 30px rgba(0,0,0,.55);
  }
}

/* ============================================================== 바탕 */
* { box-sizing:border-box; }
[hidden] { display:none !important; }

/* 스크롤 막대. 기본 막대는 폭이 넓고 회색이 짙어 본문보다 먼저 눈에 들어온다.
   여백 안에 가느다란 알약 하나만 남긴다.
   크롬은 표준 scrollbar-width가 있으면 아래 규칙을 통째로 무시하고 제 막대를
   그린다. 그래서 표준 속성은 그 규칙을 모르는 쪽(파이어폭스)에만 준다. */
@supports not selector(::-webkit-scrollbar) {
  * { scrollbar-width:thin; scrollbar-color:var(--scroll) transparent; }
}
*::-webkit-scrollbar { width:11px; height:11px; }
*::-webkit-scrollbar-track { background:transparent; }
*::-webkit-scrollbar-thumb { background:var(--scroll); border-radius:var(--r-pill);
                             border:3px solid transparent; background-clip:content-box; }
*::-webkit-scrollbar-thumb:hover { background:var(--scroll-hover); background-clip:content-box; }
*::-webkit-scrollbar-corner { background:transparent; }
*::-webkit-scrollbar-button { display:none; width:0; height:0; }

html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--text);
       font-family:var(--font); font-size:var(--fs-300); line-height:var(--lh-body);
       -webkit-font-smoothing:antialiased; }
pre, code, textarea.mono { font-family:var(--font-mono); }
/* 날짜가 세로로 줄지어 선다. 자릿수가 흔들리면 목록이 읽히지 않는다 */
.num { font-variant-numeric:tabular-nums; }
svg { display:block; flex:0 0 auto; }

/* ============================================================== 컴포넌트
   여기 있는 것만 쓴다. 화면마다 새 모양을 만들지 않는다.
     단추 button / .solid / .quiet / .iconbtn
     고르개 .seg          카드 .card         꼬리표 .chip
     입력 input .switch   줄 .field          구역 제목 .sectitle
     목록 항목 .navitem .doclink             알림 .job                     */

button {
  display:inline-flex; align-items:center; gap:var(--sp-2);
  font-family:inherit; font-size:var(--fs-200); font-weight:var(--fw-medium);
  line-height:1.4; cursor:pointer; white-space:nowrap;
  padding:6px var(--sp-3); border-radius:var(--r-md);
  border:1px solid var(--line); background:var(--panel); color:var(--text);
  transition:background .1s, border-color .1s, color .1s;
}
button:hover { background:var(--soft); border-color:var(--scroll); }
button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
button:disabled { opacity:.4; cursor:default; }
button:disabled:hover { background:var(--panel); border-color:var(--line); }
/* 그 화면에서 결론이 되는 동작 하나에만 쓴다. 색이 아니라 농도로 구분한다 */
button.solid { background:var(--solid); border-color:var(--solid); color:var(--on-solid); }
button.solid:hover { background:var(--solid-hi); border-color:var(--solid-hi); }
button.quiet { border-color:transparent; background:transparent; color:var(--muted); }
button.quiet:hover { background:var(--soft); border-color:transparent; color:var(--text); }
button.iconbtn { padding:6px; border-color:transparent; background:transparent; color:var(--muted); }
button.iconbtn:hover { background:var(--soft); border-color:transparent; color:var(--text); }

.seg { display:inline-flex; gap:2px; padding:2px; border-radius:var(--r-md);
       background:var(--soft); border:1px solid var(--line); }
.seg button { border:0; background:transparent; color:var(--muted);
              padding:3px var(--sp-3); border-radius:var(--r-sm);
              font-weight:var(--fw-normal); }
.seg button:hover { background:transparent; color:var(--text); }
.seg button.on { background:var(--panel); color:var(--text);
                 font-weight:var(--fw-bold); box-shadow:var(--shadow-1); }

.card { background:var(--panel); border:1px solid var(--line);
        border-radius:var(--r-lg); padding:var(--sp-2) var(--sp-5) var(--sp-4);
        margin:0 0 var(--sp-4); }

.chip { flex:0 0 auto; font-size:var(--fs-100); font-weight:var(--fw-normal);
        color:var(--faint); background:var(--soft);
        border-radius:var(--r-pill); padding:1px var(--sp-2); }

input[type=text], input[type=number], select, textarea {
  font-family:inherit; font-size:var(--fs-200); line-height:1.5;
  padding:6px var(--sp-3); border:1px solid var(--line);
  border-radius:var(--r-md); background:var(--bg); color:var(--text); width:100%;
}
input:focus, select:focus, textarea:focus {
  outline:2px solid var(--accent); outline-offset:-1px; border-color:transparent; }
/* 값의 길이에 맞춘다. 네 글자를 받는 칸이 화면을 가로지르지 않게 */
.short { max-width:200px; }
.mid   { max-width:340px; }
.long  { max-width:470px; }
input[type=number] { width:100px; }
select { max-width:200px; }
textarea.lines { max-width:430px; min-height:78px; resize:vertical;
                 font-family:var(--font-mono); font-size:var(--fs-mono); }
.switch { justify-self:start; position:relative; width:38px; height:22px; padding:0;
          -webkit-appearance:none; appearance:none; cursor:pointer; border:0;
          background:var(--line); border-radius:var(--r-pill); transition:background .15s; }
.switch::after { content:""; position:absolute; top:3px; left:3px;
                 width:16px; height:16px; border-radius:50%; background:#fff;
                 box-shadow:0 1px 2px rgba(0,0,0,.3); transition:left .15s; }
.switch:checked { background:var(--accent); }
.switch:checked::after { left:19px; }

.field { display:grid; grid-template-columns:minmax(0,var(--label)) minmax(0,1fr);
         gap:var(--sp-1) var(--sp-4); align-items:center;
         padding:var(--sp-3) 0; border-top:1px solid var(--hair); }
.card > .field:first-child, .card > h3 + .field { border-top:0; }
.field label { font-size:var(--fs-300); color:var(--text); }
.field .with { display:flex; align-items:center; gap:var(--sp-3); min-width:0; }
.hint { grid-column:2; font-size:var(--fs-100); color:var(--faint);
        white-space:pre-line; line-height:1.6; }

.sectitle { padding:var(--sp-4) var(--sp-3) var(--sp-1);
            font-size:var(--fs-100); font-weight:var(--fw-bold);
            letter-spacing:.07em; color:var(--faint); }

.navitem { display:flex; align-items:center; gap:var(--sp-2); width:100%;
           padding:6px var(--sp-3); border:0; border-radius:var(--r-md);
           background:transparent; color:var(--muted);
           font-size:var(--fs-200); font-weight:var(--fw-normal); text-align:left; }
.navitem:hover { background:var(--soft); color:var(--text); border-color:transparent; }
.navitem.on { background:var(--accent-soft); color:var(--text); font-weight:var(--fw-bold);
              box-shadow:inset 2px 0 0 var(--accent); }

/* 지금 고른 것은 채운 색이 아니라 왼쪽 선으로 말한다. 목록이 길어도 파란
   알약이 줄줄이 켜지지 않고 글자가 그대로 읽힌다 */
.doclink { display:flex; align-items:center; gap:var(--sp-2);
           padding:3px var(--sp-3) 3px var(--sp-6);
           border-radius:var(--r-md); color:var(--ink); text-decoration:none;
           font-size:var(--fs-200); white-space:nowrap;
           overflow:hidden; text-overflow:ellipsis; }
.doclink:hover { background:var(--soft); }
.doclink.on { background:var(--accent-soft); color:var(--text); font-weight:var(--fw-bold);
              box-shadow:inset 2px 0 0 var(--accent); }
.doclink .chip { margin-left:auto; }
.doclink.on .chip { background:var(--panel); color:var(--muted); }
.recent .doclink { padding-left:var(--sp-3); }

#jobs { position:fixed; right:var(--sp-5); bottom:var(--sp-5); z-index:30;
        display:flex; flex-direction:column; gap:var(--sp-2); width:330px; }
.job { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-lg);
       padding:var(--sp-3) var(--sp-4); box-shadow:var(--shadow-2);
       font-size:var(--fs-200); animation:rise .18s ease-out; }
@keyframes rise { from { opacity:0; transform:translateY(6px); } }
.job .row { display:flex; align-items:center; gap:var(--sp-2); }
.job .what { font-weight:var(--fw-bold); }
.job .time { margin-left:auto; color:var(--faint); font-size:var(--fs-100); }
.job .x { padding:2px; border:0; background:transparent; color:var(--faint);
          border-radius:var(--r-sm); }
.job .x:hover { background:var(--soft); color:var(--text); border-color:transparent; }
.job .bar { height:2px; border-radius:2px; background:var(--soft);
            margin-top:var(--sp-3); overflow:hidden; }
.job .bar i { display:block; height:100%; width:35%; background:var(--accent);
              animation:slide 1.2s ease-in-out infinite; }
@keyframes slide { 0% { margin-left:-35%; } 100% { margin-left:100%; } }
.job .dot { width:7px; height:7px; border-radius:50%; flex:0 0 7px; background:var(--faint); }
.job.done .dot { background:var(--ok); }
.job.failed .dot { background:var(--bad); }
.job .note { margin-top:var(--sp-2); color:var(--muted); font-size:var(--fs-100); }
.job .note a { display:block; color:var(--accent); text-decoration:none; padding:1px 0; }
.job .note a:hover { text-decoration:underline; }
.job.skipped .dot { background:var(--faint); }

/* -- 시작하기: 무엇이 되어 있는지 보여 주는 상태판 ------------------ */
.setup { max-width:620px; margin:0 auto; padding:var(--sp-5) var(--sp-4) var(--sp-6); }
.setuphead { font-size:var(--fs-600); font-weight:var(--fw-bold); margin:0 0 var(--sp-1);
             letter-spacing:-.02em; }
.setuplede { color:var(--muted); font-size:var(--fs-200); margin-bottom:var(--sp-5); }
.sfield { display:grid; gap:4px; margin:var(--sp-2) 0; max-width:420px; }
.sfield label { font-size:var(--fs-100); color:var(--faint); }
.sfield input { width:100%; padding:7px 9px; border:1px solid var(--line);
                border-radius:var(--r-md); background:var(--panel); color:var(--text);
                font:inherit; font-size:var(--fs-200); }
.sfield input:focus { outline:none; border-color:var(--accent); }
.sfield.filled input::placeholder { color:var(--ok); }
.sfield.filled label { color:var(--ok); }
.sfield .shint { font-size:var(--fs-100); color:var(--faint); }
.step { display:grid; grid-template-columns:20px 1fr; gap:var(--sp-3);
        padding:var(--sp-3) 0; border-top:1px solid var(--line); }
.step .mark { color:var(--faint); line-height:1.5; }
.step.ok .mark { color:var(--ok); }
.step .t { font-weight:var(--fw-bold); margin-bottom:2px; }
.step .d { color:var(--muted); font-size:var(--fs-200); line-height:1.55; }
.step.wait .t, .step.wait .d { color:var(--faint); }
.step button { margin-top:var(--sp-2); }

/* ============================================================== 뼈대
   화면은 세 층이다. 위 막대는 "만드는 일", 왼쪽 레일은 "어디로 갈지",
   오른쪽은 문서 한 장과 그 문서에 하는 일. 층이 나뉘어 있어야 지금 누르는
   단추가 무엇에 작용하는지 헷갈리지 않는다. */
.app { display:flex; flex-direction:column; height:100%; }
.main { flex:1 1 auto; display:flex; min-height:0; position:relative; }

.appbar { flex:0 0 auto; display:flex; align-items:center; gap:var(--sp-2);
          height:var(--topbar); padding:0 var(--sp-3) 0 var(--sp-2);
          background:var(--panel); border-bottom:1px solid var(--line); }
.appbar .home { padding:var(--sp-1) var(--sp-2); border-color:transparent; background:transparent; }
.appbar .home:hover { background:var(--soft); border-color:transparent; }
.appbar .logo { width:20px; height:20px; border-radius:var(--r-sm); display:block; }
.appbar .brand { font-size:var(--fs-300); font-weight:var(--fw-bold); letter-spacing:-.01em; }
.appbar .grow { margin-left:auto; }
.appbar .sep { width:1px; height:20px; background:var(--line); margin:0 var(--sp-1); }
.appbar .make { display:flex; gap:var(--sp-2); }

nav.side { flex:0 0 var(--rail); width:var(--rail); min-height:0;
           display:flex; flex-direction:column;
           background:var(--panel); border-right:1px solid var(--line); }
nav.side.hide { display:none; }
.searchbox { flex:0 0 auto; display:flex; align-items:center; gap:var(--sp-2);
             margin:var(--sp-3) var(--sp-3) var(--sp-1); padding:0 var(--sp-2);
             border:1px solid var(--line); border-radius:var(--r-md);
             background:var(--bg); color:var(--faint); }
.searchbox:focus-within { outline:2px solid var(--accent); outline-offset:-1px;
                          border-color:transparent; }
.searchbox input { border:0; outline:0; background:transparent; padding:5px 0;
                   font-size:var(--fs-200); }
.searchbox button { padding:2px; border:0; background:transparent; color:var(--faint); }
.searchbox button:hover { background:transparent; color:var(--text); }
.navscroll { flex:1 1 auto; overflow-y:auto; overflow-x:hidden;
             padding:0 var(--sp-2) var(--sp-4); }
.navfoot { flex:0 0 auto; border-top:1px solid var(--line); padding:var(--sp-2); }

.group > .head { display:flex; align-items:center; gap:var(--sp-2); width:100%;
                 padding:5px var(--sp-3); border:0; border-radius:var(--r-md);
                 background:transparent; color:var(--text);
                 font-size:var(--fs-200); font-weight:var(--fw-bold); text-align:left; }
.group > .head:hover { background:var(--soft); border-color:transparent; }
.group .count { margin-left:auto; color:var(--faint);
                font-size:var(--fs-100); font-weight:var(--fw-normal); }
.caret { width:9px; flex:0 0 9px; color:var(--faint); font-size:9px;
         transition:transform .12s; }
.closed > .head .caret, .closed > .mhead .caret { transform:rotate(-90deg); }
.closed > .items { display:none; }
.month > .mhead { display:flex; align-items:center; gap:var(--sp-2); width:100%;
                  padding:3px var(--sp-3) 3px var(--sp-5); border:0; background:transparent;
                  color:var(--faint); font-size:var(--fs-100); text-align:left; }
.month > .mhead:hover { color:var(--muted); background:transparent; border-color:transparent; }

.doc { flex:1 1 auto; display:flex; flex-direction:column; min-width:0; min-height:0; }
.doctop { flex:0 0 auto; display:flex; align-items:center;
          gap:var(--sp-3); flex-wrap:wrap;
          padding:var(--sp-2) var(--sp-5);
          background:var(--panel); border-bottom:1px solid var(--line); }
.flip { display:flex; gap:2px; }
.titlebox { min-width:0; margin-right:auto; padding:2px 0; }
.doctitle { margin:0; font-size:var(--fs-400); font-weight:var(--fw-bold);
            line-height:var(--lh-tight); letter-spacing:-.015em;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.docsub { color:var(--faint); font-size:var(--fs-100); margin-top:1px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.docacts { display:flex; align-items:center; gap:var(--sp-2); flex-wrap:wrap; }
.state { font-size:var(--fs-100); color:var(--muted); white-space:nowrap; }
.state.warn { color:var(--warn); font-weight:var(--fw-bold); }
.state.good { color:var(--ok); font-weight:var(--fw-bold); }

/* 같은 날짜의 다른 문서로 건너가는 줄. 요약에서 빠진 근거를 되찾는 길이라
   문서 바로 위에 둔다 */
.rel { flex:0 0 auto; display:flex; align-items:center; gap:var(--sp-2);
       padding:5px var(--sp-5); background:var(--bg);
       border-bottom:1px solid var(--line); font-size:var(--fs-100); color:var(--faint); }
.rel a { display:inline-flex; align-items:center; gap:5px; color:var(--muted);
         text-decoration:none; padding:1px var(--sp-2); border-radius:var(--r-sm);
         border:1px solid var(--line); background:var(--panel); }
.rel a:hover { color:var(--text); border-color:var(--scroll); }

/* 스크롤은 여기 한 곳에서만 일어난다 */
.sheet { flex:1 1 auto; overflow-y:auto; }
.wrap { max-width:var(--measure); margin:0 auto;
        padding:var(--sp-6) var(--sp-6) 96px; }

/* 본문은 바탕 위에 놓인 종이 한 장이다. 모서리를 깎지 않고 그림자도 거의
   없다 - 문서처럼 꾸미는 것이 아니라 문서로 보이게 한다 */
.paper { background:var(--panel); border:1px solid var(--line);
         border-radius:2px; padding:44px 52px; box-shadow:var(--shadow-1); }

/* ============================================================== 본문 서식 */
.body { color:var(--ink); }
.body h1 { color:var(--text); font-size:var(--fs-600); font-weight:var(--fw-bold);
           line-height:var(--lh-tight); letter-spacing:-.025em;
           margin:0 0 var(--sp-5); padding-bottom:var(--sp-4);
           border-bottom:1px solid var(--text); }
.body h2 { color:var(--text); font-size:var(--fs-500); font-weight:var(--fw-bold);
           line-height:var(--lh-tight); margin:var(--sp-8) 0 var(--sp-3);
           letter-spacing:-.01em; }
.body h3 { font-size:var(--fs-400); font-weight:var(--fw-bold); margin:var(--sp-6) 0 var(--sp-2); }
.body h4 { font-size:var(--fs-300); font-weight:var(--fw-bold);
           color:var(--muted); margin:var(--sp-5) 0 var(--sp-1); }
.body > :first-child { margin-top:0; }
.body p { margin:var(--sp-3) 0; }
.body ul, .body ol { margin:var(--sp-3) 0; padding-left:1.45em; }
.body ul ul, .body ol ol, .body ul ol, .body ol ul { margin:var(--sp-1) 0; }
.body li { margin:3px 0; }
.body li::marker { color:var(--faint); font-size:.9em; }
.body a { color:var(--accent); text-underline-offset:2px; }
.body hr { border:0; border-top:1px solid var(--line); margin:var(--sp-6) 0; }
.body blockquote { margin:var(--sp-3) 0; padding:2px 0 2px var(--sp-4);
                   border-left:2px solid var(--line); color:var(--muted); }
.body code { font-size:.88em; background:var(--soft); border:1px solid var(--hair);
             padding:.1em .36em; border-radius:var(--r-sm); }
.body pre.code { background:var(--soft); border:1px solid var(--hair);
                 border-radius:var(--r-md); padding:var(--sp-3) var(--sp-4);
                 overflow-x:auto; font-size:var(--fs-mono); line-height:1.65; }
.body pre.code code { background:none; border:0; padding:0; }

/* 표는 세로줄을 긋지 않는다. 칸마다 테두리가 있으면 격자가 먼저 보이고
   읽는 눈이 행을 따라가지 못한다. 가로 괘선만으로 충분하다 */
.tablewrap { overflow-x:auto; margin:var(--sp-4) 0; }
.body table { border-collapse:collapse; width:100%; font-size:var(--fs-200); }
.body th, .body td { padding:7px var(--sp-3); text-align:left; vertical-align:top;
                     border-bottom:1px solid var(--hair); }
.body th { color:var(--muted); font-size:var(--fs-100); font-weight:var(--fw-bold);
           letter-spacing:.04em; border-bottom:1px solid var(--muted); white-space:nowrap; }
.body tbody tr:last-child td { border-bottom:1px solid var(--line); }
/* 양 끝 칸은 보통 "구분", "상태"처럼 짧은 이름표다. 폭을 내주면 글자가
   세로로 쪼개져 읽을 수 없게 된다. 가운데 칸이 남는 폭을 가져간다.
   칸이 둘셋뿐인 표는 마지막이 짧은 이름표가 아니라 설명이다. */
.body table:not([data-cols="2"]):not([data-cols="3"]) :is(th, td):first-child:not(.on),
.body table:not([data-cols="2"]):not([data-cols="3"]) :is(th, td):last-child:not(.on) { white-space:nowrap; width:1%; }
.body table[data-cols="3"] :is(th, td):first-child:not(.on) { white-space:nowrap; width:1%; }
.body td { word-break:break-word; }
.body td code { word-break:break-all; }

/* 표 칸은 두 번 눌러 그 자리에서 고친다. 손대는 곳이 대개 칸 하나라서
   원문을 열지 않고 끝나는 쪽이 짧다 */
.cells td { cursor:text; }
.cells td:hover { background:var(--accent-soft); }
.body td.on { background:var(--panel); outline:2px solid var(--accent); outline-offset:-2px;
              white-space:pre-wrap; }

pre.raw { margin:0; padding:0; border:0; background:transparent;
          color:var(--ink); font-size:var(--fs-mono); line-height:1.8;
          white-space:pre-wrap; word-break:break-word; }

/* ============================================================== 고치는 화면
   왼쪽 원문, 오른쪽 결과. 저장해 봐야 어떻게 보이는지 알던 것을 없앤다 */
.sheet.editing { overflow:hidden; }
.sheet.editing .wrap { max-width:none; height:100%; padding:0; }
.editor { display:grid; grid-template-columns:1fr 1fr; height:100%; min-height:0; }
.editor .pane { min-width:0; overflow:auto; }
.editor .pane.src { overflow:hidden; background:var(--panel);
                    border-right:1px solid var(--line); }
.editor .pane.live { background:var(--bg); padding:var(--sp-6) var(--sp-8) 60px; }
.editor .pane.live .body { max-width:620px; }
.editor textarea { width:100%; height:100%; border:0; outline:none; resize:none;
                   border-radius:0; background:transparent; color:var(--ink);
                   padding:var(--sp-6) var(--sp-5); overflow:auto;
                   font-size:var(--fs-mono); line-height:1.85; }
.livehead { font-size:var(--fs-100); color:var(--faint); margin-bottom:var(--sp-4);
            letter-spacing:.06em; }

.conf .card > h3 { margin:var(--sp-4) 0 var(--sp-1); font-size:var(--fs-100);
                   font-weight:var(--fw-bold); letter-spacing:.06em; color:var(--faint); }
.conf .lede { font-size:var(--fs-100); color:var(--faint); margin:0 0 var(--sp-5); }
.conf .open { font-size:var(--fs-100); color:var(--accent); text-decoration:none;
              border:1px solid var(--accent-line); border-radius:var(--r-sm);
              padding:1px var(--sp-2); background:var(--accent-soft); white-space:nowrap; }

.empty { color:var(--faint); font-size:var(--fs-200); padding:var(--sp-4) var(--sp-3); }
.readonly { font-size:var(--fs-100); color:var(--faint); }

/* 내 양식은 문서가 아니라 설정 옆에 두는 것이라 아래 칸에 있다 */
.sectitle.custom { margin-top:var(--sp-1); padding-top:var(--sp-3);
                   border-top:1px solid var(--hair); }
.doclink.none { color:var(--faint); }

.scrim { position:absolute; inset:0; z-index:15; background:rgba(20,18,15,.3); }

@media (max-width:1100px) {
  .editor { grid-template-columns:1fr; }
  .editor .pane.live { display:none; }
  .editor.showlive .pane.src { display:none; }
  .editor.showlive .pane.live { display:block; }
}
@media (max-width:860px) {
  nav.side { position:absolute; z-index:20; height:100%; box-shadow:var(--shadow-2); }
  .wrap { padding:var(--sp-4) var(--sp-3) 80px; }
  .paper { padding:var(--sp-6) var(--sp-5); }
  .doctop, .rel { padding-left:var(--sp-4); padding-right:var(--sp-4); }
  #jobs { right:var(--sp-3); bottom:var(--sp-3); width:auto; left:var(--sp-3); }
}
</style></head>
<body>
<div class="app">

  <header class="appbar">
    <button class="iconbtn" id="toggleSide" title="목록 접기 (Ctrl+\)">
      <svg width="17" height="17" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 5.5h14M3 10h14M3 14.5h14"/></svg>
    </button>
    <button class="home" id="home" title="가장 최근 보고서로">
      <img class="logo" src="/favicon.ico" alt="">
      <span class="brand">work-report</span>
    </button>
    <span class="grow"></span>
    <button class="iconbtn" id="theme" title="화면 밝기"></button>
    <span class="sep"></span>
    <span class="make">
      <button id="runDaily">일일보고 만들기</button>
      <button id="runWeekly">주간보고 만들기</button>
    </span>
  </header>

  <div class="main">
    <nav class="side" id="side">
      <div class="searchbox">
        <svg width="14" height="14" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="9" cy="9" r="5.5"/><path d="M13.2 13.2 17 17" stroke-linecap="round"/></svg>
        <input id="q" type="text" placeholder="날짜로 찾기  (/)" spellcheck="false" autocomplete="off">
        <button id="clearq" title="지우기" hidden>
          <svg width="13" height="13" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>
        </button>
      </div>
      <div class="navscroll" id="files"></div>
      <div class="navfoot">
        <button class="navitem" id="tabSetup">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10.5l4 4 8-9"/></svg>
          시작하기
        </button>
        <button class="navitem" id="tabReadme">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="7.2"/><path d="M10 9v5" stroke-linecap="round"/><circle cx="10" cy="6.4" r=".9" fill="currentColor" stroke="none"/></svg>
          사용 설명
        </button>
        <button class="navitem" id="tabConfig">
          <svg width="15" height="15" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.6v2.2M10 15.2v2.2M17.4 10h-2.2M4.8 10H2.6M15.2 4.8l-1.6 1.6M6.4 13.6l-1.6 1.6M15.2 15.2l-1.6-1.6M6.4 6.4 4.8 4.8" stroke-linecap="round"/></svg>
          설정
        </button>
        <div id="customBox"></div>
      </div>
    </nav>

    <section class="doc">
      <div class="doctop">
        <span class="flip" id="flip">
          <button class="iconbtn" id="prev" title="이전 날짜 ([)">
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4.5 6.5 10l5.5 5.5"/></svg>
          </button>
          <button class="iconbtn" id="next" title="다음 날짜 (])">
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4.5 13.5 10 8 15.5"/></svg>
          </button>
        </span>
        <div class="titlebox">
          <h1 class="doctitle" id="docTitle">work-report</h1>
          <div class="docsub" id="docSub"></div>
        </div>
        <div class="docacts">
          <span class="state" id="state"></span>
          <span class="seg" id="seg">
            <button class="on" id="tabPreview">미리보기</button>
            <button id="tabRaw">원문</button>
          </span>
          <button id="edit">고치기</button>
          <button id="cancel" hidden>되돌리기</button>
          <button id="save" class="solid" hidden>저장</button>
          <button id="confSave" class="solid" hidden>설정 저장</button>
          <button id="copy" class="quiet" hidden>복사</button>
          <button id="pmsFill" class="solid" hidden>PMS에 채우기</button>
          <button id="submit" class="solid" hidden>{{SUBMIT_LABEL}}</button>
        </div>
      </div>
      <div class="rel" id="rel" hidden></div>
      <div class="sheet" id="sheet">
        <div class="wrap">
          <div class="paper" id="paper">
            <div class="body" id="view"></div>
            <pre class="raw" id="rawView" hidden></pre>
          </div>
          <div class="editor" id="editor" hidden>
            <div class="pane src"><textarea id="src" class="mono" spellcheck="false"></textarea></div>
            <div class="pane live">
              <div class="livehead">미리보기</div>
              <div class="body" id="liveView"></div>
            </div>
          </div>
          <div class="body conf" id="confView" hidden></div>
        </div>
      </div>
    </section>
  </div>
</div>

<div id="jobs"></div>

<script>
const SUBMIT_URL = "{{SUBMIT_URL}}";
const HAS_README = {{HAS_README}};
// 알림을 눌러 들어오면 어떤 보고서를 열지 주소가 말해 준다
const START = new URLSearchParams(location.search).get('path') || '';
const NL = String.fromCharCode(10);     // 안내문 줄바꿈
// PMS 주소가 설정돼 있는지. 설정 화면을 아직 열지 않았으면 conf 는 비어 있으므로
// 띄울 때의 값을 박아 둔다. 설정을 저장하면 conf 가 채워져 그때부터는 그쪽이 맞다.
const PMS_ON = {{PMS_ON}};
const AREA_NAME = { daily:'일일 보고', weekly:'주간 보고', log:'한 일 목록', raw:'수집 원본' };
const AREA_TAG  = { daily:'일일', weekly:'주간', log:'한 일', raw:'원본' };
const CUSTOM_NAME = { 'report-format.md':'보고서 양식', 'writing-rules.md':'글쓰기 문체',
                      'my-reports.md':'내 보고서' };
// 제출문 절은 붙여넣기용이라 보고서 전체가 아니라 그 절만 클립보드에 담는다
const SUBMIT_HEAD = '제출문';
// 보고서를 쓸 때 근거로 들춰 보는 것들이다. 매번 펼쳐져 있으면 목록만 길어진다
const FOLDED = ['log', 'raw'];
const WD = ['일','월','화','수','목','금','토'];

let raw = '', orig = '', current = '', dirty = false;
let view = 'doc';            // doc | config | readme | setup
let setup = null;            // 무엇이 되어 있는지 (서버가 센 값)
let mode = 'preview';        // 문서를 읽는 방식: preview | raw
let editing = false, cellEditing = null;
let readme = null, conf = null, confDirty = false;
let INDEX = { byArea:{}, byName:{} };

const $ = id => document.getElementById(id);
const pad = n => (n < 10 ? '0' : '') + n;
const dig = (o, k) => k.split('.').reduce((a, x) => (a || {})[x], o);
const narrow = () => window.matchMedia('(max-width:1100px)').matches;
const areaOf = p => (p || '').split('/')[0];

function el(tag, cls, text){
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}
function api(path, params){
  const q = new URLSearchParams(params || {}).toString();
  return q ? path + '?' + q : path;
}
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ============================================================ 마크다운
   보고서와 수집 원본, 사용 설명이 쓰는 문법만 다룬다. 들여쓴 목록을 단으로
   살려야 수집 원본이 읽힌다 - 지시 수십 개가 세션 정보와 같은 단으로 늘어서면
   무엇에 딸린 것인지 알 수 없다. */
function inline(s){
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function splitRow(line){
  return line.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|');
}
// cells: 표 칸에 원문 줄 번호를 달아 둘지. 그 자리에서 고치는 화면만 쓴다
function render(md, cells){
  const out = [], lines = md.split(/\r?\n/);
  const stack = [], liOpen = [];
  let i = 0, fence = null, para = [], quote = [], lastLi = false;

  const closeLi = () => {
    const n = liOpen.length - 1;
    if (n >= 0 && liOpen[n]) { out.push('</li>'); liOpen[n] = false; }
  };
  const closeList = () => { closeLi(); const s = stack.pop(); liOpen.pop(); out.push('</' + s.tag + '>'); };
  const closePara = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } };
  const closeQuote = () => { if (quote.length) { out.push('<blockquote>' + inline(quote.join(' ')) + '</blockquote>'); quote = []; } };
  const closeAll = () => { while (stack.length) closeList(); closePara(); closeQuote(); lastLi = false; };

  while (i < lines.length) {
    const ln = lines[i];
    if (fence !== null) {
      if (/^\s*```/.test(ln)) { out.push(esc(fence.join('\n'))); out.push('</code></pre>'); fence = null; }
      else fence.push(ln);
      i++; continue;
    }
    if (/^\s*```/.test(ln)) { closeAll(); out.push('<pre class="code"><code>'); fence = []; i++; continue; }

    const isTable = /^\s*\|/.test(ln) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1] || '');
    if (isTable) {
      closeAll();
      const cut = r => splitRow(r).map(c => c.trim());
      const head = cut(ln);
      out.push('<div class="tablewrap"><table' + (cells ? ' class="cells"' : '')
               + ' data-cols="' + head.length + '"><thead><tr>'
               + head.map(c => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        out.push('<tr>' + cut(lines[i]).map((c, j) =>
          '<td data-ln="' + i + '" data-c="' + j + '">' + inline(c) + '</td>').join('') + '</tr>');
        i++;
      }
      out.push('</tbody></table></div>');
      continue;
    }

    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) {
      closeAll();
      out.push('<h' + m[1].length + '>' + inline(m[2]) + '</h' + m[1].length + '>');
    }
    else if (/^\s*(---+|\*\*\*+|___+)\s*$/.test(ln)) { closeAll(); out.push('<hr>'); }
    else if ((m = ln.match(/^\s*>\s?(.*)$/))) { while (stack.length) closeList(); closePara(); quote.push(m[1]); lastLi = false; }
    else if ((m = ln.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/))) {
      closePara(); closeQuote();
      const indent = m[1].replace(/\t/g, '    ').length;
      const tag = /\d/.test(m[2]) ? 'ol' : 'ul';
      while (stack.length && indent < stack[stack.length - 1].indent) closeList();
      if (!stack.length || indent > stack[stack.length - 1].indent) {
        // 바로 위 항목 안으로 들어간다. 부모의 <li>는 열어 둔 채로 중첩한다
        out.push('<' + tag + '>'); stack.push({ tag: tag, indent: indent }); liOpen.push(false);
      } else {
        closeLi();
        if (stack[stack.length - 1].tag !== tag) {
          closeList();
          out.push('<' + tag + '>'); stack.push({ tag: tag, indent: indent }); liOpen.push(false);
        }
      }
      out.push('<li>' + inline(m[3]));
      liOpen[liOpen.length - 1] = true;
      lastLi = true;
    }
    else if (ln.trim() === '') { closeAll(); }
    else if (lastLi && /^\s{2,}\S/.test(ln)) {
      // 한 항목이 여러 줄에 걸친 경우. 새 항목으로 떼면 목록이 두 배로 길어진다
      out[out.length - 1] += ' ' + inline(ln.trim());
    }
    else { while (stack.length) closeList(); lastLi = false; para.push(ln); }
    i++;
  }
  closeAll();
  return out.join('\n');
}

/* ============================================================ 이름 붙이기
   파일 이름은 2026-10-01 이지만 사람이 찾는 단서는 요일이다 */
function parseName(name){
  let m = name.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return { kind:'day', y:+m[1], m:+m[2], d:+m[3] };
  m = name.match(/^(\d{4})-(\d{2})-(\d{2})_(\d{4})-(\d{2})-(\d{2})$/);
  if (m) return { kind:'span', y:+m[1], m:+m[2], d:+m[3], m2:+m[5], d2:+m[6] };
  return null;
}
function dayOf(p){ return WD[new Date(p.y, p.m - 1, p.d).getDay()]; }
// 레일의 달 묶음 안이라 연도를 뺀다
function shortLabel(name){
  const p = parseName(name);
  if (!p) return name;
  if (p.kind === 'day') return pad(p.m) + '-' + pad(p.d) + ' (' + dayOf(p) + ')';
  return pad(p.m) + '-' + pad(p.d) + ' ~ ' + pad(p.m2) + '-' + pad(p.d2);
}
function longLabel(name){
  const p = parseName(name);
  if (!p) return name;
  if (p.kind === 'day') return name + ' (' + dayOf(p) + ')';
  return p.y + '-' + pad(p.m) + '-' + pad(p.d) + ' ~ ' + pad(p.m2) + '-' + pad(p.d2);
}
function titleOf(path, fallback){
  if (!path) return fallback || 'work-report';
  const parts = path.split('/');
  const last = parts[parts.length - 1];
  if (parts[0] === 'custom') return (CUSTOM_NAME[last] || last) + ' · 내 양식';
  const name = last.replace(/\.(md|log)$/i, '');
  const area = AREA_NAME[parts[0]];
  return area ? longLabel(name) + ' · ' + area : name;
}

/* ============================================================ 문서 사이의 길
   요약에서 빠진 근거는 같은 날짜의 다른 문서에 있다. 왼쪽 트리를 다시
   헤치지 않고 건너갈 수 있어야 한다. */
function buildIndex(groups){
  const byArea = {}, byName = {};
  for (const g of groups) {
    const list = [];
    for (const mo of g.months) for (const it of mo.items) list.push(it);
    list.sort((a, b) => a.name < b.name ? 1 : (a.name > b.name ? -1 : 0));
    byArea[g.area] = list;
    for (const it of list) (byName[it.name] = byName[it.name] || {})[g.area] = it.path;
  }
  INDEX = { byArea: byArea, byName: byName };
}
function nameOf(path){ return (path.split('/').pop() || '').replace(/\.md$/i, ''); }
function siblingsOf(path){
  const area = areaOf(path), nm = nameOf(path);
  // 주간 보고에는 같은 이름의 수집 원본이 없다. 주간은 원본 대화가 아니라 그
  // 구간의 한 일 목록을 읽어서 쓰기 때문이다. 근거도 거기에 있다.
  const span = nm.split('_');
  if (area === 'weekly' && span.length === 2 && parseName(span[0]) && parseName(span[1])) {
    const days = (INDEX.byArea.log || [])
      .filter(it => it.name >= span[0] && it.name <= span[1])
      .sort((a, b) => a.name < b.name ? -1 : 1)
      .map(it => ({ area:'log', path:it.path, label: shortLabel(it.name) }));
    return { lead:'이 구간의 한 일 목록', items: days };
  }
  const row = INDEX.byName[nm] || {}, out = [];
  for (const a of ['daily', 'weekly', 'log', 'raw'])
    if (a !== area && row[a]) out.push({ area:a, path:row[a], label: AREA_NAME[a] });
  return { lead:'같은 날짜', items: out };
}
function neighborsOf(path){
  const list = INDEX.byArea[areaOf(path)] || [];
  let i = -1;
  for (let n = 0; n < list.length; n++) if (list[n].path === path) { i = n; break; }
  if (i < 0) return { prev:null, next:null };
  return { prev: list[i + 1] || null, next: list[i - 1] || null };   // 목록은 최신이 위다
}

/* ============================================================ 화면 상태
   어떤 단추가 보이는지는 여기 한 곳에서만 정한다. 화면마다 따로 숨기면
   읽을 수 없는 문서에 제출 단추가 남는 식으로 어긋난다. */
function setState(t, kind){
  const e = $('state');
  e.textContent = t || '';
  e.className = 'state' + (kind ? ' ' + kind : '');
}
function flash(t, kind){ setState(t, kind || 'good'); setTimeout(() => { if ($('state').textContent === t) setState(''); }, 2600); }
function setHead(title, sub){
  $('docTitle').textContent = title;
  $('docSub').textContent = sub || '';
  document.title = title;
}
function canCells(){
  return view === 'doc' && !editing && !!current
         && /\.md$/i.test(current) && areaOf(current) !== 'raw';
}
function renderPreview(){ $('view').innerHTML = render(raw, canCells()); }
function renderLive(){ $('liveView').innerHTML = render($('src').value, false); }

function markCurrent(){
  document.querySelectorAll('.doclink').forEach(a =>
    a.classList.toggle('on', view === 'doc' && a.dataset.path === current));
}
function drawRel(){
  const bar = $('rel');
  bar.textContent = '';
  const rel = (view === 'doc' && current && !editing) ? siblingsOf(current) : { lead:'', items:[] };
  if (!rel.items.length) { bar.hidden = true; return; }
  bar.appendChild(el('span', '', rel.lead));
  for (const s of rel.items) {
    const a = el('a', s.area === 'log' ? 'num' : '', s.label);
    a.href = '#';
    a.onclick = e => { e.preventDefault(); openReport(s.path); };
    bar.appendChild(a);
  }
  bar.hidden = false;
}
function layout(){
  const isDoc = view === 'doc', isReadme = view === 'readme', isSetup = view === 'setup';
  const area = areaOf(current);
  const writable = isDoc && !!current && /\.md$/i.test(current) && area !== 'raw';
  // 시작하기도 읽는 화면이라 같은 종이 위에 올린다
  const showPaper = (isDoc || isReadme || isSetup) && !editing;
  const live = $('editor').classList.contains('showlive');

  $('paper').hidden = !showPaper;
  $('view').hidden = !showPaper || (isDoc && mode === 'raw');
  $('rawView').hidden = !(showPaper && isDoc && mode === 'raw');
  $('editor').hidden = !(isDoc && editing);
  $('confView').hidden = view !== 'config';
  $('sheet').classList.toggle('editing', isDoc && editing);

  // 고치는 중에도 결과를 볼 수 있어야 한다. 넓은 화면은 좌우로 나누고,
  // 좁은 화면에서는 이 고르개가 두 쪽을 번갈아 보여 준다
  $('seg').hidden = !(isDoc && (!editing || narrow()));
  $('tabPreview').classList.toggle('on', editing ? live : mode === 'preview');
  $('tabRaw').classList.toggle('on', editing ? !live : mode === 'raw');

  $('edit').hidden = !(writable && !editing);
  $('cancel').hidden = !(isDoc && (editing || dirty));
  $('cancel').textContent = dirty ? '되돌리기' : '보기';
  $('save').hidden = !(isDoc && (editing || dirty));
  $('confSave').hidden = view !== 'config';
  // 제출은 보고서가 하는 일이다. 한 일 목록과 수집 원본은 근거라서 내지 않는다
  $('submit').hidden = !(SUBMIT_URL && isDoc && !editing && (area === 'daily' || area === 'weekly'));
  $('copy').hidden = !(isDoc && !editing && !!current);
  if (isDoc && current) $('copy').textContent = toCopy().part ? '제출문 복사' : '복사';
  // 일일보고일 때만. PMS 일일보고는 하루 한 건이고 주간 보고에는 대응하는 칸이 없다
  const pmsReady = conf && conf.pms ? !!conf.pms.url : PMS_ON;
  $('pmsFill').hidden = !(isDoc && !editing && area === 'daily' && pmsReady);

  const nb = (isDoc && current) ? neighborsOf(current) : { prev:null, next:null };
  $('flip').hidden = !(isDoc && !editing && (nb.prev || nb.next));
  $('prev').disabled = !nb.prev;
  $('next').disabled = !nb.next;
  $('prev').title = nb.prev ? '이전 - ' + longLabel(nb.prev.name) + '  ([)' : '이전  ([)';
  $('next').title = nb.next ? '다음 - ' + longLabel(nb.next.name) + '  (])' : '다음  (])';

  $('tabConfig').classList.toggle('on', view === 'config');
  $('tabReadme').classList.toggle('on', view === 'readme');
  $('tabSetup').classList.toggle('on', view === 'setup');
  markCurrent();
  drawRel();
}
function paint(){
  if (view === 'setup') { setHead('시작하기', '지금 무엇이 되어 있는지'); drawSetup(); }
  else if (view === 'readme') { setHead('사용 설명', 'GUIDE.md'); $('view').innerHTML = render(readme || '', false); }
  else if (view === 'config') { setHead('설정', 'config.json'); }
  else {
    setHead(titleOf(current), current + (areaOf(current) === 'raw' ? '   읽기 전용' : ''));
    if (mode === 'preview') renderPreview(); else $('rawView').textContent = raw;
  }
  layout();
}

/* ============================================================ 왼쪽 목록 */
function docLink(it, area, withTag){
  const a = el('a', 'doclink');
  a.href = '#';
  a.dataset.path = it.path;
  a.dataset.find = (it.name + ' ' + (AREA_NAME[area] || '')).toLowerCase();
  const t = el('span', 'num', withTag ? longLabel(it.name) : shortLabel(it.name));
  t.style.overflow = 'hidden';
  t.style.textOverflow = 'ellipsis';
  a.appendChild(t);
  if (withTag) a.appendChild(el('span', 'chip', AREA_TAG[area] || area));
  a.onclick = e => { e.preventDefault(); openReport(it.path); };
  return a;
}

async function loadFiles(){
  const d = await (await fetch(api('files'))).json();
  buildIndex(d.groups || []);
  drawCustom($('customBox'), d.custom || []);
  const box = $('files');
  const opened = new Set([...box.querySelectorAll('.month:not(.closed)')].map(e => e.dataset.key));
  const shut = new Set([...box.querySelectorAll('.group.closed')].map(e => e.dataset.area));
  const first = !box.querySelector('.group');   // 처음 그리는가, 다시 그리는가
  box.textContent = '';

  if (!d.groups.length) {
    box.appendChild(el('div', 'empty', '아직 보고서가 없습니다.'));
    return;
  }

  // 어제 쓴 보고서를 다시 여는 일이 가장 잦다. 접힌 목록을 헤치지 않게 위에 둔다
  const recent = [];
  for (const g of d.groups) {
    if (g.area !== 'daily' && g.area !== 'weekly') continue;
    for (const mo of g.months) for (const it of mo.items) recent.push({ it: it, area: g.area });
  }
  recent.sort((a, b) => a.it.name < b.it.name ? 1 : -1);
  if (recent.length) {
    const wrap = el('div', 'recent');
    wrap.id = 'recentBox';
    wrap.appendChild(el('div', 'sectitle', '최근'));
    recent.slice(0, 5).forEach(r => wrap.appendChild(docLink(r.it, r.area, true)));
    box.appendChild(wrap);
  }

  const all = el('div', 'sectitle', '전체');
  all.id = 'allHead';
  box.appendChild(all);

  for (const g of d.groups) {
    const area = el('div', 'group');
    area.dataset.area = g.area;
    if (first ? FOLDED.includes(g.area) : shut.has(g.area)) area.classList.add('closed');
    const total = g.months.reduce((n, mo) => n + mo.items.length, 0);

    const head = el('button', 'head');
    head.appendChild(caret());
    head.appendChild(document.createTextNode(g.label));
    head.appendChild(el('span', 'count', String(total)));
    head.onclick = () => area.classList.toggle('closed');
    area.appendChild(head);

    const abox = el('div', 'items');
    g.months.forEach((mo, mi) => {
      const key = g.area + '/' + mo.month;
      const month = el('div', 'month');
      month.dataset.key = key;
      // 처음에는 가장 최근 달만 펼친다. 쌓여도 목록이 길어지지 않는다.
      // 다시 그릴 때는 지금 펼쳐 둔 것을 그대로 둔다 - 전부 접어 두었다고 해서
      // 보고서를 만들 때마다 최근 달이 도로 열리면 접어 둔 뜻이 없다.
      if (!(first ? mi === 0 : opened.has(key))) month.classList.add('closed');
      const mhead = el('button', 'mhead');
      mhead.appendChild(caret());
      mhead.appendChild(document.createTextNode(mo.month));
      mhead.onclick = () => month.classList.toggle('closed');
      month.appendChild(mhead);
      const list = el('div', 'items');
      for (const it of mo.items) list.appendChild(docLink(it, g.area, false));
      month.appendChild(list);
      abox.appendChild(month);
    });
    area.appendChild(abox);
    box.appendChild(area);
  }
  applyFilter();
  markCurrent();
}
function caret(){
  const c = el('span', 'caret');
  c.innerHTML = '<svg width="9" height="9" viewBox="0 0 10 10" fill="currentColor"><path d="M1 3h8L5 8z"/></svg>';
  return c;
}

// 문서가 여든 개를 넘으면 트리를 훑는 것보다 날짜 몇 자를 치는 쪽이 빠르다
let preFilter = null;
function applyFilter(){
  const q = $('q').value.trim().toLowerCase();
  $('clearq').hidden = !q;
  if (q && !preFilter) {
    preFilter = new Set();
    document.querySelectorAll('#files .group.closed').forEach(e => preFilter.add(e.dataset.area));
    document.querySelectorAll('#files .month.closed').forEach(e => preFilter.add(e.dataset.key));
  }
  const rec = $('recentBox'), allHead = $('allHead');
  if (rec) rec.hidden = !!q;
  if (allHead) allHead.hidden = !!q;
  document.querySelectorAll('#files .doclink').forEach(a => {
    a.hidden = !!q && (a.dataset.find || '').indexOf(q) < 0;
  });
  document.querySelectorAll('#files .month').forEach(m => {
    const any = [...m.querySelectorAll('.doclink')].some(a => !a.hidden);
    m.hidden = !any;
    if (q) m.classList.remove('closed');
  });
  document.querySelectorAll('#files .group').forEach(g => {
    const any = [...g.querySelectorAll('.doclink')].some(a => !a.hidden);
    g.hidden = !any;
    if (q) g.classList.remove('closed');
  });
  if (!q && preFilter) {
    document.querySelectorAll('#files .group').forEach(g => g.classList.toggle('closed', preFilter.has(g.dataset.area)));
    document.querySelectorAll('#files .month').forEach(m => m.classList.toggle('closed', preFilter.has(m.dataset.key)));
    preFilter = null;
  }
}

// 내 양식. 보고서와 같은 .md라 뷰어가 그대로 열고 저장한다.
// 쓸지 말지는 설정이 정하므로 여기서는 지금 무엇을 쓰는지만 보인다.
function drawCustom(box, items){
  CUSTOM_STATE = items;
  box.textContent = '';
  if (!items.length) return;
  box.appendChild(el('div', 'sectitle custom', '내 양식'));
  for (const it of items) {
    const a = el('a', 'doclink');
    a.href = '#';
    a.style.paddingLeft = 'var(--sp-3)';
    a.appendChild(el('span', '', it.name));
    if (it.mine && it.exists) {
      a.dataset.path = it.path;
      a.onclick = e => { e.preventDefault(); openReport(it.path); };
    } else {
      a.classList.add('none');
      a.appendChild(el('span', 'chip', it.mine ? '다음 실행에 생김' : '기본값'));
      a.onclick = e => { e.preventDefault(); openConfig(); };
    }
    box.appendChild(a);
  }
}

/* ============================================================ 문서 열기 */
function canLeave(){
  if (dirty) {
    if (!confirm('저장하지 않은 수정이 있습니다. 그래도 넘어갈까요?')) return false;
    dirty = false;
  }
  if (confDirty) {
    if (!confirm('저장하지 않은 설정이 있습니다. 그래도 넘어갈까요?')) return false;
    confDirty = false;
  }
  return true;
}
async function openReport(path){
  if (!canLeave()) return;
  const r = await fetch(api('report', path ? { path } : {}));
  if (!r.ok) { setState('열지 못했습니다', 'warn'); return; }
  const d = await r.json();
  raw = d.text; orig = d.text; current = d.path;
  dirty = false; editing = false; cellEditing = null; view = 'doc';
  $('src').value = raw;
  $('rawView').textContent = raw;
  $('editor').classList.remove('showlive');
  // 실행 기록처럼 마크다운이 아닌 것은 꾸미지 않는다
  if (current && !/\.md$/i.test(current)) mode = 'raw';
  setState('');
  paint();
  $('sheet').scrollTop = 0;
  if (narrow() && window.innerWidth <= 860) hideSide();
}
function setRaw(text, mark){
  raw = text;
  $('src').value = text;
  $('rawView').textContent = text;
  if (mark) { dirty = true; setState('수정 중', 'warn'); }
}

/* ============================================================ 표 칸 고치기
   보고서에서 손대는 곳은 대개 수행 업무 표의 칸 하나다. 그걸 위해 원문
   전체를 열면 파이프 기호 사이에서 그 칸을 찾아야 한다. 두 번 눌러 그
   자리에서 고치고, 바꾼 값은 그 줄만 다시 쓴다. */
function startCell(td){
  if (cellEditing) return;
  const ln = +td.dataset.ln, c = +td.dataset.c;
  const parts = splitRow(raw.split(/\r?\n/)[ln] || '');
  if (parts[c] === undefined) return;
  cellEditing = td;
  td.textContent = parts[c].trim();
  td.classList.add('on');
  td.contentEditable = 'true';
  td.focus();
  const r = document.createRange();
  r.selectNodeContents(td);
  const sel = window.getSelection();
  sel.removeAllRanges();
  sel.addRange(r);
  setState('칸을 고치는 중 - Enter 끝냄, Tab 다음 칸, Esc 취소', 'warn');
}
function closeCell(){
  const td = cellEditing;
  cellEditing = null;
  if (td) { td.contentEditable = 'false'; td.classList.remove('on'); }
  return td;
}
function commitCell(step){
  const td = cellEditing;
  if (!td) return;
  const ln = +td.dataset.ln, c = +td.dataset.c;
  // 칸 안의 세로줄은 표를 쪼개므로 받지 않는다. 줄바꿈도 한 칸은 한 줄이다
  const value = td.textContent.replace(/\s+/g, ' ').replace(/\|/g, '/').trim();
  closeCell();
  const lines = raw.split(/\r?\n/);
  const parts = splitRow(lines[ln] || '');
  if (parts[c] !== undefined && parts[c].trim() !== value) {
    parts[c] = ' ' + value + ' ';
    lines[ln] = '|' + parts.join('|') + '|';
    setRaw(lines.join('\n'), true);
  } else {
    setState(dirty ? '수정 중' : '', dirty ? 'warn' : '');
  }
  renderPreview();
  layout();
  if (step) {
    const nxt = $('view').querySelector('td[data-ln="' + ln + '"][data-c="' + (c + step) + '"]');
    if (nxt) startCell(nxt);
  }
}
function cancelCell(){
  if (!cellEditing) return;
  closeCell();
  setState(dirty ? '수정 중' : '', dirty ? 'warn' : '');
  renderPreview();
}

/* ============================================================ 만들기 */
// PMS 작업은 결말이 여섯 가지다. 무엇을 해야 하는지가 상태마다 달라서
// "실패" 한 마디로는 사람이 다음 행동을 알 수 없다.
const PMS_TAIL = { running:' - 폼을 채우는 중', done:' 채웠습니다', opened:' - 폼만 열었습니다',
                   login:' - 로그인이 필요합니다', config:' - 설정이 비었습니다',
                   playwright:' - 준비물이 없습니다', env:' - 브라우저를 열 수 없습니다',
                   failed:' 실패' };

function pmsCard(j){
  const label = j.mode === 'pms-login' ? 'PMS 로그인' : 'PMS 폼 채우기';
  let box = $('job-' + j.id);
  if (!box) { box = el('div'); box.id = 'job-' + j.id; $('jobs').appendChild(box); }
  box.className = 'job ' + (j.state === 'running' ? 'running' : j.state === 'done' ? 'done'
                          : j.state === 'opened' ? 'skipped' : 'failed');
  box.textContent = '';

  const row = el('div', 'row');
  if (j.state !== 'running') row.appendChild(el('span', 'dot'));
  row.appendChild(el('span', 'what', label + (PMS_TAIL[j.state] || ' 실패')));
  row.appendChild(el('span', 'time', j.seconds + '초'));
  if (j.state !== 'running') {
    const x = el('button', 'x');
    x.title = '닫기';
    x.innerHTML = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>';
    x.onclick = async () => {
      box.remove();
      try { await fetch(api('dismiss', { id: j.id }), { method:'POST' }); } catch (e) {}
    };
    row.appendChild(x);
  }
  box.appendChild(row);

  if (j.state === 'running') {
    const bar = el('div', 'bar');
    bar.appendChild(el('i'));
    box.appendChild(bar);
    return;
  }
  // 스크립트가 한 말을 그대로 보여 준다. 안내가 로그에만 남으면 아무도 읽지 않는다
  const note = el('div', 'note');
  for (const line of (j.note || [])) note.appendChild(el('div', '', line));
  if (!(j.note || []).length) note.appendChild(document.createTextNode('runlog의 pms.log에 기록이 남습니다'));
  box.appendChild(note);

  // 바로 고칠 수 있는 것은 단추로 둔다
  if (j.state === 'done' && j.mode === 'pms') {
    // 창은 뒤에서 열린다. 하던 일을 끊지 않으려고 그렇게 했으니, 볼 길을 준다
    const b = el('button', 'quiet', 'PMS 창 보기');
    b.onclick = () => pmsRun('show');
    box.appendChild(b);
  } else if (j.state === 'login') {
    const b = el('button', 'quiet', '로그인 창 열기');
    b.onclick = async () => {
      await fetch(api('pms', { mode:'login' }), { method:'POST' });
      if (!polling) { polling = true; pollJobs(); }
    };
    box.appendChild(b);
  } else if (j.state === 'config') {
    const b = el('button', 'quiet', '설정 열기');
    b.onclick = () => show('config');
    box.appendChild(b);
  }
}

function jobCard(j){
  if (j.mode === 'pms' || j.mode === 'pms-login') return pmsCard(j);
  const label = (j.mode === 'daily' ? '일일보고' : '주간보고') + (j.external ? ' (예약 실행)' : '');
  let box = $('job-' + j.id);
  if (!box) { box = el('div'); box.id = 'job-' + j.id; $('jobs').appendChild(box); }
  box.className = 'job ' + j.state;
  box.textContent = '';

  const row = el('div', 'row');
  if (j.state !== 'running') row.appendChild(el('span', 'dot'));
  const tail = j.state === 'running' ? (j.phase === 'write' ? ' - 보고서 쓰는 중' : ' - 기록 모으는 중')
             : j.state === 'done' ? ' 완료'
             : j.state === 'skipped' ? ' 건너뜀' : ' 실패';
  row.appendChild(el('span', 'what', label + tail));
  row.appendChild(el('span', 'time', j.seconds + '초'));
  if (j.state !== 'running') {
    const x = el('button', 'x');
    x.title = '닫기';
    x.innerHTML = '<svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M5 5l10 10M15 5L5 15"/></svg>';
    x.onclick = async () => {
      box.remove();
      // 서버가 작업을 계속 들고 있으면 다음 폴링이 카드를 다시 만든다
      try { await fetch(api('dismiss', { id: j.id }), { method:'POST' }); } catch (e) {}
    };
    row.appendChild(x);
  }
  box.appendChild(row);

  if (j.state === 'running') {
    const bar = el('div', 'bar');
    bar.appendChild(el('i'));
    box.appendChild(bar);
  } else if (j.state === 'done') {
    const note = el('div', 'note');
    const paths = j.paths && j.paths.length ? j.paths : (j.path ? [j.path] : []);
    for (const p of paths) {
      const a = el('a', '', titleOf(p));
      a.href = '#';
      a.onclick = e => { e.preventDefault(); loadFiles().then(() => openReport(p)); };
      note.appendChild(a);
    }
    if (!paths.length) note.appendChild(document.createTextNode('새로 쓰인 보고서가 없습니다'));
    box.appendChild(note);
  } else if (j.state === 'skipped') {
    box.appendChild(el('div', 'note', '다른 실행이 진행 중이어서 물러났습니다'));
  } else {
    box.appendChild(el('div', 'note', '보고 폴더의 runlog에 이유가 남습니다'));
  }
}

let polling = false;
async function pollJobs(){
  let list;
  try { list = await (await fetch(api('jobs'))).json(); }
  catch (e) { polling = false; return; }
  const alive = new Set(list.map(j => 'job-' + j.id));
  // 서버가 더 들고 있지 않은 카드는 치운다 (예약 실행이 끝난 경우)
  [...$('jobs').children].forEach(c => { if (!alive.has(c.id)) c.remove(); });
  let running = false;
  for (const j of list) { jobCard(j); if (j.state === 'running') running = true; }
  if (running) { polling = true; setTimeout(pollJobs, 1500); }
  else { polling = false; loadFiles(); }
}
async function run(kind){
  await fetch(api('run', { mode: kind }), { method: 'POST' });
  if (!polling) { polling = true; pollJobs(); }
}

/* ============================================================ 설정
   화면에서 고치는 항목만 둔다. 설치가 채우는 경로 값은 여기에 없다. */
const WEEKDAYS = [
  { v:'Monday', t:'월요일' }, { v:'Tuesday', t:'화요일' }, { v:'Wednesday', t:'수요일' },
  { v:'Thursday', t:'목요일' }, { v:'Friday', t:'금요일' },
  { v:'Saturday', t:'토요일' }, { v:'Sunday', t:'일요일' }
];
const FIELDS = [
  { h:'보고서' },
  { k:'author',       t:'text',   label:'작성자', size:'short' },
  { k:'agent',        t:'select', label:'실행 CLI', opts:['claude', 'codex'] },
  { k:'submit_url',   t:'text',   label:'제출 화면 주소', size:'long',
    hint:'비우면 제출 단추가 사라진다' },
  { k:'submit_label', t:'text',   label:'제출 단추 문구', size:'mid' },
  { k:'notify',       t:'bool', def:true, label:'알림 사용' },

  { h:'내 양식',
    note:'켜는 순간 기본값이 보고 폴더의 custom\\ 에 복사된다.\n끄면 기본값으로 돌아가고, 고쳐 둔 파일은 지워지지 않는다' },
  { k:'custom_format',  t:'bool', def:false, label:'내 보고서 양식 쓰기',
    file:'custom/report-format.md', hint:'보고서 양식을 통째로 바꾼다' },
  { k:'custom_rules',   t:'bool', def:false, label:'내 글쓰기 문체 쓰기',
    file:'custom/writing-rules.md', hint:'기본 원칙 뒤에 덧붙인다' },
  { k:'custom_samples', t:'bool', def:false, label:'내 보고서 따라하기',
    file:'custom/my-reports.md',
    hint:'붙여넣은 지난 보고서를 문체 예시로 쓴다.' +
         '\n보고서 맨 앞에 그 문체로 쓴 제출문 절이 생긴다.' +
         '\n붙여넣은 것이 없으면 아무 일도 하지 않는다' },

  { h:'수집' },
  { k:'mine_only', t:'bool', def:true, label:'내 이메일의 커밋만' },
  { k:'redact',    t:'bool', def:true, label:'키·토큰 가리기' },
  { k:'exclude_repos', t:'lines', label:'제외할 저장소',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 제외된다.' +
         '\nex. my-project' +
         '\n폴더 이름만 적는 편이 확실하다. 저장소를 옮겨도 계속 걸린다.' +
         '\nex. /c/Users/me/project/my-project → 안 걸린다 (Git Bash 형식)' },
  { k:'exclude_paths', t:'lines', label:'작업으로 안 치는 경로',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 뺀다.' +
         '\nex. node_modules' +
         '\nex. \\build\\  (구분자는 \\ 로 적는다)' },

  { h:'PMS 일일보고' },
  { k:'pms.url',      t:'str',  label:'PMS 주소',
    hint:'예: https://pms.example.com' + NL + '비우면 이 기능은 꺼진다' },
  { k:'pms.project',  t:'str',  label:'올릴 프로젝트',
    hint:'PMS 주소의 /projects/<여기> 부분' },
  { k:'pms.projects', t:'lines', label:'지난 보고서를 받을 프로젝트',
    hint:'한 줄에 하나. 비우면 위의 프로젝트만 본다' + NL + '프로젝트를 옮긴 적이 있으면 예전 것도 적는다' },
  { h:'실행' },
  { k:'backfill_days',    t:'num', label:'빠뜨린 날 채우기', hint:'며칠 전까지. 0이면 안 함' },
  { k:'retain_months',    t:'num', label:'수집본 보관(개월)',
    hint:'0이면 전부 보관. 보고서는 지우지 않음' },
  { k:'weekly.end_day',   t:'select', label:'주간 마지막 요일', opts:WEEKDAYS },
  { k:'weekly.span_days', t:'num', label:'주간 기간(일)' },

  { h:'실행 파일', note:'비워 두면 자동으로 찾는다. 흐린 글씨가 지금 찾아 둔 경로다' },
  { k:'claude_bin', t:'text', label:'claude 경로', size:'long', probe:'claude', hint:'찾는 중...' },
  { k:'codex_bin',  t:'text', label:'codex 경로',  size:'long', probe:'codex',  hint:'찾는 중...' },
  { k:'python_bin', t:'text', label:'python 경로', size:'long', probe:'python', hint:'찾는 중...' },
];
let CUSTOM_STATE = [];

// 빈 칸이 "설정이 안 됐다"로 읽히지 않게, 지금 쓰는 경로를 흐린 글씨로 채운다.
// 값이 아니라 안내이므로 저장해도 덮어쓰지 않는다 - 비워 두면 계속 자동으로 찾는다
async function fillBins(){
  let found;
  try { found = await (await fetch(api('bins'))).json(); }
  catch (e) { found = {}; }
  for (const f of FIELDS) {
    if (!f.probe) continue;
    const input = $('f_' + f.k.replace('.', '_'));
    if (!input) continue;
    const got = found[f.probe];
    input.placeholder = got ? got.path : '찾지 못했습니다';
    const row = input.closest('.field');
    const hint = row && row.querySelector('.hint');
    if (!hint) continue;
    hint.textContent = got ? '자동으로 찾았습니다 - ' + got.version
                           : '자동으로 찾지 못했습니다. 전체 경로를 적어 주세요';
  }
}

function drawConfig(){
  const box = $('confView');
  box.textContent = '';
  box.appendChild(el('div', 'lede', '바꾼 값은 다음 실행부터 적용됩니다.'));

  let card = null;
  for (const f of FIELDS) {
    if (f.h) {
      card = el('div', 'card');
      card.appendChild(el('h3', '', f.h));
      if (f.note) {
        const n = el('div', 'hint', f.note);
        n.style.gridColumn = 'auto';
        n.style.margin = '0 0 ' + '4px';
        card.appendChild(n);
      }
      box.appendChild(card);
      continue;
    }
    if (!card) { card = el('div', 'card'); box.appendChild(card); }

    const row = el('div', 'field');
    const lab = el('label', '', f.label);
    row.appendChild(lab);

    let input;
    const val = dig(conf, f.k);
    if (f.t === 'bool') {
      input = el('input');
      input.type = 'checkbox';
      input.className = 'switch';
      // 값이 없으면 항목이 정한 기본값이다. 전부 켜짐으로 그리면 기본이
      // 거짓인 설정이 저장하는 순간 켜져 버린다.
      input.checked = (val === undefined || val === null) ? !!f.def : !!val;
    } else if (f.t === 'num') {
      input = el('input');
      input.type = 'number';
      input.value = val == null ? '' : val;
    } else if (f.t === 'select') {
      input = el('select');
      for (const o of f.opts) {
        const op = el('option');
        op.value = (o && o.v !== undefined) ? o.v : o;
        op.textContent = (o && o.t !== undefined) ? o.t : o;
        input.appendChild(op);
      }
      const first = f.opts[0];
      input.value = val || ((first && first.v !== undefined) ? first.v : first);
    } else if (f.t === 'lines') {
      input = el('textarea');
      input.className = 'lines';
      input.value = (val || []).join('\n');
    } else {
      input = el('input');
      input.type = 'text';
      input.className = f.size || 'mid';
      input.value = val == null ? '' : val;
    }
    input.dataset.key = f.k;
    input.dataset.type = f.t;
    input.id = 'f_' + f.k.replace('.', '_');
    lab.htmlFor = input.id;

    // 켜고 끄는 곳과 고치러 가는 곳이 갈라져 있으면 한 가지 일이 두 화면에
    // 나뉜다. 켜져 있고 파일이 있으면 그 자리에서 열 수 있게 한다.
    if (f.file) {
      const withOpen = el('div', 'with');
      withOpen.appendChild(input);
      const state = CUSTOM_STATE.filter(c => c.path === f.file)[0];
      if (state && state.exists && input.checked) {
        const a = el('a', 'open', '열어서 고치기');
        a.href = '#';
        a.onclick = e => { e.preventDefault(); openReport(f.file); };
        withOpen.appendChild(a);
      }
      row.appendChild(withOpen);
    } else {
      row.appendChild(input);
    }

    if (f.hint) row.appendChild(el('div', 'hint', f.hint));
    card.appendChild(row);
  }

  // 저장하지 않고 떠나면 조용히 사라지던 것을 막는다
  box.oninput = () => { if (!confDirty) { confDirty = true; setState('수정 중', 'warn'); } };
  box.onchange = box.oninput;

  $('confSave').onclick = async () => {
    const out = { weekly: {} };
    box.querySelectorAll('[data-key]').forEach(input => {
      const k = input.dataset.key, t = input.dataset.type;
      let v;
      if (t === 'bool') v = input.checked;
      else if (t === 'num') v = input.value === '' ? 0 : Number(input.value);
      else if (t === 'lines') v = input.value.split(/\r?\n/);
      else v = input.value;
      // 점이 있는 키는 그 앞을 묶음 이름으로 본다. weekly 만 특별히 다루면
      // 묶음을 더할 때마다 이 줄을 또 고쳐야 한다.
      const dot = k.indexOf('.');
      if (dot > 0) { const g = k.slice(0, dot); (out[g] = out[g] || {})[k.slice(dot + 1)] = v; }
      else out[k] = v;
    });
    const r = await fetch(api('config'), { method:'POST',
      headers:{ 'Content-Type':'application/json' }, body: JSON.stringify(out) });
    if (!r.ok) { setState('저장하지 못했습니다', 'warn'); return; }
    conf = await r.json();
    confDirty = false;
    // 양식 토글을 바꿨으면 왼쪽 목록이 바로 따라와야 한다. 켜면 그 자리에서
    // 파일이 생기므로 새로 고치지 않아도 열 수 있다.
    await loadFiles();
    drawConfig();
    fillBins();
    flash('저장했습니다');
  };
}

/* ============================================================ 복사와 제출
   제출문 절이 있으면 그것만, 없으면 보고서 전체를 준다. 무엇을 담았는지는
   단추 이름과 눌렀을 때의 말로 알린다 - 조용히 일부만 복사되면 붙여넣고
   나서야 안다. */
function toCopy(){
  const lines = (editing ? $('src').value : raw).split(/\r?\n/);
  const isHead = s => /^##\s/.test(s);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isHead(lines[i]) && lines[i].indexOf(SUBMIT_HEAD) >= 0) { start = i + 1; break; }
  }
  if (start < 0) return { body: lines.join('\n'), part: false };
  let end = lines.length;
  for (let i = start; i < lines.length; i++) { if (isHead(lines[i])) { end = i; break; } }
  const body = lines.slice(start, end).join('\n').trim();
  return body ? { body: body, part: true } : { body: lines.join('\n'), part: false };
}

/* ============================================================ 화면 밝기 */
const THEME_ICON = {
  system:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="10" cy="10" r="6.4"/><path d="M10 3.6a6.4 6.4 0 0 1 0 12.8z" fill="currentColor" stroke="none"/></svg>',
  light:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="10" cy="10" r="3.4"/><path d="M10 2.2v1.6M10 16.2v1.6M17.8 10h-1.6M3.8 10H2.2M15.5 4.5l-1.1 1.1M5.6 14.4l-1.1 1.1M15.5 15.5l-1.1-1.1M5.6 5.6 4.5 4.5"/></svg>',
  dark:'<svg width="16" height="16" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M15.8 12.6A6.3 6.3 0 0 1 7.4 4.2a6.5 6.5 0 1 0 8.4 8.4z"/></svg>'
};
const THEME_NAME = { system:'자동', light:'밝게', dark:'어둡게' };
function applyTheme(t){
  if (t === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  $('theme').innerHTML = THEME_ICON[t];
  $('theme').title = '화면 밝기: ' + THEME_NAME[t];
  $('theme').dataset.now = t;
  try { localStorage.setItem('wr-theme', t); } catch (e) {}
}

/* ============================================================ 왼쪽 레일 접기 */
function syncScrim(){
  const need = window.innerWidth <= 860 && !$('side').classList.contains('hide');
  let s = document.querySelector('.scrim');
  if (need && !s) {
    s = el('div', 'scrim');
    s.onclick = () => { $('side').classList.add('hide'); syncScrim(); };
    document.querySelector('.main').appendChild(s);
  }
  if (!need && s) s.remove();
}
function hideSide(){ $('side').classList.add('hide'); syncScrim(); }

/* ============================================================ 이어 붙이기 */
$('home').onclick = () => { mode = 'preview'; openReport(''); };
$('toggleSide').onclick = () => { $('side').classList.toggle('hide'); syncScrim(); };
$('theme').onclick = () => {
  const order = ['system', 'light', 'dark'];
  const now = $('theme').dataset.now || 'system';
  applyTheme(order[(order.indexOf(now) + 1) % order.length]);
};
$('tabPreview').onclick = () => {
  if (editing) { $('editor').classList.add('showlive'); renderLive(); }
  else mode = 'preview';
  paint();
};
$('tabRaw').onclick = () => {
  if (editing) $('editor').classList.remove('showlive');
  else mode = 'raw';
  paint();
};
$('tabReadme').onclick = async () => {
  if (!canLeave()) return;
  if (readme === null) readme = await (await fetch(api('readme'))).text();
  view = 'readme'; editing = false;
  paint();
  $('sheet').scrollTop = 0;
};
// 차례대로 넘기는 마법사가 아니라 상태판이다. 사람마다 도착 지점이 다르고,
// 이미 해 둔 것을 다시 묻는 화면은 길잡이가 아니라 방해다.
// 각 줄은 "무엇이 되는가 / 지금 어떤가 / 지금 할 수 있는 것" 셋으로만 쓴다.
function setupSteps(st){
  const p = st.pms || {}, h = p.harvest || {};
  const reports = (st.reports || {}).daily || 0;
  const seen = st.seen || [];
  return [
    { ok: reports > 0,
      title: '보고서 만들기',
      done: '일일보고 ' + reports + '건이 쌓여 있습니다',
      todo: '아직 보고서가 없습니다. 평일 저녁에 저절로 만들어지고, 지금 만들 수도 있습니다',
      act: reports > 0 ? null : { label:'지금 만들어 보기', run:() => run('daily') } },

    { ok: !!st.custom['report-format.md'] || !!st.custom['writing-rules.md'] || seen.indexOf('custom') >= 0,
      title: '내 양식과 문체',
      done: (st.custom['report-format.md'] || st.custom['writing-rules.md'])
            ? '내 것을 쓰고 있습니다 (왼쪽 아래 "내 양식"에서 고칩니다)'
            : '기본값을 쓰고 있습니다. 회사 양식이 다르거나 말투를 바꾸려면 설정에서 켭니다',
      todo: '회사 양식이 다르거나 말투를 바꾸려면 설정에서 켭니다. 켜지 않으면 기본값을 씁니다',
      act: { label:'설정 보기', run:() => { markSeen('custom'); openConfig(); } } },

    { ok: !!p.url,
      title: 'PMS에 자동으로 채우기',
      done: '보고서를 PMS 일일보고 폼에 채워 줍니다',
      todo: 'PMS 주소와 프로젝트를 적으면 보고서를 그 폼에 채워 줍니다',
      fields: [{ k:'pms.url', label:'PMS 주소', hint:'예: https://pms.cemware.com', value: p.url },
               { k:'pms.project', label:'프로젝트', hint:'예: sai — 주소의 /projects/<여기> 부분', value: p.project }] },

    { ok: !!p.has_token, need: !!p.url,
      title: 'PMS 토큰 (일감을 연결하려면)',
      done: '토큰이 들어 있습니다. 보고서를 쓸 때 열린 일감 목록을 받아 와 맞는 것만 연결합니다',
      todo: 'PMS의 "내 계정 > API 접근키"를 넣으면 그날 일과 맞는 일감을 연결해 줍니다. 없어도 나머지는 다 됩니다',
      fields: [{ k:'pms.token', label:'API 접근키', secret:true, saved: !!p.has_token,
                 hint:'PMS 오른쪽 위 "내 계정" 화면 아래쪽에 있습니다', value:'' }],
      optional: true },

    { ok: !!p.signed_in, need: !!p.url,
      title: 'PMS 로그인',
      done: '전용 창에 로그인되어 있습니다 (평소 쓰는 브라우저와 따로입니다)',
      todo: '전용 창에서 한 번만 로그인하면 됩니다. 비밀번호는 저장하지 않습니다',
      act: { label:'로그인 창 열기', run:() => pmsRun('login') } },

    { ok: h.count > 0, need: !!p.url,
      title: '내가 쓰던 보고서 가져오기',
      done: h.count + '건을 받아 두었습니다 (최근 ' + (h.latest || '-') + '). 그 문체와 분류를 따라 씁니다',
      todo: 'PMS에 올렸던 지난 보고서를 받아 오면 그 문체와 분류 습관대로 씁니다. 없으면 기본 문체로 씁니다',
      act: { label: h.count > 0 ? '다시 가져오기' : '최근 3달치 가져오기', run:() => pmsRun('fetch') } },

    { ok: !!st.has_submission, need: !!p.url,
      title: '제출해 보기',
      done: '가장 최근 일일보고에 제출문이 있습니다. 열어서 "PMS에 채우기"를 누르면 됩니다',
      todo: '제출문 절이 있는 보고서가 아직 없습니다. 다음 보고서부터 생깁니다',
      act: st.latest_daily ? { label:'그 보고서 열기', run:() => loadFiles().then(() => openReport(st.latest_daily)) } : null }
  ];
}

async function pmsRun(mode){
  await fetch(api('pms', { mode: mode }), { method:'POST' });
  flash(mode === 'login' ? '로그인 창을 엽니다'
      : mode === 'show' ? 'PMS 창을 가져옵니다' : '지난 보고서를 받아 오는 중입니다');
  if (!polling) { polling = true; pollJobs(); }
}

async function markSeen(what){
  try { await fetch(api('seen', { what: what }), { method:'POST' }); } catch (e) {}
}

// 단계 안에서 바로 고칠 수 있게 한다. "설정 열기"만 두면 화면을 옮겨 다니다
// 어디까지 했는지 잃는다.
function stepFields(st, body){
  const inputs = [];
  for (const f of st.fields) {
    const row = el('div', 'sfield');
    row.appendChild(el('label', '', f.label + (f.saved ? '  ✓ 들어 있음' : '')));
    const i = document.createElement('input');
    i.type = f.secret ? 'password' : 'text';
    i.value = f.value || '';
    if (f.secret) {
      i.autocomplete = 'off';
      // 값을 돌려받지 않으므로 칸은 비어 있다. 비어 있는 칸은 "안 넣었다"로
      // 읽히므로, 들어 있다는 사실은 칸 안에 적어 둔다.
      i.placeholder = f.saved ? '저장되어 있습니다 - 바꾸려면 새 값을 붙여넣으세요' : '붙여넣기';
      if (f.saved) row.classList.add('filled');
    }
    // 안내는 칸 아래 한 줄로만 둔다. placeholder 와 겹쳐 적으면 두 번 읽힌다
    row.appendChild(i);
    if (f.hint) row.appendChild(el('div', 'shint', f.hint));
    body.appendChild(row);
    inputs.push({ k: f.k, el: i, secret: !!f.secret });
  }
  const b = el('button', 'quiet', '저장');
  b.onclick = async () => {
    const out = {};
    for (const i of inputs) {
      const v = i.el.value.trim();
      if (i.secret && !v) continue;          // 빈 칸은 "그대로 둬라"는 뜻이다
      const dot = i.k.indexOf('.');
      const g = i.k.slice(0, dot);
      (out[g] = out[g] || {})[i.k.slice(dot + 1)] = v;
    }
    const r = await fetch(api('config'), { method:'POST',
      headers:{'Content-Type':'application/json'}, body: JSON.stringify(out) });
    if (!r.ok) { setState('저장하지 못했습니다', true); return; }
    conf = await r.json();
    flash('저장했습니다');
    await openSetup();                        // 상태를 다시 읽어 체크를 갱신한다
  };
  body.appendChild(b);
}

function drawSetup(){
  const host = $('view');
  host.textContent = '';
  const box = el('div', 'setup');          // #view 의 class 는 그대로 둔다
  host.appendChild(box);
  const steps = setupSteps(setup || { reports:{}, custom:{}, pms:{} });
  const left = steps.filter(s => !s.ok && !s.optional).length;
  box.appendChild(el('h1', 'setuphead',
    left ? '아직 ' + left + '가지가 남았습니다' : '다 되어 있습니다'));
  box.appendChild(el('div', 'setuplede',
    left ? '아래에서 바로 채울 수 있습니다' : '설정을 바꾸고 싶으면 아래에서 고칩니다'));

  for (const st of steps) {
    const row = el('div', 'step' + (st.ok ? ' ok' : '') + (st.need === false ? ' wait' : ''));
    const mark = el('span', 'mark', st.ok ? '✓' : (st.need === false ? '·' : '○'));
    row.appendChild(mark);
    const body = el('div', 'body');
    body.appendChild(el('div', 't', st.title + (st.optional && !st.ok ? ' (선택)' : '')));
    body.appendChild(el('div', 'd', st.ok ? st.done : st.todo));
    // 끝난 줄에서도 단추는 남긴다. 다시 가져오거나 설정을 다시 여는 일은
    // 처음 한 번으로 끝나지 않는다.
    if (st.need === false) body.appendChild(el('div', 'd', '위의 PMS 주소를 먼저 채우면 할 수 있습니다'));
    else if (st.fields) stepFields(st, body);
    else if (st.act) {
      const b = el('button', 'quiet', st.act.label);
      b.onclick = st.act.run;
      body.appendChild(b);
    }
    row.appendChild(body);
    box.appendChild(row);
  }
}

async function openSetup(){
  if (!canLeave()) return;
  setup = await (await fetch(api('setup'))).json();
  view = 'setup'; editing = false;
  paint();
  $('sheet').scrollTop = 0;
}

async function openConfig(){
  if (!canLeave()) return;
  if (conf === null) conf = await (await fetch(api('config'))).json();
  view = 'config'; editing = false;
  drawConfig();
  paint();
  $('sheet').scrollTop = 0;
  fillBins();
}
$('tabConfig').onclick = openConfig;
$('tabSetup').onclick = openSetup;
$('pmsFill').onclick = async () => {
  const r = await fetch(api('pms', { path: current }), { method:'POST' });
  if (!r.ok) { setState(await r.text(), true); return; }
  flash('PMS 폼을 채우는 중입니다');
  if (!polling) { polling = true; pollJobs(); }
};
$('runDaily').onclick = () => run('daily');
$('runWeekly').onclick = () => run('weekly');
$('prev').onclick = () => { const n = neighborsOf(current).prev; if (n) openReport(n.path); };
$('next').onclick = () => { const n = neighborsOf(current).next; if (n) openReport(n.path); };
$('q').oninput = applyFilter;
$('clearq').onclick = () => { $('q').value = ''; applyFilter(); $('q').focus(); };

$('edit').onclick = () => {
  if (cellEditing) commitCell(0);
  editing = true;
  $('editor').classList.toggle('showlive', false);
  paint();
  renderLive();
  setTimeout(() => $('src').focus(), 0);
};
$('cancel').onclick = () => {
  if (dirty && !confirm('고친 것을 버리고 저장된 내용으로 돌아갑니다. 계속할까요?')) return;
  const had = dirty;
  setRaw(orig, false);
  dirty = false; editing = false; cellEditing = null;
  setState('');
  paint();
  if (had) flash('되돌렸습니다');
};
$('save').onclick = async () => {
  if (cellEditing) commitCell(0);
  const body = editing ? $('src').value : raw;
  const r = await fetch(api('report', { path: current }),
                        { method:'POST', headers:{ 'Content-Type':'text/plain; charset=utf-8' }, body: body });
  if (!r.ok) { setState('저장하지 못했습니다', 'warn'); return; }
  raw = body; orig = body; dirty = false;
  $('rawView').textContent = raw;
  if (!editing) renderPreview();
  layout();
  flash('저장했습니다');
};
let liveTimer = null;
$('src').oninput = () => {
  raw = $('src').value;
  if (!dirty) { dirty = true; setState('수정 중', 'warn'); layout(); }
  clearTimeout(liveTimer);
  liveTimer = setTimeout(renderLive, 180);
};
$('copy').onclick = async () => {
  const c = toCopy();
  try { await navigator.clipboard.writeText(c.body); }
  catch (e) { setState('복사하지 못했습니다', 'warn'); return; }
  flash(c.part ? '제출문을 복사했습니다' : '클립보드에 복사했습니다');
};
$('submit').onclick = async () => {
  const c = toCopy();
  try { await navigator.clipboard.writeText(c.body); } catch (e) {}
  flash(c.part ? '제출문을 복사했습니다. 붙여넣으세요' : '복사했습니다. 붙여넣으세요');
  window.open(SUBMIT_URL, '_blank', 'noopener');
};

$('view').addEventListener('dblclick', e => {
  const td = e.target.closest ? e.target.closest('td[data-ln]') : null;
  if (td && canCells()) startCell(td);
});
$('view').addEventListener('keydown', e => {
  if (!cellEditing) return;
  if (e.key === 'Enter') { e.preventDefault(); commitCell(0); }
  else if (e.key === 'Tab') { e.preventDefault(); commitCell(e.shiftKey ? -1 : 1); }
  else if (e.key === 'Escape') { e.preventDefault(); cancelCell(); }
});
$('view').addEventListener('focusout', e => { if (cellEditing && e.target === cellEditing) commitCell(0); });

window.addEventListener('resize', () => { syncScrim(); layout(); });
window.addEventListener('beforeunload', e => {
  if (dirty || confDirty) { e.preventDefault(); e.returnValue = ''; }
});
document.addEventListener('keydown', e => {
  const t = e.target, tag = (t.tagName || '').toLowerCase();
  const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || t.isContentEditable;
  if ((e.ctrlKey || e.metaKey) && (e.key === 's' || e.key === 'S')) {
    if (!$('save').hidden) { e.preventDefault(); $('save').click(); }
    else if (!$('confSave').hidden) { e.preventDefault(); $('confSave').click(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key === '\\') { e.preventDefault(); $('toggleSide').click(); return; }
  if (e.key === 'Escape') {
    if (cellEditing) { e.preventDefault(); cancelCell(); }
    else if (t === $('q') && $('q').value) { $('q').value = ''; applyFilter(); }
    else if (editing) { e.preventDefault(); $('cancel').click(); }
    return;
  }
  if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === '/') { e.preventDefault(); $('q').focus(); $('q').select(); }
  else if (e.key === '[' && !$('flip').hidden && !$('prev').disabled) $('prev').click();
  else if (e.key === ']' && !$('flip').hidden && !$('next').disabled) $('next').click();
});

(async () => {
  let saved = 'system';
  try { saved = localStorage.getItem('wr-theme') || 'system'; } catch (e) {}
  applyTheme(saved);
  if (!HAS_README) $('tabReadme').hidden = true;
  if (window.innerWidth <= 860) $('side').classList.add('hide');
  const now = new Date();
  $('runDaily').title = '오늘(' + pad(now.getMonth() + 1) + '-' + pad(now.getDate()) + ')까지 모아서 다시 만든다';
  $('runWeekly').title = '이번 주간 구간을 다시 만든다';
  try {
    await loadFiles();
    await openReport(START);
    // 아직 아무것도 없는 사람에게는 문서 대신 길잡이를 먼저 보여 준다.
    // 쓰던 사람의 첫 화면은 그대로 둔다.
    if (!START && !current) await openSetup();
  } catch (e) {
    $('view').innerHTML = '<p>보고서를 읽지 못했습니다: ' + esc(String(e)) + '</p>';
  }
  // 새로 고치기 전에 시작한 작업과 예약 실행도 여기서 이어 받는다
  pollJobs();
})();
</script>
</body></html>
"""


def make_handler(root, report_path, page, readme_path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _touch(self):
            global last_seen
            last_seen = time.time()

        def _authorized(self):
            # 127.0.0.1에만 붙으므로 다른 PC는 닿지 못하고, 같은 PC의 프로그램은
            # 어차피 보고서 파일을 직접 열 수 있다. 남는 위험은 브라우저로 연
            # 바깥 웹페이지가 이 포트를 두드리는 경우뿐인데, 그런 요청에는
            # 브라우저가 Origin을 붙이므로 우리 것이 아니면 거절한다.
            origin = self.headers.get('Origin')
            return not origin or origin == 'http://%s' % self.headers.get('Host', '')

        def _send(self, code, body=b'', ctype='text/plain; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj):
            self._send(200, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _parts(self):
            path, _, qs = self.path.partition('?')
            query = {}
            for kv in qs.split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    query[k] = unquote(v)
            return path.rstrip('/').rsplit('/', 1)[-1], query

        def _target(self, query, exts=('.md',)):
            rel = query.get('path')
            return safe_join(root, rel, exts) if rel else report_path

        def do_GET(self):
            self._touch()
            if not self._authorized():
                return self._send(403, b'forbidden')
            try:
                return self._get()
            except Exception as e:
                # 창 없이 뜨므로 여기서 안 적으면 아무 데도 안 남는다
                log_error(root, 'viewer', 'GET %s' % self.path, e)
                return self._send(500, b'error')

        def _get(self):
            leaf, query = self._parts()
            if leaf == 'files':
                return self._json({'groups': list_reports(root),
                                   'custom': list_custom(root)})
            if leaf == 'jobs':
                return self._json(job_status(root))
            if leaf == 'config':
                return self._json(read_config(root))
            if leaf == 'bins':
                return self._json(resolve_bins(root))
            if leaf.endswith('.woff2'):
                # 글꼴은 함께 들어 있다. 인터넷이 막힌 PC에서도 같게 보인다
                target = asset_path(leaf)
                if not target:
                    return self._send(404, b'no asset')
                with io.open(target, 'rb') as fh:
                    return self._send(200, fh.read(), 'font/woff2')
            if leaf == 'favicon.ico':
                if not ICON_PATH:
                    return self._send(404, b'no icon')
                with io.open(ICON_PATH, 'rb') as fh:
                    return self._send(200, fh.read(), 'image/x-icon')
            if leaf == 'setup':
                return self._json(setup_status(root))
            if leaf == 'readme':
                if not readme_path:
                    return self._send(404, b'no readme')
                with io.open(readme_path, encoding='utf-8') as fh:
                    return self._send(200, fh.read().encode('utf-8'))
            if leaf == 'report':
                target = self._target(query, ('.md', '.log'))
                if not target:
                    # 경로를 줬는데 못 열었으면 실패다. 빈 문서로 덮으면 드러나지 않는다
                    if query.get('path'):
                        return self._send(404, b'not found')
                    # 갓 설치해 보고서가 하나도 없는 경우
                    return self._json({'name': 'work-report', 'path': '',
                                       'text': EMPTY_DOC})
                with io.open(target, encoding='utf-8') as fh:
                    text = fh.read()
                return self._json({'name': os.path.basename(target),
                                   'path': os.path.relpath(target, root).replace(os.sep, '/'),
                                   'text': text})
            return self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')

        def do_POST(self):
            self._touch()
            if not self._authorized():
                return self._send(403, b'forbidden')
            try:
                return self._post()
            except Exception as e:
                log_error(root, 'viewer', 'POST %s' % self.path, e)
                return self._send(500, b'error')

        def _post(self):
            leaf, query = self._parts()
            if leaf == 'config':
                length = int(self.headers.get('Content-Length') or 0)
                incoming = json.loads(self.rfile.read(length).decode('utf-8'))
                return self._json(write_config(root, incoming))
            if leaf == 'run':
                job_id = start_job(root, query.get('mode'))
                if not job_id:
                    return self._send(400, b'bad mode')
                return self._json({'id': job_id})
            if leaf == 'seen':
                what = query.get('what')
                if what in SEEN_KEYS:
                    cfg = read_config(root)
                    seen = [x for x in (cfg.get('setup_seen') or []) if x in SEEN_KEYS]
                    if what not in seen:
                        seen.append(what)
                        cfg['setup_seen'] = seen
                        with io.open(os.path.join(root, 'config.json'), 'w',
                                     encoding='utf-8', newline='') as fh:
                            fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
                return self._json({'ok': True})
            if leaf == 'pms':
                job_id, why = start_pms(root, query.get('path'), query.get('mode') or 'fill')
                if not job_id:
                    return self._send(400, (why or 'bad request').encode('utf-8'))
                return self._json({'id': job_id})
            if leaf == 'dismiss':
                return self._json({'dropped': drop_job(query.get('id'))})
            target = self._target(query)
            if not target:
                return self._send(404, b'not found')
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length).decode('utf-8')
            with io.open(target, 'w', encoding='utf-8', newline='') as fh:
                fh.write(body)
            return self._send(200, b'saved')

        def log_message(self, *args):
            pass                      # 콘솔을 조용히 둔다

    return Handler


def main():
    if len(sys.argv) < 2:
        print('usage: viewer.py <report.md | report folder>')
        return 2

    given = os.path.abspath(sys.argv[1])
    if os.path.isdir(given):
        root = given
        report = newest_report(root)          # 아직 하나도 없을 수 있다
    elif os.path.isfile(given):
        report = given
        _cfg, root = load_config(given)
    else:
        print('not found: %s' % given)
        return 1

    cfg = read_config(root)
    readme = find_guide()
    page = build_page(os.path.basename(report) if report else 'work-report',
                      cfg.get('submit_url') or '',
                      cfg.get('submit_label') or '제출하러 가기',
                      bool(readme),
                      bool((cfg.get('pms') or {}).get('url')))

    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))       # 바깥에서는 접근할 수 없다
    port = probe.getsockname()[1]
    probe.close()

    # 여러 요청이 겹친다. 보고서를 만드는 동안에도 화면이 상태를 물어보므로
    # 한 번에 하나만 받는 서버로는 막힌다.
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(('127.0.0.1', port), make_handler(root, report, page, readme))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    clear_mark = mark_running(root, port)

    url = 'http://127.0.0.1:%d/' % port
    if report:
        url += '?path=' + quote(os.path.relpath(report, root).replace(os.sep, '/'))
    webbrowser.open(url)
    print(url)

    # 탭을 닫아도 서버는 알 수 없으므로 조용해지면 물러난다.
    # 다만 보고서를 만드는 중이면 끝날 때까지 기다린다.
    while True:
        time.sleep(5)
        with jobs_lock:
            busy = any(j['state'] == 'running' for j in jobs.values())
        if not busy and time.time() - last_seen > IDLE_TIMEOUT:
            break
    server.shutdown()
    clear_mark()
    return 0


if __name__ == '__main__':
    sys.exit(main())
