# Submission section parsing shared by the viewer and PMS integration.
import re
import time


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
