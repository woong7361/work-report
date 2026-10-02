# -*- coding: utf-8 -*-
"""PMS(Redmine) 일일보고 폼을 채운다. 저장은 하지 않는다.

폼에 API가 없어서(플러그인이 API 인증을 받지 않는다) 브라우저로 채운다.
작업 항목이 여러 행이고 행마다 분류를 고르고 일감을 체크해야 하므로,
클립보드로는 애초에 옮길 수 없는 모양이다.

전용 프로파일을 쓰는 이유: Playwright는 자기가 띄운 브라우저만 조작한다.
평소 쓰는 크롬은 디버깅 포트 없이 떠 있어 붙을 수 없고, 같은 프로파일로
다시 띄우면 기존 프로세스에 창만 붙고 플래그는 무시된다. 그래서 이 도구
몫의 프로파일을 따로 두고 거기에 한 번만 로그인해 둔다. 비밀번호는
어디에도 저장하지 않는다 - 세션 쿠키가 그 폴더에 남는다.

크롬을 우리가 직접 띄우고 CDP로 붙는 이유: launch_persistent_context 로
띄우면 스크립트가 끝날 때 창이 함께 닫힌다. 채워 둔 폼을 사람이 읽고
저장해야 하므로 창은 스크립트보다 오래 살아야 한다.

  python pms.py --login              전용 프로파일에 한 번 로그인한다
  python pms.py --check              로그인 상태와 폼의 선택지를 확인한다
  python pms.py --fill rows.json     폼을 채운다 (저장은 사람이 누른다)
"""

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import secrets as secret_store
from _log import log_error

HOME = os.path.expanduser('~')

# 폼에서 직접 확인한 선택지. 바뀌면 --check 가 다른 목록을 보여 준다.
#
# 이 select 에는 빈 항목이 없다. 그래서 "분류를 모르겠으면 비운다"가 폼에서는
# 불가능하고, 고르지 않으면 첫 항목(개발)이 남는다. 지어낸 값이 조용히 들어가는
# 자리라서, 고르지 못했을 때는 무엇이 남았는지 반드시 알려 준다.
CATEGORIES = (('development', '개발'), ('design', '디자인'), ('planning', '기획'),
              ('meeting', '회의'), ('review', '리뷰'), ('documentation', '문서화'),
              ('support', '지원'), ('other', '기타'))
CATEGORY_LABEL = dict(CATEGORIES)
# 제출문에는 사람이 읽는 라벨("개발")이 적힌다. 값("development")으로 바꾸는 표를
# 여기 한 곳에만 둔다 - 뷰어가 같은 표를 또 들고 있으면 어긋난다.
CATEGORY_VALUE = dict((label, value) for value, label in CATEGORIES)


def category_value(given):
    """값이든 라벨이든 폼이 받는 값으로. 모르는 것은 그대로 돌려준다."""
    given = (given or '').strip()
    if given in CATEGORY_LABEL:
        return given
    return CATEGORY_VALUE.get(given, given)

SEL_ADD_ROW = 'a#add-work-item'
SEL_DATE = '#daily_report_report_date'
SEL_PROGRESS = '#daily_report_progress'
SEL_EXTRA_LEGEND = 'fieldset#additional-fields legend'
EXTRA_FIELDS = (('work_done', '#daily_report_work_done'),
                ('work_planned', '#daily_report_work_planned'),
                ('issues', '#daily_report_issues'))


def load_cfg():
    """보고 폴더의 config.json 에서 pms 설정만 꺼낸다."""
    root = os.environ.get('WORK_REPORT_DIR') or os.path.join(HOME, 'work-report')
    cfg = {}
    try:
        with io.open(os.path.join(root, 'config.json'), encoding='utf-8-sig') as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        pass
    # 빈 문자열은 "안 적었다"와 같이 본다. 설치가 빈 값으로 키를 만들어 두므로
    # setdefault 로는 기본값이 걸리지 않는다.
    pms = dict(cfg.get('pms') or {})
    pms['url'] = (pms.get('url') or '').rstrip('/')
    pms['project'] = pms.get('project') or ''
    pms['port'] = int(pms.get('port') or 9333)
    # 토큰은 config.json 에 두지 않는다. 거기는 보고서를 쓰는 에이전트가 읽는
    # 폴더다. 옛 설정에 남아 있으면 금고로 옮기고 설정에서는 지운다.
    stale = (pms.get('token') or '').strip()
    if stale:
        try:
            secret_store.put(root, 'pms_token', stale)
            cfg['pms'] = dict(cfg.get('pms') or {}, token='')
            with io.open(os.path.join(root, 'config.json'), 'w',
                         encoding='utf-8', newline='') as fh:
                fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
        except Exception:
            pass
    pms['token'] = secret_store.get(root, 'pms_token') or stale
    # 프로파일은 보고 폴더 안에 둔다. 업데이트가 덮지 않는 자리이고,
    # 지우면 로그인만 다시 하면 된다.
    pms['profile'] = pms.get('profile') or os.path.join(root, 'browser')
    # 처음인지 세션이 끊긴 것인지는 안내 문구가 달라진다. 크롬을 띄우면 쿠키
    # 파일이 바로 생기므로, 띄우기 전인 지금 봐 둔다.
    pms['fresh'] = not os.path.isfile(
        os.path.join(pms['profile'], 'Default', 'Network', 'Cookies'))
    return root, pms


def require_config(pms, root):
    """무엇이 비었는지, 어디에 무엇을 적어야 하는지까지 말한다."""
    missing = [k for k in ('url', 'project') if not pms.get(k)]
    if not missing:
        return
    raise Stop(EXIT_CONFIG, 'PMS 설정이 비어 있다: %s' % ', '.join(missing),
               ['앱의 설정 화면에서 PMS 주소와 프로젝트를 채워라.',
                '직접 고치려면: %s' % os.path.join(root, 'config.json'),
                '"pms": { "url": "https://pms.example.com", "project": "<프로젝트 식별자>" }',
                '프로젝트 식별자는 PMS 주소의 /projects/<여기> 부분이다.'])


# 크롬이 없는 PC도 있다. 엣지도 같은 크로미움이라 프로파일·디버깅 포트 플래그가
# 그대로 통하므로 받아 준다. 설정의 pms.chrome_bin 이 있으면 그것이 먼저다.
BROWSERS = (
    (r'Google\Chrome\Application\chrome.exe', '크롬'),
    (r'Microsoft\Edge\Application\msedge.exe', '엣지'),
)


def window_state(pg, state):
    """크롬 창을 내리거나 올린다.

    크롬은 자기 창을 직접 띄우므로 CreateProcess 의 시작 상태(SW_SHOWMINNOACTIVE)를
    무시한다. 실제로 재 보니 창이 포그라운드로 떴다. 그래서 띄운 뒤 CDP로 바꾼다.
    보고서를 쓰는 중에 브라우저가 화면을 가로채면 하던 일이 끊기고, 예약 실행이면
    더 나쁘다. 다 채운 다음 사람이 볼 때 올려 준다.
    """
    try:
        sess = pg.context.new_cdp_session(pg)
        win = sess.send('Browser.getWindowForTarget')
        sess.send('Browser.setWindowBounds',
                  {'windowId': win['windowId'], 'bounds': {'windowState': state}})
        return True
    except Exception:
        return False        # 창 하나 못 내린 것으로 채운 일을 망치지 않는다


def chrome_path(preferred=''):
    if preferred and os.path.isfile(preferred):
        return preferred
    for leaf, _label in BROWSERS:
        for base in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
            c = os.path.join(os.environ.get(base, ''), leaf)
            if os.environ.get(base) and os.path.isfile(c):
                return c
    return None


def port_endpoint(port, timeout=1.0):
    """그 포트에 우리 크롬이 이미 떠 있으면 CDP 주소를 준다."""
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=timeout):
            return 'http://127.0.0.1:%d' % port
    except Exception:
        return None


def free_port(port):
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def ensure_browser(pms, first_url=None):
    """전용 프로파일 크롬을 띄우거나, 이미 떠 있으면 그대로 쓴다."""
    port = int(pms['port'])
    ep = port_endpoint(port)
    if ep:
        return ep, False
    if not free_port(port):
        raise Stop(EXIT_ENV, '포트 %d 를 다른 프로그램이 쓰고 있다.' % port,
                   ['설정의 pms.port 를 다른 번호로 바꿔라 (예: %d).' % (port + 1)])
    exe = chrome_path(pms.get('chrome_bin', ''))
    if not exe:
        raise Stop(EXIT_ENV, '크롬도 엣지도 찾지 못했다.',
                   ['설정의 pms.chrome_bin 에 chrome.exe 또는 msedge.exe 전체 경로를 적어라.',
                    '예: C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'])
    profile = pms['profile']
    if not os.path.isdir(profile):
        os.makedirs(profile)
    argv = [exe,
            '--user-data-dir=' + profile,
            '--remote-debugging-port=%d' % port,
            '--no-first-run', '--no-default-browser-check',
            # 이 창은 이 도구 몫이다. 기본 브라우저나 세션 복원에 끼어들지 않는다.
            '--disable-session-crashed-bubble', '--disable-features=Translate']
    if first_url:
        argv.append(first_url)
    subprocess.Popen(argv, close_fds=True)
    for _ in range(40):              # 최대 20초
        time.sleep(0.5)
        ep = port_endpoint(port)
        if ep:
            return ep, True
    raise Stop(EXIT_ENV, '크롬은 떴지만 조작할 수 있는 상태가 아니다.',
               ['같은 프로파일로 크롬이 이미 떠 있으면 그 창을 닫고 다시 해 보라.',
                '프로파일: %s' % pms['profile']])


def connect(ep):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise Stop(EXIT_PLAYWRIGHT, '폼을 채우려면 playwright 가 필요하다. 아직 없다.',
                   ['설치: "%s" -m pip install playwright' % sys.executable,
                    '브라우저는 이미 설치된 크롬을 쓰므로 따로 받지 않는다.',
                    '설치 전에도 폼을 열어 주는 것까지는 된다.'])
    p = sync_playwright().start()
    return p, p.chromium.connect_over_cdp(ep)


def pick_page(browser, want):
    """같은 주소의 탭이 있으면 재사용한다. 창이 늘어나지 않게."""
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    for pg in ctx.pages:
        if want.split('?')[0] in (pg.url or ''):
            return pg
    for pg in ctx.pages:                      # 빈 탭이 있으면 그걸 쓴다
        if (pg.url or '').startswith(('about:', 'chrome://newtab')):
            return pg
    return ctx.new_page()


def form_url(pms, date):
    base = pms['url'].rstrip('/')
    url = '%s/projects/%s/daily_reports/new' % (base, pms['project'])
    return url + ('?date=' + date if date else '')


def logged_in(pg):
    return '/login' not in (pg.url or '') and pg.locator('#loggedas').count() > 0


def need_login(pms):
    return Stop(EXIT_LOGIN,
                'PMS에 아직 로그인하지 않았다.' if pms.get('fresh') else 'PMS 로그인이 풀렸다.',
                ['열리는 창에서 한 번만 로그인하면 다음부터 묻지 않는다:',
                 'pms.ps1 -Login',
                 '이 창은 work-report 전용이라 평소 쓰는 크롬과 섞이지 않는다.',
                 '비밀번호는 저장하지 않는다. 세션만 %s 에 남는다.' % pms['profile']])


def open_form(pms, url):
    """채우지 않고 폼만 띄운다.

    playwright 가 없어도, 로그인이 풀려 있어도 이건 된다. 제출문이 없는 날에도
    이 길로 간다 - 사람을 빈손으로 돌려보내지 않는다.
    """
    exe = chrome_path(pms.get('chrome_bin', ''))
    if not exe:
        raise Stop(EXIT_ENV, '크롬도 엣지도 찾지 못했다.',
                   ['설정의 pms.chrome_bin 에 chrome.exe 또는 msedge.exe 전체 경로를 적어라.'])
    profile = pms['profile']
    if not os.path.isdir(profile):
        os.makedirs(profile)
    subprocess.Popen([exe, '--user-data-dir=' + profile,
                      '--remote-debugging-port=%d' % int(pms['port']),
                      '--no-first-run', '--no-default-browser-check', url],
                     close_fds=True)


def do_login(pms):
    base = pms['url'].rstrip('/')
    ep, started = ensure_browser(pms, base + '/login')
    p, browser = connect(ep)
    try:
        pg = pick_page(browser, base)
        if not started:
            pg.goto(base + '/login', wait_until='domcontentloaded')
        print('열린 창에서 로그인해라. "로그인 유지"를 켜면 다음부터 묻지 않는다.')
        print('(이 창은 work-report 전용이다. 평소 쓰는 크롬과 섞이지 않는다)')
        for _ in range(240):                  # 최대 20분 기다린다
            time.sleep(5)
            try:
                if logged_in(pg):
                    print('로그인 확인: %s' % pg.locator('#loggedas').inner_text().strip()[:60])
                    return 0
            except Exception:
                pass                          # 사용자가 다른 페이지를 보는 중일 수 있다
        print('로그인을 확인하지 못했다. 창을 열어 둔 채 다시 --login 을 돌려도 된다.')
        return 2
    finally:
        browser.close()                       # CDP 연결만 끊는다. 창은 남는다
        p.stop()


# 목록 화면의 숨은 상세 행에 보고서 본문이 그대로 들어 있다. 편집 화면은
# 작성자에게도 403이라(기한이 지나면 못 고친다) 여기가 유일한 창구다.
JS_ROWS = r"""() => [...document.querySelectorAll('tr.report-summary-row')].map(tr => {
    const a = tr.querySelector('.author-cell a.user');
    const det = document.getElementById('report-detail-' + tr.dataset.reportId);
    const secs = det ? [...det.querySelectorAll('.detail-section')].map(s => ({
        head: (s.querySelector('h4') || {textContent: ''}).textContent.trim(),
        groups: [...s.querySelectorAll('.category-group')].map(g => {
            const NL = String.fromCharCode(10);
            const md = el => [...el.children].map(c => {
                if (c.tagName === 'UL' || c.tagName === 'OL') {
                    return [...c.children].map(li => '- ' + li.textContent.trim()).join(NL);
                }
                return c.textContent.trim();
            }).filter(Boolean).join(NL);
            const wikis = [...g.querySelectorAll('.item-list .wiki')];
            const text = wikis.length ? wikis.map(md).join(NL)
                                      : ((g.querySelector('.item-list') || g).innerText || '').trim();
            return { badge: (g.querySelector('.badge') || {textContent: ''}).textContent.trim(),
                     text: text.trim() };
        })
    })) : [];
    const issues = det ? [...det.querySelectorAll('a[href*="/issues/"]')]
        .map(x => (x.getAttribute('href').match(/issues[/](\d+)/) || [])[1])
        .filter(Boolean) : [];
    return { id: tr.dataset.reportId,
             date: (tr.querySelector('.date-cell') || {textContent: ''}).textContent.trim(),
             user: a ? a.getAttribute('href') : '',
             progress: (tr.querySelector('.progress-text') || {textContent: ''}).textContent.trim(),
             issues: [...new Set(issues)],
             secs: secs };
})"""

# 월 이동은 date= 가 아니라 month=/year= 다. 화면의 왼쪽 화살표가 그 주소를 쓴다.
URL_MONTH = '%s/projects/%s/daily_reports?month=%d&year=%d&view_type=monthly'

NL = chr(10)          # 패치 도구가 역슬래시를 먹는 일이 있어 상수로 둔다

# 뷰어가 결과에 따라 다른 안내를 띄울 수 있게 코드를 나눈다. 사람이 다음에 무엇을
# 해야 하는지가 상황마다 다르므로, 전부 1로 끝내면 화면에서 안내를 만들 수 없다.
EXIT_OK = 0           # 채웠다
EXIT_NOTHING = 10     # 채울 것이 없어 폼만 열었다
EXIT_LOGIN = 2        # 로그인이 필요하다
EXIT_CONFIG = 3       # 설정이 비었다
EXIT_PLAYWRIGHT = 4   # 라이브러리가 없다
EXIT_ENV = 5          # 크롬이 없거나 포트가 막혔다


class Stop(Exception):
    """사람이 다음에 할 일을 아는 채로 멈춘다."""

    def __init__(self, code, message, how=()):
        Exception.__init__(self, message)
        self.code = code
        self.message = message
        self.how = list(how)

    def tell(self):
        print(self.message)
        for line in self.how:
            print('  ' + line)
        return self.code


MARK_BEGIN = '<!-- work-report:pms begin -->'
MARK_END = '<!-- work-report:pms end -->'
PASTE_MARK = '<!-- PASTE BELOW -->'


def months_back(n):
    """올해 이번 달부터 n개월 거꾸로 (년, 월) 을 준다."""
    import datetime
    d = datetime.date.today().replace(day=1)
    out = []
    for _ in range(max(1, n)):
        out.append((d.year, d.month))
        d = (d - datetime.timedelta(days=1)).replace(day=1)
    return out


def my_user_path(pg):
    """로그인한 사람의 /users/<id>. 목록에는 남의 보고서도 함께 나온다."""
    return pg.eval_on_selector('#loggedas a[href^="/users/"]', 'e => e.getAttribute("href")')


def as_text(report):
    """보고서 한 건을 예시로 쓸 평문으로.

    폼에 들어 있던 모양 그대로 둔다. 보기 좋으라고 들여쓰기를 넣으면 예시를
    읽은 쪽이 그 들여쓰기까지 따라 쓴다 - 예시는 꾸미는 것이 아니라 베끼는 것이다.
    """
    lines = ['[%s]' % report['date']]
    for sec in report['secs']:
        for g in sec['groups']:
            lines.append('(%s)' % g['badge'] if g['badge'] else '(분류 없음)')
            for line in g['text'].splitlines():
                if line.strip():
                    lines.append(line.strip())
    return NL.join(lines)


def write_samples(root, reports, keep):
    """custom/my-reports.md 의 표시선 아래에 최근 것들을 넣는다.

    손으로 붙여넣은 것을 지우지 않으려고 우리 영역을 따로 표시해 둔다.
    그 바깥은 건드리지 않는다.
    """
    path = os.path.join(root, 'custom', 'my-reports.md')
    if not os.path.isfile(path):
        return None
    body = io.open(path, encoding='utf-8-sig').read()
    block = NL.join([MARK_BEGIN,
                        '<!-- PMS에서 받아온 내 일일보고다. 손으로 고치지 마라 - 다음 수집이 덮는다. -->',
                        ''] + [as_text(r) for r in reports[:keep]] + [MARK_END, ''])
    if MARK_BEGIN in body and MARK_END in body:
        head, rest = body.split(MARK_BEGIN, 1)
        _old, tail = rest.split(MARK_END, 1)
        body = head + block + tail
    elif PASTE_MARK in body:
        head, tail = body.split(PASTE_MARK, 1)
        body = head + PASTE_MARK + NL + NL + block + tail
    else:
        body = body.rstrip() + NL + NL + block
    io.open(path, 'w', encoding='utf-8', newline='').write(body)
    return path


def summarize(reports):
    """그 사람이 실제로 어떻게 써 왔는지를 센다.

    코드에 "보통 한 행이다", "일감은 안 쓴다" 같은 규칙을 박으면 그 사람에게만
    맞는 도구가 된다. 사람마다 다르게 쓰므로 규칙이 아니라 센 값을 넘긴다.
    """
    cats = {}
    rows = {}
    with_issues = 0
    issue_count = 0
    examples = {}
    for r in reports:
        groups = [g for sec in r['secs'] if '한 일' in sec['head'] for g in sec['groups']]
        rows[len(groups)] = rows.get(len(groups), 0) + 1
        for g in groups:
            key = g['badge'] or '(분류 없음)'
            cats[key] = cats.get(key, 0) + 1
            examples.setdefault(key, [])
            if len(examples[key]) < 3 and g['text'].strip():
                examples[key].append({'date': r['date'], 'text': g['text']})
        ids = r.get('issues') or []
        if ids:
            with_issues += 1
            issue_count += len(ids)
    return {'reports': len(reports), 'categories': cats, 'rows_per_report': rows,
            'reports_with_issues': with_issues, 'issue_links': issue_count,
            'examples': examples}


def write_patterns(root, reports, stat):
    """센 값을 보고서 작성자(에이전트)가 읽을 수 있게 적어 둔다."""
    path = os.path.join(root, 'pms', 'patterns.md')
    n = stat['reports'] or 1
    L = ['# 내 일일보고 작성 습관',
         '',
         'PMS에서 받아온 내 지난 보고서 %d건을 센 것이다. 규칙이 아니라 근거다.' % stat['reports'],
         '제출문을 쓸 때 이 분포에 맞추고, 근거가 없는 칸은 비운다.',
         '']
    L.append('## 분류')
    L.append('')
    for k, v in sorted(stat['categories'].items(), key=lambda x: -x[1]):
        L.append('- %s: %d회 (%d%%)' % (k, v, round(v * 100.0 / n)))
    L.append('')
    L.append('## 작업 항목 행 수')
    L.append('')
    for k, v in sorted(stat['rows_per_report'].items()):
        L.append('- %d행: %d건' % (k, v))
    L.append('')
    L.append('## 연결된 일감')
    L.append('')
    if stat['reports_with_issues']:
        L.append('- %d건 중 %d건에서 썼다 (일감 %d개 연결)'
                 % (stat['reports'], stat['reports_with_issues'], stat['issue_links']))
        L.append('- 쓰던 사람이다. 그날 작업과 맞는 일감이 폼 목록에 있으면 체크한다.')
    else:
        L.append('- %d건 모두 쓰지 않았다.' % stat['reports'])
        L.append('- 근거 없이 새로 체크하지 않는다. 다만 폼에 목록은 그대로 있다.')
    L.append('')
    L.append('## 분류별 예시')
    L.append('')
    # 울타리가 예시의 일부로 읽히면 보고서에 그대로 따라 들어간다. 한 번 겪은 일이다.
    L.append('아래 울타리(```)는 예시를 구분하려고 친 것이다. 보고서에는 쓰지 않는다.')
    L.append('')
    for k, items in sorted(stat['examples'].items(), key=lambda x: -stat['categories'].get(x[0], 0)):
        L.append('### %s' % k)
        L.append('')
        for e in items:
            L.append('%s' % e['date'])
            L.append('```')
            L.extend(e['text'].splitlines())
            L.append('```')
            L.append('')
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    io.open(path, 'w', encoding='utf-8', newline='').write(NL.join(L) + NL)
    return path


def do_fetch(pms, root, months, keep):
    projects = pms.get('projects') or [pms['project']]
    ep, _ = ensure_browser(pms)
    p, browser = connect(ep)
    try:
        pg = pick_page(browser, pms['url'])
        pg.goto(pms['url'] + '/projects/' + projects[0] + '/daily_reports', wait_until='domcontentloaded')
        if not logged_in(pg):
            raise need_login(pms)
        me = my_user_path(pg)
        mine = []
        for proj in projects:
            for year, month in months_back(months):
                pg.goto(URL_MONTH % (pms['url'], proj, month, year), wait_until='domcontentloaded')
                for r in pg.evaluate(JS_ROWS):
                    if r['user'] == me:
                        r['project'] = proj
                        mine.append(r)
        mine.sort(key=lambda r: r['date'], reverse=True)

        folder = os.path.join(root, 'pms')
        if not os.path.isdir(folder):
            os.makedirs(folder)
        out = os.path.join(folder, 'my-daily-reports.json')
        io.open(out, 'w', encoding='utf-8', newline='').write(
            json.dumps({'user': me, 'projects': projects, 'reports': mine}, ensure_ascii=False, indent=1))

        stat = summarize(mine)
        print('내 일일보고 %d건 (%s)' % (len(mine), ', '.join(projects)))
        if mine:
            print('기간        : %s ~ %s' % (mine[-1]['date'], mine[0]['date']))
        print('분류 쓰임   : %s' % ', '.join('%s %d' % kv for kv in
                                          sorted(stat['categories'].items(), key=lambda x: -x[1])))
        print('행 수       : %s' % ', '.join('%d행 %d건' % kv for kv in sorted(stat['rows_per_report'].items())))
        print('일감 연결   : %d건에서 %d개' % (stat['reports_with_issues'], stat['issue_links']))
        print('저장        : %s' % out)
        print('작성 습관   : %s' % write_patterns(root, mine, stat))
        sample = write_samples(root, mine, keep)
        if sample:
            print('문체 예시   : %s (최근 %d건)' % (sample, min(keep, len(mine))))
        else:
            print('문체 예시   : custom/my-reports.md 가 없다. 설정에서 "내 보고서 따라하기"를 켜면 생긴다')
        return 0
    finally:
        browser.close()
        p.stop()


def open_issues(pms, project=None):
    """연결할 수 있는 일감(열린 것)을 API로 받아 온다.

    폼의 체크박스 목록과 같은 것이다. 보고서를 쓰는 시점에는 브라우저가 없고,
    예약 실행이면 창을 띄울 수도 없다. 토큰이 하는 일이 이것이다.
    """
    if not pms.get('token'):
        raise Stop(EXIT_CONFIG, 'PMS 토큰이 없다. 일감 목록은 토큰으로만 받아 온다.',
                   ['PMS에서 "내 계정 > API 접근키"를 복사해 설정의 PMS 토큰에 넣어라.',
                    '토큰이 없어도 보고서와 폼 채우기는 그대로 된다. 일감만 비어 있게 된다.'])
    project = project or pms['project']
    url = '%s/issues.json?project_id=%s&status_id=open&limit=100' % (pms['url'], project)
    req = urllib.request.Request(url, headers={'X-Redmine-API-Key': pms['token']})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Stop(EXIT_CONFIG, 'PMS 토큰이 받아들여지지 않았다 (%d).' % e.code,
                       ['설정의 PMS 토큰을 다시 확인해라. 다시 발급받아야 할 수도 있다.'])
        raise Stop(EXIT_ENV, 'PMS가 일감 목록을 주지 않았다 (%d).' % e.code, [url])
    except Exception as e:
        raise Stop(EXIT_ENV, 'PMS에 닿지 못했다: %s' % e, [url])
    out = []
    for i in data.get('issues') or []:
        out.append({'id': i['id'], 'subject': i.get('subject') or '',
                    'status': (i.get('status') or {}).get('name', ''),
                    'tracker': (i.get('tracker') or {}).get('name', ''),
                    'assigned': ((i.get('assigned_to') or {}).get('name') or '')})
    return out


def do_issues(pms, root):
    """에이전트가 보고서를 쓸 때 읽을 수 있게 파일로 남긴다."""
    found = open_issues(pms)
    folder = os.path.join(root, 'pms')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, 'open-issues.md')
    import datetime
    L = ['# 연결할 수 있는 일감',
         '',
         '%s 기준, %s 프로젝트에서 열려 있는 일감이다. 담당자와 상관없이 전부 나온다.'
         % (datetime.date.today().strftime('%Y-%m-%d'), pms['project']),
         '제출문의 "연결된 일감" 줄에는 **그날 한 일과 분명히 맞는 것만** 적는다.',
         '맞는 것이 없으면 그 줄을 만들지 않는다. 비슷해 보인다고 고르지 않는다.',
         '']
    for i in found:
        L.append('- #%s %s (%s%s)' % (i['id'], i['subject'], i['status'],
                                      ', 담당 ' + i['assigned'] if i['assigned'] else ''))
    if not found:
        L.append('- (열린 일감이 없다)')
    io.open(path, 'w', encoding='utf-8', newline='').write(NL.join(L) + NL)
    print('열린 일감 %d개 -> %s' % (len(found), path))
    return EXIT_OK


def validate_report(path):
    """보고서의 제출문 절이 폼에 들어갈 수 있는 모양인지 본다.

    폼에 넣어 보기 전에 글만 보고 알 수 있는 것들이다. 틀린 채로 채우면
    울타리나 들여쓰기가 그대로 PMS에 올라가고, 그때는 사람이 지워야 한다.
    제출문 절이 없는 것은 잘못이 아니다 - 만들지 않아도 되는 절이다.
    """
    import re
    try:
        body = io.open(path, encoding='utf-8-sig').read()
    except OSError as e:
        return ['보고서를 읽지 못했다: %s' % e]

    lines = body.split(NL)
    start = -1
    for i, line in enumerate(lines):
        if line.startswith('## ') and '제출문' in line:
            start = i + 1
            break
    if start < 0:
        return []
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith('## '):
            end = i
            break

    bad = []
    seen_category = False
    has_content = False
    for n in range(start, end):
        raw = lines[n]
        line = raw.strip()
        where = '%d행' % (n + 1)
        if not line:
            continue
        if line.startswith('```') or line.startswith('~~~'):
            bad.append('%s: 코드 울타리(```)가 있다. 제출문은 울타리 없이 쓴다' % where)
            continue
        if raw[:1].isspace():
            bad.append('%s: 줄이 공백으로 시작한다. 들여쓰기 없이 쓴다 -> "%s"' % (where, line[:30]))
        if raw.lstrip().startswith('#'):
            bad.append('%s: 머리말(#)이 있다. 제출문 안에는 절을 만들지 않는다' % where)
        if '|' in line and line.count('|') >= 2:
            bad.append('%s: 표가 있다. 제출문에는 표를 넣지 않는다' % where)
        m = re.match(r'^\(([^)]*)\)$', line)
        if m:
            seen_category = True
            label = m.group(1).strip()
            if label not in CATEGORY_VALUE and label not in CATEGORY_LABEL:
                bad.append('%s: 분류 "%s" 는 폼에 없다. 쓸 수 있는 것: %s'
                           % (where, label, ', '.join(l for _v, l in CATEGORIES)))
            continue
        if re.match(r'^연결된\s*일감', line):
            rest = line.split(':', 1)[-1] if ':' in line else ''
            if not re.findall(r'#?\d{1,7}', rest):
                bad.append('%s: 연결된 일감 줄에 번호가 없다. 예) 연결된 일감: #1353, #1354' % where)
            continue
        has_content = True

    if not has_content:
        bad.append('제출문 절에 내용이 없다. 쓸 것이 없으면 절 자체를 만들지 않는다')
    elif not seen_category:
        bad.append('분류 줄이 없다. 덩어리마다 "(개발)" 처럼 분류를 한 줄로 먼저 적는다 '
                   '(쓸 수 있는 것: %s)' % ', '.join(l for _v, l in CATEGORIES))
    return bad


def fill_form(pg, data, report):
    """행을 만들고 값을 넣는다. 저장은 누르지 않는다."""
    if data.get('date'):
        pg.fill(SEL_DATE, data['date'])
        # 프로젝트 설정에 따라 오래된 날짜는 저장이 거부된다. 폼을 채우는
        # 단계에서는 알 수 없고 저장을 눌러야 드러나므로 미리 알려 둔다.
        import datetime
        if data['date'] != datetime.date.today().strftime('%Y-%m-%d'):
            report.append('오늘이 아닌 날짜다 (%s). PMS 설정에 따라 저장이 거부될 수 있다' % data['date'])
    if data.get('progress') not in (None, ''):
        pg.fill(SEL_PROGRESS, str(int(data['progress'])))

    # 이미 쓴 보고서를 열면 행이 남아 있다. 지우지 않고 뒤에 붙인다 -
    # 사람이 쓴 것을 도구가 말없이 버리면 안 된다.
    start = pg.locator('select[name^="daily_report[items_attributes]"]').count()
    if start:
        report.append('이미 있던 작업 항목 %d개는 그대로 두고 뒤에 붙였다' % start)

    for n, item in enumerate(data.get('items') or []):
        i = start + n
        pg.click(SEL_ADD_ROW)
        sel = 'select[name="daily_report[items_attributes][%d][category]"]' % i
        pg.wait_for_selector(sel, timeout=5000)
        cat = category_value(item.get('category'))
        if cat in CATEGORY_LABEL:
            pg.select_option(sel, cat)
        else:
            left = pg.input_value(sel)
            why = '분류가 비어 있다' if not cat else '분류 "%s" 는 폼에 없는 값이다' % cat
            report.append('%d행: %s. 폼 기본값 "%s" 에 머물러 있으니 직접 고쳐라'
                          % (i + 1, why, CATEGORY_LABEL.get(left, left)))
        pg.fill('#daily_report_items_%d_content' % i, item.get('content') or '')

    for iid in (data.get('linked_issue_ids') or []):
        box = 'input[name="daily_report[linked_issue_ids][]"][value="%s"]' % iid
        if pg.locator(box).count():
            pg.check(box)
        else:
            report.append('일감 #%s 는 이 폼의 목록에 없다 (닫힌 일감이거나 다른 프로젝트다)' % iid)

    extra = [(k, s) for k, s in EXTRA_FIELDS if (data.get(k) or '').strip()]
    if extra:
        # 접혀 있으면 숨은 칸이라 채울 수 없다. 먼저 펼친다.
        if not pg.locator(extra[0][1]).is_visible():
            pg.click(SEL_EXTRA_LEGEND)
        for key, sel in extra:
            pg.fill(sel, data[key])


def do_fill(pms, path):
    with io.open(path, encoding='utf-8-sig') as fh:
        data = json.load(fh)
    project = data.get('project') or pms['project']
    if not project:
        raise SystemExit('프로젝트가 없다. rows.json 의 project 나 config.json 의 pms.project 를 채워라.')
    pms = dict(pms, project=project)
    url = form_url(pms, data.get('date'))

    # 채울 것이 하나도 없는 날은 막는 게 아니라 폼만 열어 준다. 제출문이 없는
    # 날에도 사람은 PMS에 뭔가 써야 하고, 그 자리까지는 데려다 줄 수 있다.
    filled = (data.get('items') or data.get('linked_issue_ids')
              or any((data.get(k) or '').strip() for k, _ in EXTRA_FIELDS))
    if not filled:
        open_form(pms, url)
        print('채울 것이 없어 폼만 열었다.')
        print('  제출문 절이 있는 보고서를 먼저 만들면 그 내용으로 채운다.')
        print('  앱 설정에서 "내 보고서 따라하기"를 켜고 pms.ps1 -Fetch 를 한 번 돌리면 생긴다.')
        return EXIT_NOTHING

    ep, _started = ensure_browser(pms, url)
    try:
        p, browser = connect(ep)
    except Stop as stop:
        if stop.code == EXIT_PLAYWRIGHT:
            open_form(pms, url)              # 채우지는 못해도 자리까지는 데려다 준다
            stop.how.append('폼은 열어 두었다. 그때까지는 직접 붙여넣어라.')
        raise
    try:
        pg = pick_page(browser, url)
        pg.goto(url, wait_until='domcontentloaded')
        if not logged_in(pg):
            raise need_login(pms)
        report = []
        fill_form(pg, data, report)
        window_state(pg, 'minimized')     # 다 채웠으면 물러난다
        n = len(data.get('items') or [])
        print('채웠다: 작업 항목 %d개, 일감 %d개' % (n, len(data.get('linked_issue_ids') or [])))
        if report:
            print('확인 필요 %d건:' % len(report))
            for line in report:
                print('  - ' + line)
        print('PMS 창에 채워 두었다. 확인하고 [저장]을 눌러라. 저장은 하지 않았다.')
        return 0
    finally:
        browser.close()
        p.stop()


def do_validate(path):
    bad = validate_report(path)
    if not bad:
        print('제출문 절이 양식에 맞다.')
        return EXIT_OK
    print('제출문 절이 양식에 맞지 않다. %d곳:' % len(bad))
    for b in bad:
        print('  - ' + b)
    print('양식은 보고서 양식 파일의 "제출문 절 쓰는 법"에 있다. 고쳐서 다시 써라.')
    return EXIT_NOTHING


def do_show(pms):
    """채워 둔 창을 앞으로 가져온다. 백그라운드로 띄웠으니 볼 길이 있어야 한다."""
    ep = port_endpoint(int(pms['port']))
    if not ep:
        return do_open(pms, None)
    p, browser = connect(ep)
    try:
        pages = [x for ctx in browser.contexts for x in ctx.pages if 'daily_reports' in (x.url or '')]
        if not pages:
            print('채워 둔 창을 찾지 못했다. 다시 채워라.')
            return EXIT_NOTHING
        window_state(pages[0], 'normal')
        pages[0].bring_to_front()
        print('PMS 창을 앞으로 가져왔다.')
        return EXIT_OK
    finally:
        browser.close()
        p.stop()


def do_open(pms, date):
    """폼만 연다. 채우지 않는다."""
    url = form_url(pms, date)
    open_form(pms, url)
    print('폼을 열었다: %s' % url)
    print('  로그인이 풀려 있으면 로그인 화면이 먼저 뜬다.')
    return EXIT_OK


def do_check(pms):
    url = form_url(pms, None)
    ep, _ = ensure_browser(pms, url)
    p, browser = connect(ep)
    try:
        pg = pick_page(browser, url)
        pg.goto(url, wait_until='domcontentloaded')
        print('주소        : %s' % pg.url)
        if not logged_in(pg):
            raise need_login(pms)
        print('로그인      : %s' % pg.locator('#loggedas').inner_text().strip()[:60])
        print('보고 날짜   : %s' % pg.input_value(SEL_DATE))
        pg.click(SEL_ADD_ROW)                 # 선택지는 행을 만들어야 보인다
        sel = 'select[name="daily_report[items_attributes][0][category]"]'
        pg.wait_for_selector(sel, timeout=5000)
        opts = pg.eval_on_selector(sel, 'e => [...e.options].map(o => o.value + "=" + o.text)')
        print('분류        : %s' % ', '.join(opts))
        known = [v for v, _ in CATEGORIES]
        seen = [o.split('=')[0] for o in opts]
        if seen != known:
            print('            ! 스크립트가 아는 목록과 다르다: %s' % ', '.join(known))
        boxes = pg.eval_on_selector_all(
            'input[name="daily_report[linked_issue_ids][]"]',
            'es => es.map(e => e.value + " " + (e.closest("label")||e.parentElement).textContent.trim())')
        print('연결 가능한 일감 %d개:' % len(boxes))
        for b in boxes:
            print('  %s' % ' '.join(b.split())[:72])
        pg.reload()                           # 확인용으로 만든 행을 남기지 않는다
        return 0
    finally:
        browser.close()
        p.stop()


def main():
    ap = argparse.ArgumentParser(description='PMS 일일보고 폼을 채운다 (저장은 하지 않는다)')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--login', action='store_true', help='전용 프로파일에 한 번 로그인한다')
    g.add_argument('--check', action='store_true', help='로그인 상태와 폼의 선택지를 본다')
    g.add_argument('--fill', metavar='ROWS.JSON', help='폼을 채운다')
    g.add_argument('--fetch', action='store_true', help='내 지난 일일보고를 받아 온다')
    g.add_argument('--open', action='store_true', help='폼만 연다 (채우지 않는다)')
    g.add_argument('--validate', metavar='REPORT.MD', help='제출문 절이 양식에 맞는지 본다')
    g.add_argument('--issues', action='store_true', help='연결할 수 있는 일감 목록을 받아 둔다')
    g.add_argument('--show', action='store_true', help='채워 둔 PMS 창을 앞으로 가져온다')
    ap.add_argument('--date', help='--open 이 열 날짜 (YYYY-MM-DD)')
    ap.add_argument('--months', type=int, default=3, help='--fetch 가 거슬러 볼 개월 수 (기본 3)')
    ap.add_argument('--keep', type=int, default=10, help='문체 예시로 남길 최근 보고서 수 (기본 10)')
    a = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')

    root, pms = load_cfg()

    # 검사는 PMS 설정도 브라우저도 필요 없다. 글만 본다.
    if a.validate:
        return do_validate(a.validate)
    try:
        require_config(pms, root)
        if a.login:
            return do_login(pms)
        if a.open:
            return do_open(pms, a.date)
        if a.show:
            return do_show(pms)
        if a.check:
            return do_check(pms)
        if a.issues:
            return do_issues(pms, root)
        if a.fetch:
            return do_fetch(pms, root, a.months, a.keep)
        return do_fill(pms, a.fill)
    except Stop as stop:
        return stop.tell()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as err:
        root = os.environ.get('WORK_REPORT_DIR') or os.path.join(HOME, 'work-report')
        log_error(root, 'pms', ' '.join(sys.argv[1:]) or '(인자 없음)', err)
        raise
