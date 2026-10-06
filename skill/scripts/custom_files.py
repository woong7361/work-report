# User-owned report format and style sample discovery.
import os
import re


# ---------------------------------------------------------------- 사용자 양식

# 보고 폴더의 custom\ 에 양식과 문체를 두면 그것을 쓴다. 업데이트가 skill 폴더를
# 통째로 덮어써도 보고 폴더는 건드리지 않으므로 고쳐 둔 것이 살아남는다.
#
# 에이전트가 파일을 읽고 안 읽고에 맡기지 않고, 수집기가 먼저 확인해서 상태를
# 수집 결과에 적는다. 무시한 경우에는 왜 무시했는지도 같이 적는다.
# 조용히 안 먹는 것이 제일 나쁘다.

CUSTOM_FILES = (
    ('report-format.md', '보고서 양식', 'custom_format'),
    ('writing-rules.md', '글쓰기 문체', 'custom_rules'),
)
CUSTOM_MAX = 8 * 1024       # 이보다 크면 양식이 아니라 다른 글이다
CUSTOM_MIN = 40             # 제목만 남기고 지운 파일을 양식으로 쓰면 보고서가 빈다

def read_text(path, cap=CUSTOM_MAX):
    """사용자가 메모장으로 저장해도 읽히게 한다. 실패하면 이유를 준다."""
    try:
        raw = open(path, 'rb').read()
    except OSError as e:
        return None, '읽지 못했다 (%s)' % e.__class__.__name__
    if len(raw) > cap:
        return None, '너무 크다 (%.1fKB, 최대 %dKB)' % (len(raw) / 1024.0, cap // 1024)
    for enc in ('utf-8-sig', 'cp949'):
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        body = ''.join(text.split())
        if not body:
            return None, '비어 있다'
        if len(body) < CUSTOM_MIN:
            # 기본값을 복사한 뒤 지우다 만 경우. 대체 파일이 비면 보고서가 빈다
            return None, '내용이 거의 없다 (글자 %d개, 최소 %d개)' % (len(body), CUSTOM_MIN)
        return text, None
    return None, '글자 인코딩을 알 수 없다 (UTF-8로 저장해 보라)'


def headings(text):
    """문서의 절 제목만 뽑는다."""
    return [m.group(2).strip() for m in re.finditer(r'(?m)^(#{1,3})\s+(.+)$', text or '')]


def stale_sections(name, text):
    """기본 양식에는 있는데 내 사본에는 없는 절.

    토글을 켜면 그 사본은 그 시점에 멈춘다. 나중에 기본 양식에 절이 생겨도
    따라오지 않고, 그 절이 필요하다는 사실조차 알 수 없다. 그래서 센다.
    """
    template = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'templates', name)
    try:
        with open(os.path.normpath(template), encoding='utf-8') as fh:
            base = fh.read()
    except OSError:
        return []
    mine = set(headings(text))
    return [h for h in headings(base) if h not in mine]


def scan_custom(root, cfg):
    """어느 양식이 쓰이는지 기록해 둔다.

    고르는 것은 설정의 토글이고, 실제 경로는 실행할 때 러너가 넘긴다.
    여기서는 그날 무엇이 쓰였는지 보이게만 한다 - 보고서 모양이 달라진
    이유를 나중에 이 줄에서 찾을 수 있어야 한다.
    """
    out = []
    for name, label, flag in CUSTOM_FILES:
        if not cfg.get(flag):
            out.append({'name': name, 'label': label, 'mine': False,
                        'used': False, 'why': None})
            continue
        path = os.path.join(root, 'custom', name)
        if not os.path.isfile(path):
            out.append({'name': name, 'label': label, 'mine': True, 'used': False,
                        'why': '파일이 없다 (다음 실행이 기본값으로 다시 만든다)'})
            continue
        text, why = read_text(path)
        out.append({'name': name, 'label': label, 'mine': True,
                    'used': text is not None, 'why': why,
                    'path': os.path.join('custom', name),
                    'missing': stale_sections(name, text) if text else [],
                    'size': len(text.encode('utf-8')) if text else 0})
    return out
