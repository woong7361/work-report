# -*- coding: utf-8 -*-
"""AI 대화 기록과 git 이력에서 보고서 재료를 뽑는다.

대화 본문은 읽지 않는다. 세션당 골격만 남긴다:
제목, 사용자가 친 지시, 편집된 파일 경로, 저장소, 시각.

실행 예:
    python collect.py --from 2026-09-21 --md out.md --json out.json
    python collect.py --check          # 기록 위치와 수집 가능 여부만 점검
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone

from _log import log_error

# ---------------------------------------------------------------- 설정

# 수집기가 실제로 읽는 값만 둔다. 보고서 작성에만 쓰이는 값(author 등)은
# config.json에 있고 여기서는 보지 않는다.
DEFAULTS = {
    # 작업 저장소로 치지 않는 경로 조각
    'exclude_paths': ['node_modules', '\\scratchpad', '/scratchpad', '\\Temp\\', '/Temp/'],
    # 보고에서 통째로 빼는 저장소 (경로 조각)
    'exclude_repos': [],
    # git 커밋을 저장소에 설정된 내 이메일로만 거를지
    'mine_only': True,
    # 비우면 실행 환경의 지역 시간대를 쓴다. 숫자를 넣으면 UTC 기준 고정 오프셋(시)
    'utc_offset_hours': None,
    'max_prompt_chars': 600,
    'max_prompts_per_session': 40,
    # 프롬프트에 섞여 들어온 키·토큰을 가린다
    'redact': True,
}

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
HOME = os.path.expanduser('~')


def patch_path(raw):
    path = PATCH_TAIL_RE.split(raw, 1)[0].strip().strip('"').strip()
    if not path or len(path) > 400 or path.startswith('@@'):
        return None
    # 패치 헤더를 코드 안에서 문자열 결합으로 만든 경우 경로를 복원할 수 없다
    if '"' in path or re.search(r'\+\w+\+', path):
        return None
    return path


def merge_replays(sessions):
    """같은 대화를 다시 태운 세션을 하나로 본다.

    파이프라인이나 스킬이 기존 대화를 그대로 재생하며 세션을 띄우면 같은 지시가
    여러 벌로 남는다. 지시가 한 시각에 몰려 있어 사람이 친 것과 구별된다.
    재생본의 편집 파일은 실제 산출물이므로 원본 세션으로 옮긴다.
    """
    kept = []
    for s in sessions:
        first = s.prompts[0][1] if s.prompts else None
        host = None
        if first:
            for k in kept:
                if k.tool != s.tool or k.cwd != s.cwd:
                    continue
                if not k.prompts or k.prompts[0][1] != first:
                    continue
                texts = set(t for _, t in k.prompts)
                if all(t in texts for _, t in s.prompts):
                    host = k
                    break
        if host is None:
            kept.append(s)
            continue
        host.files.update(s.files)
        host.tools.update(s.tools)
        host.agents.extend(s.agents)
        host.times.extend(s.times)
        host.replays += 1
    return kept


def load_config(path=None):
    cfg = dict(DEFAULTS)
    for candidate in [path,
                      os.path.join(os.environ.get('WORK_REPORT_DIR', ''), 'config.json'),
                      os.path.join(HOME, 'work-report', 'config.json')]:
        if candidate and os.path.isfile(candidate):
            try:
                # utf-8-sig: 편집기와 PowerShell이 붙이는 BOM을 그냥 넘긴다
                with open(candidate, encoding='utf-8-sig') as fh:
                    cfg.update({k: v for k, v in json.load(fh).items() if v is not None})
            except (ValueError, OSError) as e:
                sys.stderr.write('설정 파일을 읽지 못했다 (%s): %s\n' % (candidate, e))
            cfg['_config_path'] = candidate
            break
    return cfg


def _roots(env_name, default_dir, leaf, extra):
    """기록 위치는 하나가 아니다. 환경변수로 옮겨 둔 곳과 기본 위치가 함께 있을 수 있고,
    예약 실행에는 그 환경변수가 없다. 있는 곳을 모두 훑는다."""
    out = []
    for base in [os.environ.get(env_name), os.path.join(HOME, default_dir)] + list(extra or ()):
        if not base:
            continue
        path = base if os.path.basename(base) == leaf else os.path.join(base, leaf)
        path = os.path.normpath(path)
        if os.path.isdir(path) and path not in out:
            out.append(path)
    return out


def claude_dirs(cfg=None):
    return _roots('CLAUDE_CONFIG_DIR', '.claude', 'projects',
                  (cfg or {}).get('claude_dirs'))


def codex_dirs(cfg=None):
    return _roots('CODEX_HOME', '.codex', 'sessions',
                  (cfg or {}).get('codex_dirs'))


# ---------------------------------------------------------------- 공통

class Ctx(object):
    """수집 한 번에 쓰이는 설정 묶음."""

    def __init__(self, cfg):
        self.claude_dirs = claude_dirs(cfg)
        self.codex_dirs = codex_dirs(cfg)
        off = cfg.get('utc_offset_hours')
        self.tz = timezone(timedelta(hours=off)) if off is not None else None  # None = 지역 시간
        self.exclude = tuple(cfg.get('exclude_paths') or ())
        self.exclude_repos = tuple(cfg.get('exclude_repos') or ())
        self.max_prompt = int(cfg.get('max_prompt_chars') or 600)
        self.max_prompts = int(cfg.get('max_prompts_per_session') or 40)
        self.redact = cfg.get('redact') is not False
        self.mine_only = bool(cfg.get('mine_only'))
        # 도구 자신의 폴더는 작업이 아니다. 설치가 채워 둔 값을 그대로 쓴다
        self.skip_paths = []
        for key in ('claude_homes', 'codex_homes', 'claude_dirs', 'codex_dirs', 'skill_dirs'):
            self.skip_paths.extend(cfg.get(key) or [])
        # 보고 폴더는 설정 파일이 있는 곳이다. 환경변수로 짐작하면 폴더를 옮겨
        # 쓰는 사람의 보고서가 다음 날 '바뀐 파일'로 되잡힌다.
        root = os.path.dirname(cfg.get('_config_path') or '')
        self.cfg = cfg
        self.root = (root or os.environ.get('WORK_REPORT_DIR')
                     or os.path.join(HOME, 'work-report'))
        self.skip_paths.append(self.root)

    def local(self, ts):
        """UTC 타임스탬프를 지역 날짜/시각으로. 기록은 UTC로 저장되므로
                               or os.path.join(HOME, 'work-report'))
        변환하지 않으면 자정 근처 작업이 다른 날짜로 새어 나간다."""
        if not ts:
            return None, None
        try:
            dt = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        except ValueError:
            return None, None
        dt = dt.astimezone(self.tz) if self.tz else dt.astimezone()
        return dt.strftime('%Y-%m-%d'), dt.strftime('%H:%M')

    def now(self):
        dt = datetime.now(self.tz) if self.tz else datetime.now().astimezone()
        return dt.strftime('%Y-%m-%d %H:%M %Z').strip()

    def excluded(self, path):
        return not path or any(x in path for x in self.exclude)

    def excluded_repo(self, path):
        return bool(path) and any(x in path for x in self.exclude_repos)

    def clean_prompt(self, text):
        """주입 블록과 잡음을 버리고 남으면 반환."""
        t = (text or '').strip()
        if not t or t.startswith('<'):
            return None
        if t.startswith(DROP_PREFIX):
            return None
        if INJECTED_HEAD_RE.match(t) or any(m in t[:400] for m in INJECTED_MARKERS):
            return None
        # 가리는 것이 먼저다. 길이로 자른 뒤에 가리면 잘린 조각이 그대로 남는다.
        if self.redact:
            t = redact(t)
        if len(t) > self.max_prompt:
            t = t[:self.max_prompt] + ' …'
        return t


class Session(object):
    def __init__(self, ctx, tool, sid):
        self.ctx = ctx
        self.tool = tool
        self.id = sid
        self.title = None
        self.cwd = None
        self.branch = None
        self.prompts = []          # [(시각, 본문)]
        self.files = Counter()
        self.tools = Counter()
        self.agents = []
        self.times = []
        self.replays = 0

    def add_prompt(self, clock, text):
        t = self.ctx.clean_prompt(text)
        if not t:
            return
        for _, prev in self.prompts[-2:]:       # 재전송·편집으로 생기는 중복 제거
            if t == prev or t in prev or prev in t:
                return
        self.prompts.append((clock, t))

    def as_dict(self):
        shown = self.prompts[:self.ctx.max_prompts]
        # 제목이 없는 기록(Codex)은 첫 지시의 첫 줄로 대신한다. 통째로 자르면
        # 여러 줄짜리 지시에서 제목이 문장 중간에 끊긴다.
        title = self.title
        if not title:
            title = shown[0][1].strip().splitlines()[0][:60] if shown else '(제목 없음)'
        return OrderedDict([
            ('tool', self.tool),
            ('id', str(self.id)[:8]),
            ('title', title),
            ('cwd', self.cwd),
            ('branch', self.branch),
            ('start', min(self.times) if self.times else None),
            ('end', max(self.times) if self.times else None),
            ('prompts', [{'at': a, 'text': b} for a, b in shown]),
            ('prompt_total', len(self.prompts)),
            ('files', [{'path': p, 'edits': n} for p, n in self.files.most_common(25)]),
            ('tools', dict(self.tools.most_common(8))),
            ('agents', self.agents[:10]),
            ('replays', self.replays),
        ])


# ---------------------------------------------------------------- Claude Code

def scan_claude(ctx, dfrom, dto):
    """~/.claude/projects/<경로>/<세션>.jsonl"""
    sessions = []
    auto = Counter()
    try:
        floor = datetime.strptime(dfrom, '%Y-%m-%d') - timedelta(days=1)
    except ValueError:
        raise SystemExit('날짜 형식은 YYYY-MM-DD다: %s' % dfrom)
    paths = []
    for root in ctx.claude_dirs:
        paths += glob.glob(os.path.join(root, '*', '*.jsonl'))
    for path in paths:
        try:
            if datetime.fromtimestamp(os.path.getmtime(path)) < floor:
                continue
        except OSError:
            continue
        s = Session(ctx, 'claude', os.path.basename(path)[:-6])
        in_range = False
        try:
            fh = open(path, encoding='utf-8', errors='replace')
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                kind = d.get('type')
                if kind in ('user', 'assistant') and d.get('entrypoint') not in (None, 'cli'):
                    # 스크립트가 돌린 헤드리스 실행. 사람이 친 프롬프트가 아니다
                    day, _ = ctx.local(d.get('timestamp'))
                    if day and dfrom <= day <= dto:
                        auto['claude ' + (d.get('cwd') or '?')] += 1
                    in_range = False
                    break
                if kind == 'ai-title' and d.get('aiTitle'):
                    s.title = d['aiTitle']          # Claude Code가 붙여 둔 세션 제목
                    continue
                if kind == 'summary' and d.get('summary') and not s.title:
                    s.title = d['summary']
                    continue
                if kind not in ('user', 'assistant'):
                    continue
                day, clock = ctx.local(d.get('timestamp'))
                if day is None or not (dfrom <= day <= dto):
                    continue
                in_range = True
                s.times.append(clock)
                if d.get('cwd') and not s.cwd:
                    s.cwd = d.get('cwd')
                    s.branch = d.get('gitBranch')
                content = (d.get('message') or {}).get('content')
                if kind == 'user':
                    # 서브에이전트 내부 대화와 메타 항목은 사람의 지시가 아니다
                    if d.get('isMeta') or d.get('isSidechain'):
                        continue
                    if isinstance(content, str):
                        s.add_prompt(clock, content)
                    else:
                        for b in content or []:
                            if isinstance(b, dict) and b.get('type') == 'text':
                                s.add_prompt(clock, b.get('text'))
                    continue
                # assistant: 도구 호출 메타데이터만.
                # 서브에이전트가 고친 파일도 실제 작업이므로 여기서는 거르지 않는다.
                for b in content or []:
                    if not isinstance(b, dict) or b.get('type') != 'tool_use':
                        continue
                    name = b.get('name')
                    s.tools[name] += 1
                    inp = b.get('input') or {}
                    if name in EDIT_TOOLS:
                        p = inp.get('file_path') or inp.get('notebook_path')
                        if p and not ctx.excluded(p):
                            # 도구마다 구분자가 달라 같은 파일이 둘로 세어진다
                            s.files[os.path.normpath(p)] += 1
                    elif name in ('Agent', 'Task') and inp.get('description'):
                        s.agents.append(inp['description'])
        if in_range and (s.prompts or s.files):
            sessions.append(s)
    return sessions, auto


# ---------------------------------------------------------------- Codex

def scan_codex(ctx, dfrom, dto):
    """~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl"""
    sessions = []
    auto = Counter()
    if not ctx.codex_dirs:
        return sessions, auto
    paths = []
    for root in ctx.codex_dirs:
        day = datetime.strptime(dfrom, '%Y-%m-%d') - timedelta(days=1)
        end = datetime.strptime(dto, '%Y-%m-%d') + timedelta(days=1)
        while day <= end:
            paths += glob.glob(os.path.join(root, day.strftime('%Y'), day.strftime('%m'),
                                            day.strftime('%d'), '*.jsonl'))
            day += timedelta(days=1)
    for path in paths:
        try:
            fh = open(path, encoding='utf-8', errors='replace')
        except OSError:
            continue
        with fh:
            try:
                meta = json.loads(fh.readline()).get('payload', {})
            except ValueError:
                continue
            started, _ = ctx.local(meta.get('timestamp'))
            if meta.get('originator') == 'codex_exec':
                # 훅·파이프라인이 돌린 자동 실행. 세기만 하고 본문은 읽지 않는다
                if started and dfrom <= started <= dto:
                    auto['codex ' + (meta.get('cwd') or '?')] += 1
                continue
            s = Session(ctx, 'codex', meta.get('session_id') or os.path.basename(path))
            s.cwd = meta.get('cwd')
            in_range = False
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('type') != 'response_item':
                    continue
                day_s, clock = ctx.local(d.get('timestamp'))
                if day_s is None or not (dfrom <= day_s <= dto):
                    continue
                in_range = True
                s.times.append(clock)
                p = d.get('payload') or {}
                ptype = p.get('type')
                if ptype == 'message' and p.get('role') == 'user':
                    text = ''.join(c.get('text', '') for c in p.get('content') or []
                                   if isinstance(c, dict))
                    s.add_prompt(clock, text)
                elif ptype in ('function_call', 'custom_tool_call', 'local_shell_call'):
                    s.tools[p.get('name') or ptype] += 1
                    args = p.get('arguments') or p.get('input') or ''
                    # Codex는 파일 편집을 exec 안에서 하므로 패치 헤더로만 경로를 건진다
                    if isinstance(args, str) and '*** ' in args:
                        for m in PATCH_RE.finditer(args):
                            f = patch_path(m.group(1))
                            if f and not ctx.excluded(f):
                                s.files[os.path.normpath(f)] += 1
            if in_range and (s.prompts or s.files):
                sessions.append(s)
    return sessions, auto


# ---------------------------------------------------------------- git

def git_run(repo, args):
    # 기본 인코딩(cp949 등)으로 디코드하면 한글 커밋 메시지에서 죽는다
    try:
        r = subprocess.run(['git', '-c', 'core.quotepath=false', '-C', repo] + args,
                           capture_output=True, encoding='utf-8', errors='replace',
                           timeout=60)
    except (OSError, subprocess.SubprocessError):
        return ''
    return r.stdout or ''


def git_toplevel(path):
    out = git_run(path, ['rev-parse', '--show-toplevel']).strip()
    return out or None


def scan_git(ctx, repos, dfrom, dto):
    out = []
    for repo in sorted(repos):
        email = git_run(repo, ['config', 'user.email']).strip()
        args = ['log', '--all', '--since', dfrom + ' 00:00', '--until', dto + ' 23:59',
                '--date=format:%Y-%m-%d %H:%M', '--numstat',
                '--pretty=format:@@|%h|%ad|%an|%s']
        if ctx.mine_only and email:
            args.append('--author=' + email)
        commits = []
        cur = None
        for line in git_run(repo, args).splitlines():
            if line.startswith('@@|'):
                _, h, at, an, subject = line.split('|', 4)
                cur = OrderedDict([('hash', h), ('at', at), ('author', an),
                                   ('subject', subject), ('files', 0), ('added', 0),
                                   ('deleted', 0), ('paths', [])])
                commits.append(cur)
            elif line.strip() and cur is not None:
                parts = line.split('\t')
                if len(parts) == 3:
                    cur['files'] += 1
                    if parts[0].isdigit():
                        cur['added'] += int(parts[0])
                    if parts[1].isdigit():
                        cur['deleted'] += int(parts[1])
                    if len(cur['paths']) < 12:
                        cur['paths'].append(parts[2])
        dirty = [l for l in git_run(repo, ['status', '--porcelain']).splitlines() if l.strip()]
        branch = git_run(repo, ['rev-parse', '--abbrev-ref', 'HEAD']).strip()
        if commits or dirty:
            out.append(OrderedDict([('repo', repo), ('branch', branch), ('email', email),
                                    ('commits', commits), ('dirty', dirty[:40]),
                                    ('dirty_total', len(dirty))]))
    return out


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
    ('my-reports.md', '내 보고서', 'custom_samples'),
)
CUSTOM_MAX = 8 * 1024       # 이보다 크면 양식이 아니라 다른 글이다
CUSTOM_MIN = 40             # 제목만 남기고 지운 파일을 양식으로 쓰면 보고서가 빈다

# 지난 보고서를 쌓아 두는 파일은 규칙이 아니라 예시 더미라 훨씬 커진다.
SAMPLE_FILE = 'my-reports.md'
SAMPLE_MAX = 64 * 1024
# 표시선 아래가 붙여넣는 자리다. 안내문만 있고 그 아래가 비었으면 아직 넣지
# 않은 것이다. 이때 안내문을 예시로 삼으면 안내문의 문체를 배우게 된다.
SAMPLE_MARK = '<!-- PASTE BELOW -->'
# 여기서 쓰이지 않는 예시는 러너도 넘기지 않아야 한다. 한쪽만 통과하면 수집
# 결과에는 쓰였다고 적히는데 보고서엔 그 절이 없는, 설명할 수 없는 상태가 된다.
# 러너 쪽 기준은 _env.ps1 의 Get-SampleFile 에 있다.
SAMPLE_MIN = CUSTOM_MIN


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


def sample_body(text):
    """표시선 아래에 붙여넣은 부분만 돌려준다.

    표시선을 지우고 보고서만 남긴 파일도 받는다. 그때는 전체가 붙여넣은 것이다.
    """
    if SAMPLE_MARK not in text:
        return text.strip()
    return text.rsplit(SAMPLE_MARK, 1)[1].strip()


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
        text, why = read_text(path, SAMPLE_MAX if name == SAMPLE_FILE else CUSTOM_MAX)
        if text is not None and name == SAMPLE_FILE:
            # 파일 전체는 안내문만으로도 분량을 넘기므로 표시선 아래만 센다.
            pasted = ''.join(sample_body(text).split())
            if not pasted:
                text, why = None, '붙여넣은 보고서가 없다 (안내문만 있다)'
            elif len(pasted) < SAMPLE_MIN:
                text, why = None, ('붙여넣은 보고서가 너무 짧다 (글자 %d개, 최소 %d개)'
                                   % (len(pasted), SAMPLE_MIN))
        out.append({'name': name, 'label': label, 'mine': True,
                    'used': text is not None, 'why': why,
                    'path': os.path.join('custom', name),
                    'size': len(text.encode('utf-8')) if text else 0})
    return out


# ---------------------------------------------------------------- 파일 변경

# 홈 폴더를 훑어 그 구간에 바뀐 파일을 찾는다. AI CLI를 쓰지 않는 일(문서, 기획,
# 디자인)은 대화에도 커밋에도 남지 않으므로, 그런 날의 유일한 근거가 된다.
#
# 제외 목록을 설정이 아니라 여기 두는 이유: 나중에 빠진 것을 발견해도 업데이트로
# 고쳐 줄 수 있어야 하기 때문이다. 설정에 넣으면 설치 시점의 목록에 묶인다.
# 사용자의 exclude_paths는 이 목록을 덮지 않고 더해진다.

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
NOT_LOCAL = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
             | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)

# 작업 산출물이 아닌 폴더. 점으로 시작하는 폴더는 이름과 무관하게 모두 뺀다
# (.git, .venv, .claude, .codex, .vscode … 전부 도구의 것이다)
SKIP_DIRS = frozenset([
    'appdata', 'application data', 'local settings', 'node_modules', 'bower_components',
    'dist', 'build', 'out', 'target', 'obj', 'bin', 'venv', 'env', 'vendor',
    '__pycache__', 'coverage', 'logs', 'temp', 'tmp', 'cache',
    'nuget', 'packages', 'site-packages', 'recent', 'sendto', 'nethood', 'printhood',
    'templates', 'searches', 'links', 'saved games', 'contacts', 'favorites',
    '내 문서', '내 그림', '내 음악', '내 비디오',
])
SKIP_EXT = frozenset([
    '.pyc', '.pyo', '.pyd', '.log', '.tmp', '.temp', '.bak', '.old', '.swp', '.swo',
    '.lock', '.pid', '.map', '.dll', '.exe', '.pdb', '.obj', '.o', '.a', '.lib',
    '.class', '.jar', '.war', '.db-wal', '.db-shm', '.sqlite-journal', '.vscdb',
    '.crdownload', '.part', '.partial', '.ini', '.dat', '.etl', '.cab', '.msi',
])
SKIP_NAMES = frozenset([
    'thumbs.db', 'desktop.ini', '.ds_store', 'ntuser.dat', 'ntuser.ini',
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'cargo.lock',
    'composer.lock', 'gemfile.lock',
])
# OneDrive는 읽지 않는다. 동기화가 남의 수정을 내 파일에 써 넣고, 아직
# 내려받지 않은 파일은 건드리는 것만으로 다운로드를 시작할 수 있다.
SKIP_PREFIX = ('onedrive', 'dropbox', 'google drive', 'nextcloud', '$')

MAX_FILES = 400            # 이보다 많으면 훑다 만 것이다. 근거로 못 쓴다


def _attrs(st):
    return getattr(st, 'st_file_attributes', 0)


def known_folders():
    """바탕화면·문서·다운로드가 지금 실제로 어디인지 Windows에 물어본다.

    폴더 이동(Known Folder Move)을 켜면 이것들이 OneDrive 밑으로 간다.
    홈만 훑으면 그 사람의 작업이 통째로 빠지므로, 위치를 확인해 알려 준다.
    """
    out, moved = [], []
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders')
        names = [('Desktop', '바탕 화면'), ('Personal', '문서'),
                 ('{374DE290-123F-4565-9164-39C4925E467B}', '다운로드')]
        with key:
            for reg_name, label in names:
                try:
                    raw = winreg.QueryValueEx(key, reg_name)[0]
                except OSError:
                    continue
                path = os.path.normpath(os.path.expandvars(raw))
                if not os.path.isdir(path):
                    continue
                base = os.path.basename(path).lower()
                parts = [p.lower() for p in path.split(os.sep)]
                if any(p.startswith(SKIP_PREFIX[:4]) for p in parts):
                    moved.append('%s -> %s' % (label, path))
                    continue
                out.append(path)
    except (ImportError, OSError):
        pass
    return out, moved


def _walk(base, ctx, skip_paths, since, until, hits, budget):
    stack = [base]
    while stack and len(hits) < budget:
        cur = stack.pop()
        try:
            entries = list(os.scandir(cur))
        except OSError:
            continue
        for e in entries:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            attrs = _attrs(st)
            if e.is_dir(follow_symlinks=False):
                name = e.name.lower()
                # 연결 폴더를 따라가면 같은 곳을 두 번 센다. Windows 홈의
                # "Application Data"와 "Local Settings"가 그런 연결이다.
                if attrs & FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                if name.startswith('.') or name.startswith(SKIP_PREFIX):
                    continue
                if name in SKIP_DIRS:
                    continue
                if os.path.normcase(e.path) in skip_paths or ctx.excluded(e.path):
                    continue
                stack.append(e.path)
                continue
            if not e.is_file(follow_symlinks=False):
                continue
            name = e.name.lower()
            if name.startswith('.') or name.startswith('~$') or name in SKIP_NAMES:
                continue
            if os.path.splitext(name)[1] in SKIP_EXT:
                continue
            if attrs & NOT_LOCAL:          # 아직 내려받지 않은 파일. 건드리지 않는다
                continue
            mod = since <= st.st_mtime <= until
            made = since <= st.st_ctime <= until
            if not mod and not made:
                continue
            # 복사해 온 파일은 만든 시각만 지금이고 내용은 그대로다
            if made and not mod:
                continue
            # 받은 뒤 손대지 않은 파일. 읽기만 한 자료지 내가 한 일이 아니다.
            # 오늘 받은 파일은 만든 시각도 오늘이므로 만든 시각으로 걸러서는 안 된다.
            if os.path.exists(e.path + ':Zone.Identifier') \
                    and st.st_mtime <= st.st_ctime + 2:
                continue
            hits.append(OrderedDict([
                ('path', e.path),
                ('at', datetime.fromtimestamp(st.st_mtime).strftime('%H:%M')),
                ('size', st.st_size),
            ]))
            if len(hits) >= budget:     # 한 폴더가 통째로 바뀐 경우
                return


def _repo_of(path, cache):
    """파일이 속한 git 저장소. 한 번 올라간 경로는 기억해 둔다."""
    d = os.path.dirname(path)
    seen = []
    while True:
        key = os.path.normcase(d)
        if key in cache:
            found = cache[key]
            break
        seen.append(key)
        # worktree 와 서브모듈에서는 .git 이 폴더가 아니라 파일이다. 폴더만
        # 보면 저장소가 아닌 것으로 처리되어, 이미 커밋된 변경이 커밋 절과
        # 파일 변경 절에 두 번 올라온다.
        if os.path.exists(os.path.join(d, '.git')):
            found = d
            break
        parent = os.path.dirname(d)
        if parent == d:
            found = None
            break
        d = parent
    for key in seen:
        cache[key] = found
    return found


def _uncommitted(repo, cache):
    """저장소에서 아직 커밋되지 않은 경로. 커밋된 변경은 커밋 절이 이미 말한다.

    git이 없거나 답하지 못하면 None을 준다. 빈 목록으로 돌려주면 저장소 안의
    파일이 전부 "이미 커밋됨"으로 처리되어 통째로 사라진다.
    """
    key = os.path.normcase(repo)
    if key in cache:
        return cache[key]
    if not git_run(repo, ['rev-parse', '--is-inside-work-tree']).strip():
        cache[key] = None
        return None
    out = set()
    # -uall이 없으면 추적되지 않는 폴더가 "dir/" 한 줄로만 나오고 그 안의 파일은
    # 목록에 없다. 새로 만든 기획 폴더가 통째로 빠진다.
    for line in git_run(repo, ['status', '--porcelain', '-uall']).splitlines():
        rel = line[3:].strip()
        if not rel:
            continue
        if ' -> ' in rel:                       # 이름이 바뀐 경우 새 이름만 본다
            rel = rel.split(' -> ')[-1]
        rel = rel.strip('"')
        out.add(os.path.normcase(os.path.normpath(os.path.join(repo, rel))))
    cache[key] = out
    return out


def scan_files(ctx, dfrom, dto):
    """오늘 내가 고치거나 새로 만든 파일.

    오늘 하루만 본다. 파일에는 마지막 수정 시각 하나만 남아서, 지난 날은 되짚을 수
    없고 여러 날을 한 번에 보면 어느 날 일인지 가릴 수 없다. 사흘 전과 오늘 두 번
    고친 파일은 오늘 것으로만 남는다.
    """
    today = datetime.now().strftime('%Y-%m-%d')
    if dfrom != today or dto != today:
        return {'skipped': '파일 수정 시각으로는 오늘 것만 가릴 수 있어 건너뛴다',
                'folders': [], 'total': 0, 'moved': []}

    since = datetime.strptime(dfrom + ' 00:00', '%Y-%m-%d %H:%M').timestamp()
    until = datetime.strptime(dto + ' 23:59', '%Y-%m-%d %H:%M').timestamp()

    skip_paths = set()
    for p in ctx.skip_paths:
        skip_paths.add(os.path.normcase(os.path.normpath(p)))

    roots, moved = known_folders()
    bases = [HOME] + [p for p in roots
                      if not os.path.normcase(p).startswith(os.path.normcase(HOME) + os.sep)]

    hits = []
    for base in bases:
        _walk(base, ctx, skip_paths, since, until, hits, MAX_FILES + 1)

    # 저장소 안의 파일은 커밋이 훨씬 정확하게 말해 준다. 아직 커밋되지 않은
    # 것만 남긴다 - 그건 커밋이 모르는 정보다.
    repo_cache, dirty_cache = {}, {}
    kept = []
    for h in hits:
        repo = _repo_of(h['path'], repo_cache)
        if repo:
            if ctx.excluded_repo(repo):
                continue
            dirty = _uncommitted(repo, dirty_cache)
            if dirty is not None and os.path.normcase(h['path']) not in dirty:
                continue
            h['repo'] = repo
        kept.append(h)

    folders = OrderedDict()
    for h in sorted(kept, key=lambda x: x['path']):
        folders.setdefault(os.path.dirname(h['path']), []).append(h)

    return {'folders': [{'dir': d, 'files': v} for d, v in folders.items()],
            'total': len(kept),
            'truncated': len(hits) > MAX_FILES,
            'moved': moved,
            'skipped': None}


# ---------------------------------------------------------------- 출력

def render(data):
    L = []
    r = data['range']
    st = data['stats']
    L.append('# 수집 결과 %s ~ %s' % (r['from'], r['to']))
    L.append('')
    L.append('생성 %s · 시간대 %s' % (data['generated_at'], r['tz']))
    L.append('')
    head = ('세션 %d개 (claude %d, codex %d) · 지시 %d개 · 편집 파일 %d개 · 커밋 %d개'
            % (st['sessions'], st['claude'], st['codex'], st['prompts'], st['files'],
               st['commits']))
    if st.get('changed_files'):
        head += ' · git 밖에서 바뀐 파일 %d개' % st['changed_files']
    L.append(head)
    for c in data.get('custom') or []:
        if not c['mine']:
            continue
        L.append('')
        if c['used']:
            L.append('%s: 내 것을 쓴다 — `%s` (%.1fKB)'
                     % (c['label'], c['path'], c['size'] / 1024.0))
        else:
            L.append('%s: 내 것을 못 썼다 — %s. 기본값으로 쓴다' % (c['label'], c['why']))
    if data['auto_runs']:
        L.append('')
        L.append('자동 실행 %d회 (헤드리스·파이프라인. 사람이 프롬프트를 치지 않았다):'
                 % sum(data['auto_runs'].values()))
        for k, n in sorted(data['auto_runs'].items(), key=lambda x: -x[1]):
            L.append('- %s: %d회' % (k, n))
    fc = data.get('file_changes') or {}
    if fc.get('skipped'):
        L.append('')
        L.append('파일 변경: %s' % fc['skipped'])
    elif fc.get('folders'):
        L.append('')
        L.append('## 파일 변경 (대화·커밋에 안 남은 것)')
        L.append('')
        L.append('내 PC의 내 폴더에서 이 구간에 바뀐 파일이다. 읽기만 한 것, 받은 것,')
        L.append('복사해 온 것은 빠져 있다. 같은 폴더에서 비슷한 시각에 바뀐 것은 한 작업이다.')
        L.append('')
        for f in fc['folders']:
            L.append('### ' + f['dir'])
            for it in f['files']:
                L.append('- %s %s (%.1fKB)'
                         % (it['at'], os.path.basename(it['path']), it['size'] / 1024.0))
            L.append('')
        if fc.get('truncated'):
            L.append('(너무 많아 %d개에서 끊었다. 근거로 쓰기 어렵다)' % MAX_FILES)
            L.append('')
    for note in (fc.get('moved') or []):
        L.append('파일 변경에서 제외: %s (OneDrive는 읽지 않는다)' % note)
        L.append('')
    L.append('')
    for grp in data['groups']:
        L.append('## ' + grp['repo'])
        L.append('')
        for s in grp['sessions']:
            L.append('### [%s~%s] %s' % (s['start'] or '??', s['end'] or '??', s['title']))
            L.append('- %s · 브랜치 %s%s'
                     % (s['tool'], s['branch'] or '-',
                        ' · 같은 대화 재생 %d회 합침' % s['replays'] if s.get('replays') else ''))
            if s['prompts']:
                L.append('- 지시 %d개:' % s['prompt_total'])
                for p in s['prompts']:
                    L.append('  - (%s) %s' % (p['at'], p['text'].replace('\n', ' ')))
            if s['files']:
                L.append('- 편집 %d개 파일:' % len(s['files']))
                for f in s['files'][:12]:
                    L.append('  - %s (%d회)' % (f['path'], f['edits']))
            if s['agents']:
                L.append('- 서브에이전트: ' + ', '.join(s['agents']))
            L.append('')
        for g in grp['git']:
            if g['commits']:
                L.append('#### 커밋 %d개' % len(g['commits']))
                for c in g['commits']:
                    L.append('- %s `%s` %s (%d파일 +%d -%d)'
                             % (c['at'], c['hash'], c['subject'], c['files'],
                                c['added'], c['deleted']))
                    for p in c['paths']:
                        L.append('    - %s' % p)
                L.append('')
            if g['dirty']:
                L.append('#### 미커밋 변경 %d건' % g['dirty_total'])
                for d in g['dirty'][:20]:
                    L.append('- %s' % d)
                L.append('')
    return '\n'.join(L)


def collect(ctx, dfrom, dto):
    csess, cauto = scan_claude(ctx, dfrom, dto)
    xsess, xauto = scan_codex(ctx, dfrom, dto)
    sessions = merge_replays(csess + xsess)

    repo_of = {}
    for s in sessions:
        if s.cwd and s.cwd not in repo_of and not ctx.excluded(s.cwd):
            repo_of[s.cwd] = git_toplevel(s.cwd)
    repos = set(v for v in repo_of.values()
                if v and not ctx.excluded(v) and not ctx.excluded_repo(v))
    gits = scan_git(ctx, repos, dfrom, dto)

    groups = OrderedDict()

    def bucket(key):
        key = os.path.normpath(key)
        if key not in groups:
            groups[key] = {'repo': key, 'sessions': [], 'git': []}
        return groups[key]

    for s in sorted(sessions, key=lambda x: (x.times[0] if x.times else '')):
        target = repo_of.get(s.cwd) or s.cwd or '(경로 없음)'
        if ctx.excluded_repo(target):
            continue
        bucket(target)['sessions'].append(s.as_dict())
    for g in gits:
        bucket(g['repo'])['git'].append(g)

    files = scan_files(ctx, dfrom, dto)
    custom = scan_custom(ctx.root, ctx.cfg)

    tzname = 'UTC%+d' % (ctx.tz.utcoffset(None).total_seconds() / 3600) if ctx.tz else '지역 시간'
    return OrderedDict([
        ('range', {'from': dfrom, 'to': dto, 'tz': tzname}),
        ('generated_at', ctx.now()),
        ('stats', {'sessions': len(sessions),
                   'claude': sum(1 for s in sessions if s.tool == 'claude'),
                   'codex': sum(1 for s in sessions if s.tool == 'codex'),
                   'prompts': sum(len(s.prompts) for s in sessions),
                   'files': len(set(f for s in sessions for f in s.files)),
                   'commits': sum(len(g['commits']) for g in gits),
                   'changed_files': files['total']}),
        ('auto_runs', dict(cauto + xauto)),
        ('file_changes', files),
        ('custom', custom),
        ('groups', [g for g in groups.values() if g['sessions'] or g['git']]),
    ])


def self_check(ctx):
    """설치 직후 점검용. 기록 위치와 읽기 가능 여부만 본다."""
    out = sys.stdout
    out.write('python      : %s\n' % sys.version.split()[0])
    for label, roots, pattern in (('claude 기록', ctx.claude_dirs, ('*', '*.jsonl')),
                                  ('codex 기록', ctx.codex_dirs, ('*', '*', '*', '*.jsonl'))):
        if not roots:
            out.write('%-12s: 없음 (건너뜀)\n' % label)
            continue
        for path in roots:
            n = len(glob.glob(os.path.join(path, *pattern)))
            out.write('%-12s: %s (파일 %d개)\n' % (label, path, n))
    try:
        v = subprocess.run(['git', '--version'], capture_output=True, encoding='utf-8',
                           errors='replace', timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        v = '없음 — git 이력은 수집되지 않는다'
    out.write('git         : %s\n' % v)
    today = (datetime.now(ctx.tz) if ctx.tz else datetime.now()).strftime('%Y-%m-%d')
    data = collect(ctx, today, today)
    out.write('오늘 수집   : 세션 %d개, 커밋 %d개, 바뀐 파일 %d개\n'
              % (data['stats']['sessions'], data['stats']['commits'],
                 data['stats']['changed_files']))
    # 폴더 이동(Known Folder Move)으로 작업 폴더가 OneDrive에 있으면 파일이
    # 하나도 안 잡힌다. 설치할 때 알려 줘야 나중에 빈 보고서를 보고 헤매지 않는다.
    for note in (data.get('file_changes') or {}).get('moved') or []:
        out.write('            : %s — OneDrive는 읽지 않으므로 이 폴더의 파일은 빠진다\n' % note)
    return 0


def main():
    ap = argparse.ArgumentParser(description='AI 대화·git에서 보고서 재료를 수집한다')
    ap.add_argument('--from', dest='dfrom', help='YYYY-MM-DD (지역 시간 기준)')
    ap.add_argument('--to', dest='dto', help='YYYY-MM-DD (생략하면 --from과 같은 날)')
    ap.add_argument('--json', dest='json_out')
    ap.add_argument('--md', dest='md_out')
    ap.add_argument('--config', dest='config')
    ap.add_argument('--all-authors', action='store_true', help='내 커밋만이 아니라 전부')
    ap.add_argument('--check', action='store_true', help='설치 점검만 하고 끝낸다')
    a = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):      # 콘솔 기본 인코딩에서 한글이 깨지지 않게
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')

    cfg = load_config(a.config)
    if a.all_authors:
        cfg['mine_only'] = False
    ctx = Ctx(cfg)

    if a.check:
        raise SystemExit(self_check(ctx))
    if not a.dfrom:
        ap.error('--from 또는 --check 가 필요하다')

    dfrom = a.dfrom
    dto = a.dto or dfrom
    data = collect(ctx, dfrom, dto)
    md = render(data)

    for path, payload in ((a.json_out, None), (a.md_out, md)):
        if not path:
            continue
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, 'w', encoding='utf-8') as fh:
            if payload is None:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            else:
                fh.write(payload)

    if a.md_out:
        print('수집 완료: 세션 %d, 커밋 %d -> %s'
              % (data['stats']['sessions'], data['stats']['commits'], a.md_out))
    else:
        print(md)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:
        root = (os.environ.get('WORK_REPORT_DIR')
                or os.path.join(HOME, 'work-report'))
        log_error(root, 'collect', ' '.join(sys.argv[1:]) or '(인자 없음)', err)
        raise
