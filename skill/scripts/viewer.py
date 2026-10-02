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
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, unquote

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
                        'proc': proc, 'state': 'running', 'path': None}
    return job_id


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
    with jobs_lock:
        for job in jobs.values():
            if job['state'] == 'running':
                code = job['proc'].poll()
                if code is not None:
                    made = newest_under(root, job['mode'], job['started'])
                    # 2는 러너가 "다른 실행이 도는 중이라 물러났다"고 말하는 값이다.
                    # 쓴 것이 없다는 점은 실패와 같지만 실패가 아니다.
                    if code == 2 and not made:
                        job['state'] = 'skipped'
                    else:
                        job['state'] = 'done' if (code == 0 and made) else 'failed'
                    if made:
                        job['path'] = os.path.relpath(made, root).replace(os.sep, '/')
            out.append({'id': job['id'], 'mode': job['mode'], 'state': job['state'],
                        'seconds': int(time.time() - job['started']), 'path': job['path']})
    return out


# 화면에서 고칠 수 있는 항목만 받는다. 설치가 채우는 기계 정보
# (*_homes, *_dirs, skill_dirs)는 손으로 고치면 깨지므로 받지 않는다.
EDITABLE = {
    'author': str, 'agent': str, 'notify': bool, 'submit_url': str, 'submit_label': str,
    'mine_only': bool, 'redact': bool, 'backfill_days': int, 'retain_months': int,
    'custom_format': bool, 'custom_rules': bool, 'custom_samples': bool,
    'exclude_repos': list, 'exclude_paths': list,
    'claude_bin': str, 'codex_bin': str, 'python_bin': str,
    'max_prompt_chars': int, 'max_prompts_per_session': int,
}
WEEKLY_KEYS = {'end_day': str, 'span_days': int}


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

    weekly = incoming.get('weekly') or {}
    if weekly:
        cur = dict(cfg.get('weekly') or {})
        for key, kind in WEEKLY_KEYS.items():
            if key in weekly:
                try:
                    cur[key] = int(weekly[key]) if kind is int else str(weekly[key])
                except (TypeError, ValueError):
                    pass
        cfg['weekly'] = cur
    path = os.path.join(root, 'config.json')
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
    return cfg


def html_escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def build_page(title, submit_url, submit_label, has_readme):
    submit_button = ''
    if submit_url:
        submit_button = ('<button id="submit" class="primary">%s</button>'
                         % html_escape(submit_label))
    return (PAGE.replace('{{TITLE}}', html_escape(title))
            .replace('{{SUBMIT_BUTTON}}', submit_button)
            .replace('{{SUBMIT_URL}}', html_escape(submit_url or ''))
            .replace('{{README_TAB}}',
                     '<button class="navitem" id="tabReadme"><span class="ic">&#9432;</span>사용 설명</button>' if has_readme else ''))


PAGE = r"""<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><title>{{TITLE}}</title>
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
   규칙을 찾아다니지 말고 이 표를 고친다. */
:root {
  /* 색 */
  --bg:#f5f6f8; --panel:#ffffff; --soft:#f2f4f7; --line:#e3e6ea;
  --text:#17191c; --muted:#616a75; --faint:#949ca8;
  --ink:#2b3036;     /* 본문 글자. 화면 글자보다 한 단 옅다 */
  --accent:#2563eb; --accent-soft:#eef3ff; --accent-line:#cfdffb;
  --ok:#15803d; --bad:#dc2626;
  --scroll:#c8cfd9; --scroll-hover:#a7b1bf;

  /* 글꼴 */
  --font:'Pretendard',-apple-system,"Segoe UI","Malgun Gothic",system-ui,sans-serif;
  --font-mono:ui-monospace,"Cascadia Mono",Consolas,monospace;

  /* 글자 크기 - 여섯 단만 쓴다 */
  --fs-100:12.5px;   /* 꼬리표, 보조 설명 */
  --fs-200:14px;     /* 목록, 단추, 라벨 */
  --fs-300:15.5px;   /* 본문 */
  --fs-400:17px;     /* 작은 제목 */
  --fs-500:19px;     /* 절 제목, 문서 이름 */
  --fs-600:26px;     /* 문서 제목 */
  --fs-mono:14px;

  --lh-tight:1.4;
  --lh-body:1.72;
  --fw-normal:400;
  --fw-medium:550;
  --fw-bold:650;

  /* 간격 */
  --sp-1:4px; --sp-2:8px; --sp-3:12px; --sp-4:16px;
  --sp-5:20px; --sp-6:24px; --sp-8:32px;

  /* 모양 */
  --r-sm:6px; --r-md:9px; --r-lg:12px; --r-pill:999px;
  --shadow-1:0 1px 2px rgba(16,24,40,.06);
  --shadow-2:0 8px 24px rgba(16,24,40,.14);

  /* 치수 */
  --rail:252px;       /* 왼쪽 목록 */
  --topbar:58px;
  --measure:880px;    /* 본문이 넘지 않는 폭 */
  --label:196px;      /* 설정 라벨 칸 */
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1115; --panel:#161a20; --soft:#1c2128; --line:#282e37;
          --text:#e7eaee; --muted:#a2abb7; --faint:#717b88; --ink:#d3dae3;
          --accent:#4d8bf5; --accent-soft:#182337; --accent-line:#2c4573;
          --scroll:#39414d; --scroll-hover:#4d5766;
          --ok:#4ade80; --bad:#f87171;
          --shadow-1:0 1px 2px rgba(0,0,0,.4);
          --shadow-2:0 8px 24px rgba(0,0,0,.5); }
}

/* ============================================================== 바탕 */
* { box-sizing:border-box; }
[hidden] { display:none !important; }

/* 스크롤 막대. 기본 막대는 폭이 넓고 회색이 짙어 본문보다 먼저 눈에 들어온다.
   여백 안에 가느다란 알약 하나만 남긴다. */
/* 크롬은 표준 scrollbar-width가 있으면 아래 규칙을 통째로 무시하고 제 막대를
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
/* Windows 크롬이 막대 양 끝에 붙이는 화살표 단추 */
*::-webkit-scrollbar-button { display:none; width:0; height:0; }
html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--text);
       font-family:var(--font); font-size:var(--fs-300); line-height:var(--lh-body);
       -webkit-font-smoothing:antialiased; }
pre, code, textarea.src { font-family:var(--font-mono); }

/* ============================================================== 컴포넌트
   여기 있는 것만 쓴다. 화면마다 새 모양을 만들지 않는다.
     단추 .btn / .btn.primary / .btn.quiet / .btn.icon
     고르개 .seg          카드 .card         꼬리표 .chip
     입력 .input .switch  줄 .field          구역 제목 .sectitle
     목록 항목 .navitem .doclink             알림 .job                     */

/* -- 단추 ---------------------------------------------------------- */
button, .btn {
  font-family:inherit; font-size:var(--fs-200); font-weight:var(--fw-medium);
  line-height:1.4; cursor:pointer; white-space:nowrap;
  padding:var(--sp-2) var(--sp-3); border-radius:var(--r-md);
  border:1px solid var(--line); background:var(--panel); color:var(--text);
  transition:background .1s, border-color .1s, filter .1s;
}
button:hover, .btn:hover { background:var(--soft); }
button:focus-visible, .btn:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.primary:hover { background:var(--accent); filter:brightness(1.08); }
/* 아주 옅은 강조. 제출(꽉 찬 색)보다 한 단 아래, 보통 단추보다 한 단 위 */
button.tint { background:var(--accent-soft); border-color:var(--accent-line); color:var(--accent); }
button.tint:hover { background:var(--accent-soft); filter:brightness(.97); }
button.quiet { border-color:transparent; background:transparent; color:var(--muted); }
button.quiet:hover { background:var(--soft); color:var(--text); }
button.iconbtn { padding:var(--sp-2); border-color:transparent; background:transparent;
                 color:var(--muted); font-size:var(--fs-400); line-height:1; }
button.iconbtn:hover { background:var(--soft); color:var(--text); }

/* -- 고르개 (표현 방식처럼 서로 배타적인 선택) ----------------------- */
.seg { display:inline-flex; gap:2px; padding:3px; border-radius:var(--r-md);
       background:var(--soft); border:1px solid var(--line); }
.seg button { border:0; background:transparent; color:var(--muted);
              padding:var(--sp-1) var(--sp-3); border-radius:var(--r-sm);
              font-weight:var(--fw-normal); }
.seg button:hover { background:transparent; color:var(--text); }
.seg button.on { background:var(--panel); color:var(--text);
                 font-weight:var(--fw-bold); box-shadow:var(--shadow-1); }

/* -- 카드 ---------------------------------------------------------- */
.card { background:var(--panel); border:1px solid var(--line);
        border-radius:var(--r-lg); padding:var(--sp-1) var(--sp-5) var(--sp-4);
        margin:0 0 var(--sp-4); }

/* -- 꼬리표 -------------------------------------------------------- */
.chip { flex:0 0 auto; font-size:var(--fs-100); font-weight:var(--fw-normal);
        color:var(--faint); background:var(--soft);
        border-radius:var(--r-pill); padding:1px var(--sp-2); }

/* -- 입력 ---------------------------------------------------------- */
input[type=text], input[type=number], select, textarea {
  font-family:inherit; font-size:var(--fs-200); line-height:1.5;
  padding:var(--sp-2) var(--sp-3); border:1px solid var(--line);
  border-radius:var(--r-md); background:var(--bg); color:var(--text); width:100%;
}
input:focus, select:focus, textarea:focus {
  outline:2px solid var(--accent); outline-offset:-1px; border-color:transparent; }
/* 값의 길이에 맞춘다. 네 글자를 받는 칸이 화면을 가로지르지 않게 */
.short { max-width:210px; }
.mid   { max-width:360px; }
.long  { max-width:480px; }
input[type=number] { width:104px; }
select { max-width:210px; }
textarea.lines { max-width:440px; min-height:82px; resize:vertical;
                 font-family:var(--font-mono); font-size:var(--fs-mono); }
/* 켬/끔은 라벨 바로 옆에 붙어야 무엇의 켬인지 읽힌다 */
.switch { justify-self:start; position:relative; width:42px; height:24px; padding:0;
          -webkit-appearance:none; appearance:none; cursor:pointer; border:0;
          background:var(--line); border-radius:var(--r-pill); transition:background .15s; }
.switch::after { content:""; position:absolute; top:3px; left:3px;
                 width:18px; height:18px; border-radius:50%; background:#fff;
                 box-shadow:0 1px 2px rgba(0,0,0,.25); transition:left .15s; }
.switch:checked { background:var(--accent); }
.switch:checked::after { left:21px; }

/* -- 설정 한 줄 ---------------------------------------------------- */
.field { display:grid; grid-template-columns:var(--label) minmax(0,1fr);
         gap:var(--sp-1) var(--sp-5); align-items:center;
         padding:var(--sp-3) 0; border-top:1px solid var(--line); }
.card > .field:first-child { border-top:0; }
.field label { font-size:var(--fs-300); color:var(--text); }
.hint { grid-column:2; font-size:var(--fs-100); color:var(--faint);
        white-space:pre-line; }

/* -- 구역 제목 ----------------------------------------------------- */
.sectitle { padding:var(--sp-3) var(--sp-3) var(--sp-1);
            font-size:var(--fs-100); font-weight:var(--fw-bold);
            letter-spacing:.06em; color:var(--faint); }

/* -- 목록 항목 ----------------------------------------------------- */
.navitem { display:flex; align-items:center; gap:var(--sp-2); width:100%;
           padding:var(--sp-2) var(--sp-3); border:0; border-radius:var(--r-md);
           background:transparent; color:var(--muted);
           font-size:var(--fs-200); font-weight:var(--fw-normal); text-align:left; }
.navitem:hover { background:var(--soft); color:var(--text); }
.navitem.on { background:var(--accent-soft); color:var(--accent); font-weight:var(--fw-bold); }
.navitem .ic { width:15px; text-align:center; opacity:.85; }

.doclink { display:flex; align-items:center; gap:var(--sp-2);
           padding:var(--sp-1) var(--sp-3) var(--sp-1) var(--sp-8);
           border-radius:var(--r-md); color:var(--text); text-decoration:none;
           font-size:var(--fs-200); white-space:nowrap;
           overflow:hidden; text-overflow:ellipsis; }
.doclink:hover { background:var(--soft); }
.doclink.on { background:var(--accent-soft); color:var(--accent); font-weight:var(--fw-bold); }
.doclink .chip { margin-left:auto; }
.doclink.on .chip { background:var(--panel); }
.recent .doclink { padding-left:var(--sp-3); }

/* -- 알림 카드 ----------------------------------------------------- */
#jobs { position:fixed; right:var(--sp-5); bottom:var(--sp-5); z-index:30;
        display:flex; flex-direction:column; gap:var(--sp-3); width:320px; }
.job { background:var(--panel); border:1px solid var(--line); border-radius:var(--r-lg);
       padding:var(--sp-3) var(--sp-4); box-shadow:var(--shadow-2);
       font-size:var(--fs-200); animation:rise .18s ease-out; }
@keyframes rise { from { opacity:0; transform:translateY(6px); } }
.job .row { display:flex; align-items:center; gap:var(--sp-2); }
.job .what { font-weight:var(--fw-bold); }
.job .time { margin-left:auto; color:var(--faint); font-size:var(--fs-100); }
.job .x { cursor:pointer; color:var(--faint); padding:0 2px; }
.job .x:hover { color:var(--text); }
.job .bar { height:3px; border-radius:2px; background:var(--soft);
            margin-top:var(--sp-3); overflow:hidden; }
.job .bar i { display:block; height:100%; width:35%; background:var(--accent);
              animation:slide 1.2s ease-in-out infinite; }
@keyframes slide { 0% { margin-left:-35%; } 100% { margin-left:100%; } }
.job .dot { width:8px; height:8px; border-radius:50%; flex:0 0 8px; }
.job.done .dot { background:var(--ok); }
.job.failed .dot { background:var(--bad); }
.job.skipped .dot { background:var(--faint); }
.job .note { margin-top:var(--sp-2); color:var(--muted); font-size:var(--fs-100); }
.job a { color:var(--accent); text-decoration:none; word-break:break-all; }
.job a:hover { text-decoration:underline; }

/* ============================================================== 뼈대
   화면은 세 층이다. 위 막대는 "만드는 일", 왼쪽 레일은 "어디로 갈지",
   오른쪽은 문서 한 장과 그 문서에 하는 일. 층이 나뉘어 있어야 지금 누르는
   단추가 무엇에 작용하는지 헷갈리지 않는다. */
.app { display:flex; flex-direction:column; height:100%; }
.main { flex:1 1 auto; display:flex; min-height:0; }

.appbar { flex:0 0 auto; display:flex; align-items:center; gap:var(--sp-3);
          height:var(--topbar); padding:0 var(--sp-4) 0 var(--sp-2);
          background:var(--panel); border-bottom:1px solid var(--line); }
.appbar .home { display:flex; align-items:center; gap:var(--sp-2);
                padding:var(--sp-1) var(--sp-2); border-color:transparent;
                background:transparent; }
.appbar .home:hover { background:var(--soft); }
.appbar .logo { width:22px; height:22px; border-radius:var(--r-sm); display:block; }
.appbar .brand { font-size:var(--fs-300); font-weight:var(--fw-bold); letter-spacing:-.01em; }
.appbar .grow { margin-left:auto; }
.appbar .make { display:flex; gap:var(--sp-2); }
.appbar .make button::before { content:"+"; margin-right:var(--sp-2);
                               opacity:.6; font-weight:var(--fw-bold); }

nav.side { flex:0 0 var(--rail); width:var(--rail); min-height:0;
           display:flex; flex-direction:column;
           background:var(--panel); border-right:1px solid var(--line); }
nav.side.hide { display:none; }
.navscroll { flex:1 1 auto; overflow-y:auto; overflow-x:hidden;
             padding:var(--sp-3) var(--sp-2) var(--sp-4); }
.navfoot { flex:0 0 auto; border-top:1px solid var(--line); padding:var(--sp-2); }

.group > .head { display:flex; align-items:center; gap:var(--sp-2); width:100%;
                 padding:var(--sp-2) var(--sp-3); border:0; border-radius:var(--r-md);
                 background:transparent; color:var(--text);
                 font-size:var(--fs-200); font-weight:var(--fw-bold); text-align:left; }
.group > .head:hover { background:var(--soft); }
.group .count { margin-left:auto; color:var(--faint);
                font-size:var(--fs-100); font-weight:var(--fw-normal); }
.caret { width:10px; flex:0 0 10px; color:var(--faint); font-size:10px;
         transition:transform .12s; }
.closed > .head .caret, .closed > .mhead .caret { transform:rotate(-90deg); }
.closed > .items { display:none; }
.month > .mhead { display:flex; align-items:center; gap:var(--sp-2); width:100%;
                  padding:3px var(--sp-3) 3px var(--sp-6); border:0; background:transparent;
                  color:var(--faint); font-size:var(--fs-100); text-align:left; }
.month > .mhead:hover { color:var(--muted); }

.doc { flex:1 1 auto; display:flex; flex-direction:column; min-width:0; min-height:0; }
.doctop { flex:0 0 auto; display:flex; align-items:center;
          gap:var(--sp-4); flex-wrap:wrap;
          padding:var(--sp-3) var(--sp-6);
          background:var(--panel); border-bottom:1px solid var(--line); }
.titlebox { min-width:0; margin-right:auto; }
.doctitle { margin:0; font-size:var(--fs-500); font-weight:var(--fw-bold);
            line-height:var(--lh-tight); letter-spacing:-.015em;
            white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.docsub { color:var(--faint); font-size:var(--fs-100); margin-top:1px;
          white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.docacts { display:flex; align-items:center; gap:var(--sp-2); flex-wrap:wrap; }
.state { font-size:var(--fs-100); color:var(--muted); white-space:nowrap; }
.state.warn { color:var(--accent); font-weight:var(--fw-bold); }

/* 스크롤은 여기 한 곳에서만 일어난다 */
.sheet { flex:1 1 auto; overflow-y:auto; }
.wrap { max-width:var(--measure); margin:0 auto;
        padding:var(--sp-6) var(--sp-6) 96px; }

/* 본문은 바탕 위에 놓인 종이 한 장이다. 읽을 때와 고칠 때가 같은 상자 안에서
   일어나므로, 고치기를 눌러도 없던 테두리가 새로 생기지 않는다. */
.paper { background:var(--panel); border:1px solid var(--line);
         border-radius:var(--r-lg); padding:var(--sp-8);
         transition:border-color .12s, box-shadow .12s; }
.paper.edit { border-color:var(--accent); box-shadow:0 0 0 3px var(--accent-soft); }

/* ============================================================== 본문 서식 */
.body { color:var(--ink); }
.body h1 { color:var(--text); font-size:var(--fs-600); font-weight:var(--fw-bold);
           line-height:var(--lh-tight); letter-spacing:-.02em; margin:0 0 var(--sp-5); }
.body h2 { color:var(--text); font-size:var(--fs-500); font-weight:var(--fw-bold);
           line-height:var(--lh-tight); margin:var(--sp-8) 0 var(--sp-3);
           padding-bottom:var(--sp-2); border-bottom:1px solid var(--line); }
.body h3 { font-size:var(--fs-400); font-weight:var(--fw-bold); margin:var(--sp-6) 0 var(--sp-2); }
.body h4 { font-size:var(--fs-300); font-weight:var(--fw-bold);
           color:var(--muted); margin:var(--sp-5) 0 var(--sp-1); }
.body p { margin:var(--sp-2) 0; }
.body ul { margin:var(--sp-2) 0; padding-left:var(--sp-5); }
.body li { margin:var(--sp-1) 0; }
.body li::marker { color:var(--faint); }
.body a { color:var(--accent); }
.body hr { border:0; border-top:1px solid var(--line); margin:var(--sp-6) 0; }
.body code { font-size:.9em; background:var(--soft); border:1px solid var(--line);
             padding:.1em .38em; border-radius:var(--r-sm); }
.body pre.code { background:var(--soft); border:1px solid var(--line);
                 border-radius:var(--r-md); padding:var(--sp-3) var(--sp-4);
                 overflow-x:auto; font-size:var(--fs-mono); line-height:1.65; }
.tablewrap { overflow-x:auto; margin:var(--sp-4) 0; }
.body table { border-collapse:collapse; width:100%; font-size:var(--fs-200); }
.body th, .body td { border:1px solid var(--line);
                     padding:var(--sp-2) var(--sp-3); text-align:left; vertical-align:top; }
.body th { background:var(--soft); color:var(--muted);
           font-size:var(--fs-100); font-weight:var(--fw-bold); letter-spacing:.02em; }
/* 양 끝 칸은 보통 "구분", "상태"처럼 짧은 이름표다. 폭을 내주면 글자가
   세로로 쪼개져 읽을 수 없게 된다. 가운데 칸이 남는 폭을 가져간다. */
.body th:first-child, .body td:first-child,
.body th:last-child, .body td:last-child { white-space:nowrap; width:1%; }
.body td { word-break:break-word; }
.body td code { word-break:break-all; }

/* 원문과 편집은 같은 자리에 같은 글자로 놓인다. 종이가 이미 여백과 테두리를
   맡고 있으므로 입력칸은 아무 모양도 갖지 않는다. */
pre.raw, textarea.src { margin:0; padding:0; border:0; background:transparent;
                        color:var(--ink); font-size:var(--fs-mono); line-height:1.75; }
pre.raw { white-space:pre-wrap; word-break:break-word; }
textarea.src { display:block; width:100%; min-height:320px;
               resize:none; outline:none; overflow:hidden; }

.conf .card > h3 { margin:var(--sp-4) 0 2px; font-size:var(--fs-100);
                   font-weight:var(--fw-bold); letter-spacing:.05em; color:var(--faint); }

.empty { color:var(--faint); font-size:var(--fs-200); padding:var(--sp-4) var(--sp-3); }

  /* 내 양식은 문서가 아니라 설정 옆에 두는 것이라 아래 칸에 있다 */
  .sectitle.custom { margin-top:var(--sp-2); padding-top:var(--sp-3);
                     border-top:1px solid var(--line); }

  /* 아직 만들지 않은 내 양식 */
  .doclink.none { color:var(--faint); }
  .doclink.none:hover { background:transparent; cursor:default; }

@media (max-width:820px) {
  nav.side { position:absolute; z-index:20; height:calc(100% - var(--topbar));
             box-shadow:var(--shadow-2); }
  .wrap { padding:var(--sp-6) var(--sp-4) 80px; }
  .doctop { padding:var(--sp-2) var(--sp-4); }
}
</style></head>
<body>
<div class="app">

  <header class="appbar">
    <button class="iconbtn" id="toggleSide" title="목록 접기">&#9776;</button>
    <button class="home" id="home" title="첫 화면으로">
      <img class="logo" src="/favicon.ico" alt="">
      <span class="brand">work-report</span>
    </button>
    <span class="grow"></span>
    <span class="make">
      <button class="tint" id="runDaily">일일보고 만들기</button>
      <button class="tint" id="runWeekly">주간보고 만들기</button>
    </span>
  </header>

  <div class="main">
    <nav class="side" id="side">
      <div class="navscroll" id="files"></div>
      <div class="navfoot">
        {{README_TAB}}
        <button class="navitem" id="tabConfig"><span class="ic">&#9881;</span>설정</button>
        <div id="customBox"></div>
      </div>
    </nav>

    <section class="doc">
      <div class="doctop">
        <div class="titlebox">
          <h1 class="doctitle" id="docTitle">work-report</h1>
          <div class="docsub" id="docPath"></div>
        </div>
        <div class="docacts">
          <span class="state" id="state"></span>
          <span class="seg" id="seg">
            <button class="on" id="tabPreview">미리보기</button>
            <button id="tabRaw">원문</button>
          </span>
          <button id="edit">고치기</button>
          <button id="save" class="primary" hidden>저장</button>
          <button id="confSave" class="primary" hidden>설정 저장</button>
          <button id="copy" class="quiet">복사</button>
          {{SUBMIT_BUTTON}}
        </div>
      </div>
      <div class="sheet" id="sheet">
        <div class="wrap">
          <div class="paper" id="paper">
            <div class="body" id="view"></div>
            <pre class="raw" id="rawView" hidden></pre>
            <textarea class="src" id="src" hidden spellcheck="false"></textarea>
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
// 알림을 눌러 들어오면 어떤 보고서를 열지 주소가 말해 준다
const START = new URLSearchParams(location.search).get('path') || '';
const AREA_NAME = { daily:'일일보고', weekly:'주간보고', log:'한 일 목록', raw:'수집 원본' };
const CUSTOM_NAME = { 'report-format.md':'보고서 양식', 'writing-rules.md':'글쓰기 문체',
                      'my-reports.md':'내 보고서' };
// 제출문 절은 붙여넣기용이라 보고서 전체가 아니라 그 절만 클립보드에 담는다
const SUBMIT_HEAD = '제출문';
// 보고서를 쓸 때 근거로 들춰 보는 것들이다. 매번 펼쳐져 있으면 목록만 길어진다
const FOLDED = ['log', 'raw'];
let raw = '', current = '', dirty = false, mode = 'preview', readme = null, conf = null;

// 화면에서 고치는 항목만 둔다. 설치가 채우는 경로 값은 여기에 없다.
const WEEKDAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
const FIELDS = [
  { k:'author',        t:'text',   label:'작성자', size:'short' },
  { k:'agent',         t:'select', label:'실행 CLI', opts:['claude','codex'] },
  { k:'notify',        t:'bool', def:true,   label:'알림 사용' },
  { k:'submit_url',    t:'text',   label:'제출 화면 주소', size:'long',
    hint:'비우면 제출 단추가 사라집니다' },
  { k:'submit_label',  t:'text',   label:'제출 단추 문구', size:'mid' },
  { k:'custom_format', t:'bool', def:false, label:'내 보고서 양식 쓰기',
    hint:'켜면 보고 폴더의 custom\\report-format.md를 쓴다. 없으면 기본값을 복사해 만들어 준다' },
  { k:'custom_rules',  t:'bool', def:false, label:'내 글쓰기 문체 쓰기',
    hint:'켜면 기본 원칙 뒤에 custom\\writing-rules.md를 덧붙인다' },
  { k:'custom_samples', t:'bool', def:false, label:'내 보고서 따라하기',
    hint:'켜면 custom\\my-reports.md에 붙여넣은 지난 보고서를 문체 예시로 쓴다.' +
         '\n보고서 맨 앞에 그 문체로 쓴 제출문 절이 생긴다.' +
         '\n붙여넣은 것이 없으면 아무 일도 하지 않는다' },
  { h:'수집' },
  { k:'mine_only',     t:'bool', def:true,   label:'내 이메일의 커밋만' },
  { k:'redact',        t:'bool', def:true,   label:'키·토큰 가리기' },
  { k:'exclude_repos', t:'lines',  label:'제외할 저장소',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 제외된다.' +
         '\nex. my-project' +
         '\n폴더 이름만 적는 편이 확실하다. 저장소를 옮겨도 계속 걸린다.' +
         '\nex. /c/Users/me/project/my-project → 안 걸린다 (Git Bash 형식)' },
  { k:'exclude_paths', t:'lines',  label:'작업으로 안 치는 경로',
    hint:'한 줄에 하나. 경로에 그 글자가 들어가면 뺀다.' +
         '\nex. node_modules' +
         '\nex. \\build\\  (구분자는 \\ 로 적는다)' },
  { h:'실행' },
  { k:'backfill_days', t:'num',    label:'빠뜨린 날 채우기', hint:'며칠 전까지. 0이면 안 함' },
  { k:'retain_months', t:'num',    label:'수집본 보관(개월)',
    hint:'0이면 전부 보관. 보고서는 지우지 않음' },
  { k:'weekly.end_day',   t:'select', label:'주간 마지막 요일', opts:WEEKDAYS },
  { k:'weekly.span_days', t:'num',    label:'주간 기간(일)' },
  { h:'실행 파일' },
  { k:'claude_bin', t:'text', label:'claude 경로', size:'long',
    probe:'claude', hint:'찾는 중...' },
  { k:'codex_bin',  t:'text', label:'codex 경로',  size:'long',
    probe:'codex',  hint:'찾는 중...' },
  { k:'python_bin', t:'text', label:'python 경로', size:'long',
    probe:'python', hint:'찾는 중...' },
];
const dig = (o, k) => k.split('.').reduce((a, x) => (a || {})[x], o);
const $ = id => document.getElementById(id);

function api(path, params){
  const q = new URLSearchParams(params || {}).toString();
  return q ? path + '?' + q : path;
}
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function inline(s){
  return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>')
               .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
               .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
}
// 보고서와 README가 쓰는 문법만 다룬다
function render(md){
  const out = [], lines = md.split(/\r?\n/);
  let i = 0, list = false, fence = null, para = [];
  const closeList = () => { if (list) { out.push('</ul>'); list = false; } };
  // 이어진 줄은 한 문단이다. 줄마다 문단을 열면 원문의 줄바꿈 위치가
  // 그대로 화면의 끊김이 되어, 폭이 넓은 화면에서 글이 토막나 보인다.
  const closePara = () => {
    if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; }
  };
  const closeBoth = () => { closeList(); closePara(); };
  while (i < lines.length) {
    const ln = lines[i];
    if (fence !== null) {
      if (/^\s*```/.test(ln)) { out.push(esc(fence.join('\n'))); out.push('</pre>'); fence = null; }
      else fence.push(ln);
      i++; continue;
    }
    if (/^\s*```/.test(ln)) { closeBoth(); out.push('<pre class="code">'); fence = []; i++; continue; }
    const isTable = /^\s*\|/.test(ln) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i+1] || '');
    if (isTable) {
      closeBoth();
      const cells = r => r.replace(/^\s*\|/,'').replace(/\|\s*$/,'').split('|').map(c => c.trim());
      out.push('<div class="tablewrap"><table><thead><tr>'
               + cells(ln).map(c => '<th>'+inline(c)+'</th>').join('') + '</tr></thead><tbody>');
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        out.push('<tr>' + cells(lines[i]).map(c => '<td>'+inline(c)+'</td>').join('') + '</tr>');
        i++;
      }
      out.push('</tbody></table></div>');
      continue;
    }
    let m;
    if ((m = ln.match(/^(#{1,4})\s+(.*)$/))) { closeBoth(); out.push('<h'+m[1].length+'>'+inline(m[2])+'</h'+m[1].length+'>'); }
    else if (/^\s*---+\s*$/.test(ln)) { closeBoth(); out.push('<hr>'); }
    else if ((m = ln.match(/^\s*[-*]\s+(.*)$/))) { closePara(); if (!list) { out.push('<ul>'); list = true; } out.push('<li>'+inline(m[1])+'</li>'); }
    else if ((m = ln.match(/^\s*\d+\.\s+(.*)$/))) { closePara(); if (!list) { out.push('<ul>'); list = true; } out.push('<li>'+inline(m[1])+'</li>'); }
    else if (ln.trim() === '') { closeBoth(); }
    else if (list) { out.push('</ul>'); list = false; para.push(ln); }   // 목록이 끝나고 문단이 시작
    else { para.push(ln); }
    i++;
  }
  closeBoth();
  return out.join('\n');
}

// 입력칸이 내용만큼 자란다. 안에서 또 스크롤되면 스크롤 막대가 둘이 된다
function fitSrc(){
  const ta = $('src');
  ta.style.height = 'auto';
  ta.style.height = ta.scrollHeight + 'px';
}

function setState(t, warn){
  const el = $('state');
  el.textContent = t || '';
  el.classList.toggle('warn', !!warn);
}
function flash(t){ setState(t); setTimeout(() => setState(''), 2500); }
function editing(){ return !$('src').hidden; }
function text(){ return editing() ? $('src').value : raw; }

// 경로에서 사람이 읽을 제목을 만든다: daily/2026-09/2026-09-22.md -> 2026-09-22 일일보고
function titleOf(path, fallback){
  if (!path) return fallback || 'work-report';
  const parts = path.split('/');
  const last = parts[parts.length - 1];
  if (parts[0] === 'custom') return (CUSTOM_NAME[last] || last) + ' (내 양식)';
  const name = last.replace(/\.md$/, '');
  const area = AREA_NAME[parts[0]];
  return area ? name + ' ' + area : name;
}

function setHead(title, sub){
  $('docTitle').textContent = title;
  $('docPath').textContent = sub || '';
  document.title = title;
}

// 보고서를 볼 때와 설정·README를 볼 때는 쓸 수 있는 동작이 다르다
function show(which){
  mode = which;
  const doc = which === 'preview' || which === 'raw';
  const ed = editing();
  // 실행 기록은 읽기만 한다. 저장은 보고서에만 연다
  const writable = !current || /\.md$/i.test(current);
  $('view').hidden = ed || which === 'raw' || which === 'config';
  $('rawView').hidden = ed || which !== 'raw';
  $('confView').hidden = which !== 'config';
  $('src').hidden = !ed || !doc;
  // 설정은 종이 위의 글이 아니라 다른 화면이다
  $('paper').hidden = which === 'config';
  $('paper').classList.toggle('edit', ed && doc);
  if (ed && doc) fitSrc();

  // 고칠 때는 고르는 일이 없다. 표현 방식도, 복사도, 제출도 읽을 때의 동작이다
  $('seg').hidden = !doc || ed;
  $('edit').hidden = !doc || !writable;
  $('edit').textContent = ed ? '보기' : '고치기';
  $('save').hidden = !doc || !ed || !writable;
  $('copy').hidden = !doc || ed;
  $('confSave').hidden = which !== 'config';
  if ($('submit')) $('submit').hidden = !doc || ed;

  $('tabPreview').classList.toggle('on', which === 'preview');
  $('tabRaw').classList.toggle('on', which === 'raw');
  $('tabConfig').classList.toggle('on', which === 'config');
  if ($('tabReadme')) $('tabReadme').classList.toggle('on', which === 'readme');
  document.querySelectorAll('.doclink').forEach(a =>
    a.classList.toggle('on', doc && a.dataset.path === current));

  if (which === 'readme') { setHead('사용 설명', 'GUIDE.md'); $('view').innerHTML = render(readme || ''); }
  else if (which === 'config') { setHead('설정', 'config.json'); }
  else {
    setHead(titleOf(current), current);
    if (which === 'preview') $('view').innerHTML = render(raw);
  }
  $('sheet').scrollTop = 0;
}

function docLink(it, area, withChip){
  const a = document.createElement('a');
  a.className = 'doclink';
  a.href = '#';
  a.dataset.path = it.path;
  const t = document.createElement('span');
  t.textContent = it.name;
  t.style.overflow = 'hidden';
  t.style.textOverflow = 'ellipsis';
  a.appendChild(t);
  if (withChip) {
    const c = document.createElement('span');
    c.className = 'chip';
    c.textContent = AREA_NAME[area] || area;
    a.appendChild(c);
  }
  if (it.path === current) a.classList.add('on');
  a.onclick = e => { e.preventDefault(); openReport(it.path); };
  return a;
}

async function loadFiles(){
  const d = await (await fetch(api('files'))).json();
  drawCustom($('customBox'), d.custom || []);
  const box = $('files');
  const opened = new Set([...box.querySelectorAll('.month:not(.closed)')].map(e => e.dataset.key));
  const shut = new Set([...box.querySelectorAll('.group.closed')].map(e => e.dataset.area));
  const first = !box.querySelector('.group');   // 처음 그리는가, 다시 그리는가
  box.innerHTML = '';

  if (!d.groups.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = '아직 보고서가 없습니다.';
    box.appendChild(e);
    return;
  }

  // 어제 쓴 보고서를 다시 여는 일이 가장 잦다. 접힌 목록을 헤치지 않게 위에 둔다
  const recent = [];
  for (const g of d.groups) {
    if (g.area !== 'daily' && g.area !== 'weekly') continue;
    for (const mo of g.months) for (const it of mo.items) recent.push({ it, area: g.area });
  }
  recent.sort((a, b) => a.it.name < b.it.name ? 1 : -1);
  if (recent.length) {
    const head = document.createElement('div');
    head.className = 'sectitle';
    head.textContent = '최근';
    box.appendChild(head);
    const wrap = document.createElement('div');
    wrap.className = 'recent';
    recent.slice(0, 5).forEach(r => wrap.appendChild(docLink(r.it, r.area, true)));
    box.appendChild(wrap);
  }

  const all = document.createElement('div');
  all.className = 'sectitle';
  all.textContent = '전체';
  all.style.marginTop = '6px';
  box.appendChild(all);

  for (const g of d.groups) {
    const area = document.createElement('div');
    area.className = 'group';
    area.dataset.area = g.area;
    if (first ? FOLDED.includes(g.area) : shut.has(g.area)) area.classList.add('closed');
    const total = g.months.reduce((n, mo) => n + mo.items.length, 0);

    const ahead = document.createElement('button');
    ahead.className = 'head';
    ahead.innerHTML = '<span class="caret">&#9660;</span>';
    ahead.appendChild(document.createTextNode(g.label));
    const cnt = document.createElement('span');
    cnt.className = 'count';
    cnt.textContent = total;
    ahead.appendChild(cnt);
    ahead.onclick = () => area.classList.toggle('closed');
    area.appendChild(ahead);

    const abox = document.createElement('div');
    abox.className = 'items';
    g.months.forEach((mo, mi) => {
      const key = g.area + '/' + mo.month;
      const month = document.createElement('div');
      month.className = 'month';
      month.dataset.key = key;
      // 처음에는 가장 최근 달만 펼친다. 쌓여도 목록이 길어지지 않는다.
      // 다시 그릴 때는 지금 펼쳐 둔 것을 그대로 둔다 - 전부 접어 두었다고 해서
      // 보고서를 만들 때마다 최근 달이 도로 열리면 접어 둔 뜻이 없다.
      const keepOpen = first ? mi === 0 : opened.has(key);
      if (!keepOpen) month.classList.add('closed');
      const mhead = document.createElement('button');
      mhead.className = 'mhead';
      mhead.innerHTML = '<span class="caret">&#9660;</span>';
      mhead.appendChild(document.createTextNode(mo.month));
      mhead.onclick = () => month.classList.toggle('closed');
      month.appendChild(mhead);
      const list = document.createElement('div');
      list.className = 'items';
      for (const it of mo.items) list.appendChild(docLink(it, g.area, false));
      month.appendChild(list);
      abox.appendChild(month);
    });
    area.appendChild(abox);
    box.appendChild(area);
  }
}

// 내 양식. 보고서와 같은 .md라 뷰어가 그대로 열고 저장한다.
// 없을 때는 기본값을 복사해 주는 단추만 둔다 - 백지에서 쓰게 하지 않는다.
function drawCustom(box, items){
  box.innerHTML = '';
  if (!items.length) return;
  const head = document.createElement('div');
  head.className = 'sectitle custom';
  head.textContent = '내 양식';
  box.appendChild(head);
  for (const it of items) {
    const a = document.createElement('a');
    a.className = 'doclink' + (it.exists ? '' : ' none');
    a.href = '#';
    a.style.paddingLeft = 'var(--sp-3)';
    const t = document.createElement('span');
    t.textContent = it.name;
    t.style.overflow = 'hidden';
    t.style.textOverflow = 'ellipsis';
    a.appendChild(t);
    if (it.mine && it.exists) {
      a.dataset.path = it.path;
      if (it.path === current) a.classList.add('on');
      a.onclick = e => { e.preventDefault(); openReport(it.path); };
    } else {
      // 쓸지 말지는 설정에서 정한다. 여기서는 지금 무엇을 쓰는지만 보인다
      a.classList.add('none');
      const tag = document.createElement('span');
      tag.className = 'chip';
      tag.textContent = it.mine ? '다음 실행에 생김' : '기본값';
      a.appendChild(tag);
      a.onclick = e => { e.preventDefault(); $('tabConfig').click(); };
    }
    box.appendChild(a);
  }
}

async function openReport(path){
  if (dirty && !confirm('저장하지 않은 수정이 있습니다. 그래도 넘어갈까요?')) return;
  const d = await (await fetch(api('report', path ? { path } : {}))).json();
  raw = d.text; current = d.path; dirty = false;
  $('rawView').textContent = raw;
  $('src').value = raw;
  $('src').hidden = true;
  let next = mode === 'readme' || mode === 'config' ? 'preview' : mode;
  if (current && !/\.md$/i.test(current)) next = 'raw';   // 실행 기록 같은 것
  show(next);
}

function jobCard(j){
  const label = j.mode === 'daily' ? '일일보고' : '주간보고';
  let el = $('job-' + j.id);
  if (!el) { el = document.createElement('div'); el.id = 'job-' + j.id; $('jobs').appendChild(el); }
  el.className = 'job ' + j.state;
  if (j.state === 'running') {
    el.innerHTML = '<div class="row"><span class="what">' + label + ' 만드는 중</span>'
                 + '<span class="time">' + j.seconds + '초</span></div>'
                 + '<div class="bar"><i></i></div>';
  } else if (j.state === 'done') {
    el.innerHTML = '<div class="row"><span class="dot"></span><span class="what">' + label + ' 완료</span>'
                 + '<span class="time">' + j.seconds + '초</span><span class="x">&times;</span></div>'
                 + (j.path ? '<div class="note"><a href="#" data-open="' + j.path + '">열기: ' + j.path + '</a></div>' : '');
  } else if (j.state === 'skipped') {
    el.innerHTML = '<div class="row"><span class="dot"></span><span class="what">' + label + ' 건너뜀</span>'
                 + '<span class="time">' + j.seconds + '초</span><span class="x">&times;</span></div>'
                 + '<div class="note">다른 실행이 진행 중이어서 물러났습니다</div>';
  } else {
    el.innerHTML = '<div class="row"><span class="dot"></span><span class="what">' + label + ' 실패</span>'
                 + '<span class="time">' + j.seconds + '초</span><span class="x">&times;</span></div>'
                 + '<div class="note">runlog 폴더의 실행 기록을 확인하세요</div>';
  }
  const x = el.querySelector('.x');
  // 서버가 작업을 계속 들고 있으면 다음 폴링이 카드를 다시 만든다.
  // 화면에서 지우기 전에 서버에서 먼저 뺀다.
  if (x) x.onclick = async () => {
    el.remove();
    try { await fetch(api('dismiss', { id: j.id }), { method:'POST' }); } catch (e) {}
  };
  const a = el.querySelector('a[data-open]');
  if (a) a.onclick = e => { e.preventDefault(); loadFiles().then(() => openReport(a.dataset.open)); };
}

let polling = false;
async function pollJobs(){
  const list = await (await fetch(api('jobs'))).json();
  let running = false;
  for (const j of list) { jobCard(j); if (j.state === 'running') running = true; }
  if (running) setTimeout(pollJobs, 1500);
  else { polling = false; loadFiles(); }
}
async function run(kind){
  await fetch(api('run', { mode: kind }), { method: 'POST' });
  if (!polling) { polling = true; pollJobs(); }
}

// 빈 칸이 "설정이 안 됐다"로 읽히지 않게, 지금 쓰는 경로를 흐린 글씨로 채운다.
// 값이 아니라 안내이므로 저장해도 덮어쓰지 않는다 - 비워 두면 계속 자동으로 찾는다
async function fillBins(){
  let found;
  try { found = await (await fetch(api('bins'))).json(); }
  catch (e) { found = {}; }
  for (const f of FIELDS) {
    if (!f.probe) continue;
    const el = $('f_' + f.k.replace('.', '_'));
    if (!el) continue;
    const got = found[f.probe];
    el.placeholder = got ? got.path : '찾지 못했습니다';
    const hint = el.parentNode.querySelector('.hint');
    if (!hint) continue;
    hint.textContent = got
      ? '자동으로 찾았습니다 — ' + got.version + '. 다른 걸 쓰려면 전체 경로를 적으세요'
      : '자동으로 찾지 못했습니다. 전체 경로를 적어 주세요';
  }
}

function drawConfig(){
  const box = $('confView');
  box.innerHTML = '';
  const h = document.createElement('h1');
  h.textContent = '설정';
  box.appendChild(h);
  const note = document.createElement('div');
  note.className = 'hint';
  note.style.gridColumn = 'auto';
  note.style.margin = '-12px 0 20px';
  note.textContent = '다음 실행부터 적용됩니다';
  box.appendChild(note);

  let card = document.createElement('div');
  card.className = 'card';
  box.appendChild(card);

  for (const f of FIELDS) {
    if (f.h) {
      card = document.createElement('div');
      card.className = 'card';
      const t = document.createElement('h3');
      t.textContent = f.h;
      card.appendChild(t);
      box.appendChild(card);
      continue;
    }
    const row = document.createElement('div');
    row.className = 'field';
    const lab = document.createElement('label');
    lab.textContent = f.label;
    row.appendChild(lab);
    let el;
    const val = dig(conf, f.k);
    if (f.t === 'bool') {
      el = document.createElement('input');
      el.type = 'checkbox';
      el.className = 'switch';
      // 값이 없으면 항목이 정한 기본값이다. 전부 켜짐으로 그리면 기본이
      // 거짓인 설정이 저장하는 순간 켜져 버린다.
      el.checked = (val === undefined || val === null) ? !!f.def : !!val;
    } else if (f.t === 'num') {
      el = document.createElement('input');
      el.type = 'number';
      el.value = val == null ? '' : val;
    } else if (f.t === 'select') {
      el = document.createElement('select');
      for (const o of f.opts) {
        const op = document.createElement('option');
        op.value = o; op.textContent = o;
        el.appendChild(op);
      }
      el.value = val || f.opts[0];
    } else if (f.t === 'lines') {
      el = document.createElement('textarea');
      el.className = 'lines';
      el.value = (val || []).join('\n');
    } else {
      el = document.createElement('input');
      el.type = 'text';
      el.className = f.size || 'mid';
      el.value = val == null ? '' : val;
    }
    el.dataset.key = f.k;
    el.dataset.type = f.t;
    lab.htmlFor = 'f_' + f.k.replace('.', '_');
    el.id = lab.htmlFor;
    row.appendChild(el);
    if (f.hint) {
      const hint = document.createElement('div');
      hint.className = 'hint';
      hint.textContent = f.hint;
      row.appendChild(hint);
    }
    card.appendChild(row);
  }

  // 저장은 문서 화면과 같은 자리에서 한다: 위쪽 동작 줄의 단추와 그 옆 알림
  $('confSave').onclick = async () => {
    const out = { weekly: {} };
    box.querySelectorAll('[data-key]').forEach(el => {
      const k = el.dataset.key, t = el.dataset.type;
      let v;
      if (t === 'bool') v = el.checked;
      else if (t === 'num') v = el.value === '' ? 0 : Number(el.value);
      else if (t === 'lines') v = el.value.split(/\r?\n/);
      else v = el.value;
      if (k.startsWith('weekly.')) out.weekly[k.slice(7)] = v; else out[k] = v;
    });
    const r = await fetch(api('config'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(out) });
    if (!r.ok) { setState('저장하지 못했습니다', true); return; }
    conf = await r.json();
    // 양식 토글을 바꿨으면 왼쪽 목록이 바로 따라와야 한다. 켜면 그 자리에서
    // 파일이 생기므로 새로 고치지 않아도 열 수 있다.
    loadFiles();
    flash('저장했습니다');
  };
}

// 첫 화면은 가장 최근 보고서다. 길을 잃으면 여기로 돌아온다
$('home').onclick = () => { mode = 'preview'; openReport(''); };
$('toggleSide').onclick = () => $('side').classList.toggle('hide');
$('tabPreview').onclick = () => show('preview');
$('tabRaw').onclick = () => show('raw');
if ($('tabReadme')) $('tabReadme').onclick = async () => {
  if (readme === null) readme = await (await fetch(api('readme'))).text();
  show('readme');
};
$('tabConfig').onclick = async () => {
  if (conf === null) conf = await (await fetch(api('config'))).json();
  drawConfig();
  show('config');
  fillBins();
};
$('runDaily').onclick = () => run('daily');
$('runWeekly').onclick = () => run('weekly');
$('edit').onclick = () => {
  const was = editing();
  $('src').hidden = was;
  if (was) { raw = $('src').value; $('rawView').textContent = raw; }
  show(mode === 'readme' || mode === 'config' ? 'preview' : mode);
};
$('save').onclick = async () => {
  const body = $('src').value;
  const r = await fetch(api('report', { path: current }),
                        { method:'POST', headers:{'Content-Type':'text/plain; charset=utf-8'}, body });
  if (r.ok) { raw = body; dirty = false; $('rawView').textContent = raw; show(mode); flash('저장했습니다'); }
  else { setState('저장하지 못했습니다', true); }
};
$('src').oninput = () => { dirty = true; setState('수정 중', true); fitSrc(); };
// 제출문 절이 있으면 그것만, 없으면 보고서 전체를 준다. 무엇을 담았는지는
// 눌렀을 때 말해 준다 - 조용히 일부만 복사되면 붙여넣고 나서야 안다.
function toCopy(){
  const lines = text().split(/\r?\n/);
  const isHead = s => /^##\s/.test(s);
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (isHead(lines[i]) && lines[i].includes(SUBMIT_HEAD)) { start = i + 1; break; }
  }
  if (start < 0) return { body: text(), part: false };
  let end = lines.length;
  for (let i = start; i < lines.length; i++) { if (isHead(lines[i])) { end = i; break; } }
  const body = lines.slice(start, end).join('\n').trim();
  return body ? { body: body, part: true } : { body: text(), part: false };
}
$('copy').onclick = async () => {
  const c = toCopy();
  await navigator.clipboard.writeText(c.body);
  flash(c.part ? '제출문을 복사했습니다' : '클립보드에 복사했습니다');
};
if ($('submit')) $('submit').onclick = async () => {
  const c = toCopy();
  try { await navigator.clipboard.writeText(c.body); } catch (e) {}
  flash(c.part ? '제출문을 복사했습니다. 붙여넣으세요' : '복사했습니다. 붙여넣으세요');
  window.open(SUBMIT_URL, '_blank');
};
window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 's' && editing()) { e.preventDefault(); $('save').click(); }
});

(async () => {
  try { await openReport(START); await loadFiles(); }
  catch (e) { $('view').innerHTML = '<p>보고서를 읽지 못했습니다: ' + esc(String(e)) + '</p>'; }
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
                      bool(readme))

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
