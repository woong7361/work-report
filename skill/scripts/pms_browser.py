# PMS browser process and CDP lifecycle.
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from pms_config import (EXIT_ENV, EXIT_LOGIN, EXIT_PLAYWRIGHT, Stop)


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
    exact = [pg for pg in ctx.pages if want.split('?')[0] in (pg.url or '')]
    if exact:
        chosen = exact[0]
        # 전용 프로파일에 남은 같은 폼 탭은 하나만 유지한다. 중복 탭이
        # 쌓이면 채운 결과를 어느 탭에서 저장해야 하는지 알 수 없어진다.
        for pg in exact[1:]:
            try:
                pg.close()
            except Exception:
                pass
        return chosen
    blank = [pg for pg in ctx.pages
             if (pg.url or '').startswith(('about:', 'chrome://newtab'))]
    if blank:                                  # 빈 탭이 있으면 그걸 쓴다
        chosen = blank[0]
        for pg in blank[1:]:
            try:
                pg.close()
            except Exception:
                pass
        return chosen
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
    ep, _started = ensure_browser(pms)
    p, browser = connect(ep)
    try:
        pg = pick_page(browser, base)
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
