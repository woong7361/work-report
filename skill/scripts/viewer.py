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
    return _render_page('index.html', title, submit_url, submit_label, has_readme, pms_on)


def build_asset(name, title, submit_url, submit_label, has_readme, pms_on=False):
    """페이지가 참조하는 자산을 같은 설정값으로 렌더링한다."""
    return _render_page(name, title, submit_url, submit_label, has_readme, pms_on)


def _render_page(name, title, submit_url, submit_label, has_readme, pms_on):
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(here, 'page', name)
    with io.open(path, encoding='utf-8') as fh:
        text = fh.read()
    return (text.replace('{{TITLE}}', html_escape(title))
            .replace('{{SUBMIT_URL}}', html_escape(submit_url or ''))
            .replace('{{SUBMIT_LABEL}}', html_escape(submit_label or '제출하러 가기'))
            .replace('{{HAS_README}}', 'true' if has_readme else 'false')
            .replace('{{PMS_ON}}', 'true' if pms_on else 'false'))





def make_handler(root, report_path, page, readme_path, page_assets=None):
    page_assets = page_assets or {}

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
            asset_path_name = self.path.partition('?')[0]
            if asset_path_name == '/page/app.js':
                body = page_assets.get('app.js')
                if body is None:
                    return self._send(404, b'no page asset')
                return self._send(200, body.encode('utf-8'),
                                   'application/javascript; charset=utf-8')
            if asset_path_name == '/page/app.css':
                body = page_assets.get('app.css')
                if body is None:
                    return self._send(404, b'no page asset')
                return self._send(200, body.encode('utf-8'),
                                   'text/css; charset=utf-8')
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
    page_assets = {
        name: build_asset(name, os.path.basename(report) if report else 'work-report',
                          cfg.get('submit_url') or '',
                          cfg.get('submit_label') or '제출하러 가기',
                          bool(readme),
                          bool((cfg.get('pms') or {}).get('url')))
        for name in ('app.js', 'app.css')
    }

    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))       # 바깥에서는 접근할 수 없다
    port = probe.getsockname()[1]
    probe.close()

    # 여러 요청이 겹친다. 보고서를 만드는 동안에도 화면이 상태를 물어보므로
    # 한 번에 하나만 받는 서버로는 막힌다.
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(('127.0.0.1', port),
                    make_handler(root, report, page, readme, page_assets))
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
