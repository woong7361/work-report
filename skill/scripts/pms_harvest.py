# PMS history collection and writing-style statistics.
import io
import json
import os

from pms_browser import connect, ensure_browser, logged_in, need_login, pick_page


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

    제출문 절과 같은 모양으로 적는다 - 예시가 양식과 다른 모양이면 읽는 쪽이
    어느 쪽을 따라야 할지 알 수 없다. 그래서 날짜는 주석으로 빼고(제출문에는
    날짜 줄이 없다), 연결된 일감은 양식이 정한 자리인 맨 끝에 둔다.

    폼에 들어 있던 본문은 그대로 둔다. 보기 좋으라고 들여쓰기를 넣으면 예시를
    읽은 쪽이 그 들여쓰기까지 따라 쓴다 - 예시는 꾸미는 것이 아니라 베끼는 것이다.
    """
    lines = ['<!-- %s -->' % report['date']]
    for sec in report['secs']:
        for g in sec['groups']:
            lines.append('(%s)' % g['badge'] if g['badge'] else '(분류 없음)')
            for line in g['text'].splitlines():
                if line.strip():
                    lines.append(line.strip())
    ids = [str(x) for x in (report.get('issues') or [])]
    if ids:
        lines.append('연결된 일감: ' + ', '.join('#' + x for x in ids))
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
    # 보고서 사이는 --- 로 나눈다. 안내문이 사람에게 시키는 규칙과 같은 모양이어야
    # 손으로 넣은 것과 받아온 것이 한 파일에서 같게 읽힌다.
    joined = (NL + '---' + NL).join(as_text(r) for r in reports[:keep])
    block = NL.join([MARK_BEGIN,
                        '<!-- PMS에서 받아온 내 지난 제출문이다. 손으로 고치지 마라 - 다음 수집이 덮는다. -->',
                        '', joined, MARK_END, ''])
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
    for r in reports:
        groups = [g for sec in r['secs'] if '한 일' in sec['head'] for g in sec['groups']]
        rows[len(groups)] = rows.get(len(groups), 0) + 1
        for g in groups:
            key = g['badge'] or '(분류 없음)'
            cats[key] = cats.get(key, 0) + 1
        ids = r.get('issues') or []
        if ids:
            with_issues += 1
            issue_count += len(ids)
    # 본문은 세지 않는다. 문체 예시는 custom/my-reports.md 가 전담한다 -
    # 그쪽에만 토글과 길이 검증이 걸려 있어서, 여기 본문을 함께 담으면
    # "지난 제출문 따라하기"를 꺼도 지난 보고서 글이 에이전트에게 간다.
    return {'reports': len(reports), 'categories': cats, 'rows_per_report': rows,
            'reports_with_issues': with_issues, 'issue_links': issue_count}


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
            print('문체 예시   : custom/my-reports.md 가 없다. 설정에서 "지난 제출문 따라하기"를 켜면 생긴다')
        return 0
    finally:
        browser.close()
        p.stop()
