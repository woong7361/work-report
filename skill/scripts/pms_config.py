# PMS configuration and user-facing stop codes.
import io
import json
import os

import secrets as secret_store

HOME = os.path.expanduser('~')


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
    # 금고가 사실의 원본이다. 설정 파일에 남은 평문은 옛 버전이 남긴 흔적이므로
    # 금고가 비어 있을 때만 옮겨 심는다. 거꾸로 하면 화면에서 새로 넣은 토큰이
    # 옛 평문에 덮여 조용히 사라지고, 되돌아오지도 않는다.
    stale = (pms.get('token') or '').strip()
    if stale:
        try:
            if not secret_store.has(root, 'pms_token'):
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


NL = chr(10)

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
