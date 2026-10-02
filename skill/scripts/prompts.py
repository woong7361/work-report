# Prompt normalization and secret masking shared by record readers.
import re


EDIT_TOOLS = ('Edit', 'Write', 'NotebookEdit', 'MultiEdit')
PATCH_RE = re.compile(r'\*\*\*\s+(?:Add|Update|Delete) File:\s*([^\r\n]+)')
# 패치는 명령 인자 안에 JSON 문자열로 들어 있어 줄바꿈이 실제 개행이 아니라
# 두 글자(\ n)다. 그래서 경로 뒤에서 정규식이 멈추지 않고 패치 본문까지 삼킨다.
PATCH_TAIL_RE = re.compile(r'\\+[nr]')
DROP_PREFIX = ('[Request interrupted', '[Image', 'Caveat:', 'API Error')

# 사람이 프롬프트에 붙여 넣은 비밀값은 그대로 보고서의 근거가 되어 남는다.
# 이름표가 붙은 값과, 이름표가 없어도 비밀값으로만 보이는 긴 문자열을 가린다.
# 확실한 것만 가린다: 본문이 통째로 지워지면 무엇을 한 일인지 알 수 없게 된다.
SECRET_PATTERNS = [
    # key=..., password: ..., token "..." 처럼 이름이 붙은 값
    re.compile(r"""((?:[\w.-]*(?:key|secret|token|password|passwd|pwd|credential|auth))"""
               r"""\s*[=:]\s*["']?)([^\s"',;)]{8,})""", re.IGNORECASE),
    re.compile(r'(Bearer\s+)([A-Za-z0-9._\-]{20,})'),
    re.compile(r'(-----BEGIN [A-Z ]*PRIVATE KEY-----)[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----)'),
    # 이름표 없이 떠 있는 긴 무작위 문자열 (서비스 키, 액세스 토큰).
    #
    # 경로 구분자를 문자 클래스에 넣지 않는다. 넣으면 40자가 넘는 파일 경로가
    # 통째로 가려져 무엇을 한 일인지 알 수 없게 된다. 슬래시가 섞인 키는 조각이
    # 짧아져 안 걸리지만, 그런 값은 보통 이름표를 달고 있어 위에서 잡힌다.
    # 숫자를 하나 요구하는 이유도 같다. 긴 함수 이름이 가려지면 안 된다.
    re.compile(r'\b(?=[A-Za-z0-9+]*\d)([A-Za-z0-9+]{40,}={0,2})(?![A-Za-z0-9+=])'),
    # 숫자가 없는 16진수(abcdef...)는 위 패턴이 그냥 넘긴다. 여기서 받는다.
    #
    # 40자 16진수는 커밋 해시 길이이기도 해서, 프롬프트에 적어 둔 해시도 함께
    # 가려진다. 그래도 40자로 둔다. 같은 길이가 토큰의 길이이기도 하고(옛
    # GitHub 액세스 토큰이 40자 16진수다), 해시 자체는 커밋 절에 그대로 남는다.
    re.compile(r'\b([0-9a-f]{40,})\b', re.IGNORECASE),
]
SECRET_MASK = '<가림>'


def redact(text):
    """비밀값으로 보이는 부분만 가리고 나머지는 그대로 둔다."""
    if not text:
        return text
    out = SECRET_PATTERNS[0].sub(lambda m: m.group(1) + SECRET_MASK, text)
    out = SECRET_PATTERNS[1].sub(lambda m: m.group(1) + SECRET_MASK, out)
    out = SECRET_PATTERNS[2].sub(lambda m: m.group(1) + SECRET_MASK + m.group(2), out)
    for pat in SECRET_PATTERNS[3:]:
        out = pat.sub(SECRET_MASK, out)
    return out

# 도구가 세션 앞에 끼워 넣는 지시문·환경 블록은 사람이 친 지시가 아니다.
# 사용자 메시지로 기록되고 여는 꺾쇠로 시작하지도 않아서 따로 걸러야 한다.
INJECTED_MARKERS = ('<INSTRUCTIONS>', '<user_instructions>', '<environment_context>',
                    '<recommended_plugins>', '<system-reminder>')
INJECTED_HEAD_RE = re.compile(r'^#+\s+\S+\.md\s+instructions', re.IGNORECASE)


def patch_path(raw):
    path = PATCH_TAIL_RE.split(raw, 1)[0].strip().strip('"').strip()
    if not path or len(path) > 400 or path.startswith('@@'):
        return None
    # 패치 헤더를 코드 안에서 문자열 결합으로 만든 경우 경로를 복원할 수 없다
    if '"' in path or re.search(r'\+\w+\+', path):
        return None
    return path
