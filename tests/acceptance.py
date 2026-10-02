# -*- coding: utf-8 -*-
"""겉에서 본 동작을 고정한다.

단위 테스트가 아니다. 함수 하나하나가 아니라 "이 입력을 주면 이 결과가 나온다"를
붙잡아 둔다. 안을 쪼개고 옮기는 동안 겉이 그대로인지 보는 것이 목적이라,
내부 이름이 바뀌어도 여기는 그대로여야 한다.

바깥에 나가지 않는다. PMS도 브라우저도 쓰지 않고, 쓰더라도 가짜로 세운다.
실제 보고 폴더도 건드리지 않는다 - 매번 임시 폴더를 새로 만든다.

  python tests/acceptance.py            전부
  python tests/acceptance.py collect    이름에 collect 가 들어간 것만
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'skill', 'scripts')
sys.path.insert(0, os.path.normpath(SCRIPTS))

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class Bed(object):
    """보고 폴더 하나를 세운다. 테스트마다 새로 만들고 끝나면 지운다."""

    def __init__(self, config=None):
        self.root = tempfile.mkdtemp(prefix='wr-test-')
        for d in ('daily', 'weekly', 'log', 'raw', 'runlog', 'custom'):
            os.makedirs(os.path.join(self.root, d))
        self.write('config.json', json.dumps(config or {'author': '테스터'}, ensure_ascii=False))

    def write(self, rel, text):
        path = os.path.join(self.root, rel.replace('/', os.sep))
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(text)
        return path

    def read(self, rel):
        with io.open(os.path.join(self.root, rel.replace('/', os.sep)), encoding='utf-8') as fh:
            return fh.read()

    def close(self):
        shutil.rmtree(self.root, ignore_errors=True)


def same(got, want, what):
    if got != want:
        raise AssertionError('%s\n  나온 것: %r\n  바라는 것: %r' % (what, got, want))


def truthy(got, what):
    if not got:
        raise AssertionError('%s (나온 것: %r)' % (what, got))


# ---------------------------------------------------------------- 제출문 파싱

SUBMISSION = u"""# 일일 업무 보고

- 일자: 2026-10-02 (금)

## 0. 제출문

(개발)
work-report
- 수집기 결함 수정
CDMS
- 메뉴 구조도 검토

(회의)
시스템팀 회의
- 연동 방식 논의

연결된 일감: #1396, #1406

## 1. 요약

- 이 줄은 제출문이 아니다
"""


@case
def 제출문을_분류별로_가른다():
    import viewer
    out = viewer.parse_submission(SUBMISSION)
    same([i['category'] for i in out['items']], ['개발', '회의'], '분류가 순서대로 나와야 한다')
    same(out['linked_issue_ids'], [1396, 1406], '일감 번호를 뽑아야 한다')
    truthy('CDMS' in out['items'][0]['content'], '같은 분류의 여러 주제가 한 덩어리여야 한다')
    truthy('요약' not in json.dumps(out, ensure_ascii=False), '제출문 절 밖은 들어오면 안 된다')


@case
def 제출문이_없으면_빈_결과다():
    import viewer
    out = viewer.parse_submission(u'# 보고서\n\n## 1. 요약\n- 없다\n')
    same(out['items'], [], '없으면 빈 목록이어야 한다')


@case
def 제출문의_코드_울타리는_버린다():
    import viewer
    out = viewer.parse_submission(u'## 0. 제출문\n\n```\n(개발)\n일\n- 한 줄\n```\n')
    same(len(out['items']), 1, '울타리가 덩어리를 늘리면 안 된다')
    truthy('`' not in out['items'][0]['content'], '울타리가 본문에 섞이면 안 된다')


# ---------------------------------------------------------------- 제출문 검사

@case
def 검사기가_양식_위반을_잡는다():
    import pms
    bed = Bed()
    try:
        ok = bed.write('daily/2026-10/ok.md', SUBMISSION)
        same(pms.validate_report(ok), [], '맞는 제출문은 통과해야 한다')

        bad = bed.write('daily/2026-10/bad.md',
                        u'## 0. 제출문\n\n(없는분류)\n  들여쓴 줄\n```\n\n## 1. 요약\n')
        found = ' / '.join(pms.validate_report(bad))
        truthy('분류' in found, '없는 분류를 잡아야 한다')
        truthy('공백' in found, '들여쓰기를 잡아야 한다')
        truthy('울타리' in found, '울타리를 잡아야 한다')

        none = bed.write('daily/2026-10/none.md', u'# 보고서\n\n## 1. 요약\n- 없다\n')
        same(pms.validate_report(none), [], '제출문 절이 없는 것은 잘못이 아니다')
    finally:
        bed.close()


# ---------------------------------------------------------------- 비밀값

@case
def 토큰은_평문으로_저장되지_않는다():
    import secrets as store
    bed = Bed()
    try:
        store.put(bed.root, 'pms_token', 'abcd1234')
        truthy(store.has(bed.root, 'pms_token'), '있다고 답해야 한다')
        same(store.get(bed.root, 'pms_token'), 'abcd1234', '다시 꺼내면 같아야 한다')
        truthy('abcd1234' not in bed.read('pms/secrets.dat'), '파일에 평문이 있으면 안 된다')
        store.put(bed.root, 'pms_token', '')
        same(store.has(bed.root, 'pms_token'), False, '빈 값을 주면 지워야 한다')
    finally:
        bed.close()


@case
def 설정에_남은_토큰은_금고로_옮겨진다():
    import pms, secrets as store
    bed = Bed({'author': 't', 'pms': {'url': 'https://x', 'project': 'p', 'token': 'OLD123'}})
    try:
        os.environ['WORK_REPORT_DIR'] = bed.root
        root, cfg = pms.load_cfg()
        same(cfg['token'], 'OLD123', '옮긴 뒤에도 값은 읽혀야 한다')
        truthy(store.has(bed.root, 'pms_token'), '금고에 들어가야 한다')
        left = json.loads(bed.read('config.json'))['pms'].get('token')
        same(left, '', '설정에서는 비워야 한다')
    finally:
        os.environ.pop('WORK_REPORT_DIR', None)
        bed.close()


# ---------------------------------------------------------------- 설정 저장

@case
def 뷰어가_허용된_설정만_저장한다():
    import viewer
    bed = Bed()
    try:
        out = viewer.write_config(bed.root, {
            'author': '새이름', 'pms': {'url': 'https://pms.example.com/', 'project': 'sai',
                                      'projects': ['sai', '', 'dodo'], 'port': '9333'},
            'weekly': {'end_day': 'Friday', 'span_days': 5},
            'claude_homes': ['해킹시도'],
        })
        same(out['author'], '새이름', '허용된 값은 저장돼야 한다')
        same(out['pms']['projects'], ['sai', 'dodo'], '빈 줄은 버려야 한다')
        same(out['pms']['port'], 9333, '숫자는 숫자로 저장돼야 한다')
        same(out['weekly']['span_days'], 5, '중첩 묶음도 저장돼야 한다')
        same(out.get('claude_homes'), None, '설치가 채우는 값은 받으면 안 된다')
    finally:
        bed.close()


# ---------------------------------------------------------------- 수집기

@case
def 수집기가_비밀값은_가리고_경로는_남긴다():
    import collect
    masked = collect.redact(u'api_key = AKIAIOSFODNN7EXAMPLEKEY123456')
    truthy('<가림>' in masked, '이름표 붙은 키는 가려야 한다')
    kept = u'src/components/dashboard/widgets/ChartPanel 수정'
    same(collect.redact(kept), kept, '긴 경로는 그대로 둬야 한다')
    truthy('<가림>' in collect.redact(u'토큰 sk1aB9xQ7zR2mN4pV6wL8kJ3hG5dF0sA2cE7yT1uI9oP'), '긴 토큰은 가려야 한다')


@case
def 수집기가_worktree를_저장소로_본다():
    import collect
    bed = Bed()
    try:
        wt = os.path.join(bed.root, 'wt')
        os.makedirs(os.path.join(wt, 'src'))
        with io.open(os.path.join(wt, '.git'), 'w', encoding='utf-8') as fh:
            fh.write('gitdir: /elsewhere\n')
        same(collect._repo_of(os.path.join(wt, 'src', 'a.py'), {}), wt, 'worktree 도 저장소다')
    finally:
        bed.close()


@case
def 수집기가_옛_양식_사본을_알린다():
    import collect
    bed = Bed({'author': 't', 'custom_format': True})
    try:
        bed.write('custom/report-format.md', u'# 보고서 양식\n\n## 일일 보고\n\n내용이 충분히 길어야 한다. ' * 3)
        rows = collect.scan_custom(bed.root, {'custom_format': True})
        row = [r for r in rows if r['name'] == 'report-format.md'][0]
        truthy(row['used'], '쓰이긴 해야 한다')
        truthy(row['missing'], '기본 양식에만 있는 절을 알려야 한다')
    finally:
        bed.close()


# ---------------------------------------------------------------- 러너

@case
def 러너가_설치되지_않은_에이전트를_알려준다():
    bed = Bed({'author': 't', 'agent': 'codex', 'notify': False,
               'codex_homes': [tempfile.mkdtemp(prefix='wr-home-')]})
    try:
        runner = os.path.normpath(os.path.join(SCRIPTS, 'run-report.ps1'))
        r = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', runner, '-Mode', 'daily', '-From', '2026-10-01',
                            '-Root', bed.root, '-NoToast', '-DryRun', '-Backfill', '0'],
                           capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        out = (r.stdout or '') + (r.stderr or '')
        truthy('not installed' in out, '무엇이 문제인지 말해야 한다')
        logs = []
        for dirpath, _d, names in os.walk(os.path.join(bed.root, 'runlog')):
            logs += [os.path.join(dirpath, n) for n in names]
        truthy(logs, '실행 기록이 남아야 한다')
        truthy('not installed' in io.open(logs[0], encoding='utf-8').read(), '기록에도 이유가 있어야 한다')
    finally:
        bed.close()


@case
def 러너가_같은_실행이_돌면_건너뛴다():
    bed = Bed({'author': 't', 'notify': False})
    try:
        os.makedirs(os.path.join(bed.root, 'runlog'), exist_ok=True)
        bed.write('runlog/.running', '9999 daily 2026-10-02T00:00:00')
        runner = os.path.normpath(os.path.join(SCRIPTS, 'run-report.ps1'))
        r = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                            '-File', runner, '-Mode', 'daily', '-Root', bed.root,
                            '-NoToast', '-Backfill', '0'],
                           capture_output=True, encoding='utf-8', errors='replace', timeout=120)
        same(r.returncode, 2, '건너뜀은 실패가 아니라 2다')
        truthy(os.path.isfile(os.path.join(bed.root, 'runlog', '.running')), '남의 잠금을 지우면 안 된다')
    finally:
        bed.close()


# ---------------------------------------------------------------- PMS 안내

@case
def pms가_설정이_비면_무엇을_적을지_알려준다():
    import pms
    bed = Bed()
    try:
        cfg = {'url': '', 'project': '', 'profile': os.path.join(bed.root, 'browser'), 'port': 9999}
        try:
            pms.require_config(cfg, bed.root)
            raise AssertionError('멈춰야 한다')
        except pms.Stop as stop:
            same(stop.code, pms.EXIT_CONFIG, '설정 없음은 3이다')
            truthy('config.json' in ' '.join(stop.how), '어디를 고칠지 알려야 한다')
    finally:
        bed.close()


@case
def pms가_분류_라벨과_값을_모두_받는다():
    import pms
    same(pms.category_value('개발'), 'development', '라벨을 값으로 바꿔야 한다')
    same(pms.category_value('development'), 'development', '값은 그대로여야 한다')
    same(pms.category_value('없는것'), '없는것', '모르는 것은 그대로 돌려줘야 한다')


# ---------------------------------------------------------------- 시작하기

@case
def 시작하기가_무엇이_비었는지_센다():
    import viewer
    bed = Bed({'author': 't'})
    try:
        st = viewer.setup_status(bed.root)
        same(st['reports']['daily'], 0, '보고서가 없다고 해야 한다')
        same(st['pms']['url'], '', 'PMS 주소가 비었다고 해야 한다')
        same(st['pms']['has_token'], False, '토큰이 없다고 해야 한다')
        same(st['has_submission'], False, '제출문이 없다고 해야 한다')

        bed.write('daily/2026-10/2026-10-02.md', SUBMISSION)
        st = viewer.setup_status(bed.root)
        same(st['reports']['daily'], 1, '보고서를 세야 한다')
        truthy(st['has_submission'], '제출문을 알아봐야 한다')
    finally:
        bed.close()


# ---------------------------------------------------------------- 달리기

def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    picked = [c for c in CASES if only in c.__name__]
    bad = 0
    for c in picked:
        name = c.__name__.replace('_', ' ')
        try:
            c()
            print('  OK   %s' % name)
        except Exception as e:
            bad += 1
            print('  실패 %s' % name)
            for line in str(e).splitlines():
                print('       %s' % line)
    print('')
    print('%d개 중 %d개 통과%s' % (len(picked), len(picked) - bad, '' if not bad else ', %d개 실패' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
