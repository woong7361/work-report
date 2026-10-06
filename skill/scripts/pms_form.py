# PMS form selectors, categories and validation/filling.
import io
import re

NL = chr(10)


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


def validate_report(path):
    """보고서의 제출문 절이 폼에 들어갈 수 있는 모양인지 본다.

    폼에 넣어 보기 전에 글만 보고 알 수 있는 것들이다. 틀린 채로 채우면
    울타리나 들여쓰기가 그대로 PMS에 올라가고, 그때는 사람이 지워야 한다.
    제출문 절이 없는 것은 잘못이 아니다 - 만들지 않아도 되는 절이다.
    """
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


