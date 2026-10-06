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
import sys

from _log import log_error

from pms_config import (EXIT_NOTHING, EXIT_OK, EXIT_PLAYWRIGHT, HOME, Stop,
                        load_cfg, require_config)
from pms_browser import (connect, do_login, ensure_browser, form_url, logged_in,
                         need_login, open_form, pick_page, port_endpoint, window_state)
from pms_form import (CATEGORIES, EXTRA_FIELDS, SEL_ADD_ROW, SEL_DATE, fill_form,
                      validate_report)
from pms_harvest import do_fetch
from pms_issues import do_issues

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
        print('  앱 설정에서 "지난 제출문 따라하기"를 켜고 pms.ps1 -Fetch 를 한 번 돌리면 생긴다.')
        return EXIT_NOTHING

    # 시작 명령에 URL을 넘기면 크롬이 탭을 하나 만든 뒤 Playwright가 같은
    # 폼으로 다시 이동하면서 두 번째 탭이 생길 수 있다. 빈 탭을 재사용한다.
    ep, _started = ensure_browser(pms)
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
        window_state(pg, 'normal')        # 사람이 확인하고 저장할 수 있게 둔다
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
    ep, _ = ensure_browser(pms)
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
