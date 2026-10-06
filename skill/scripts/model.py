# -*- coding: utf-8 -*-
"""수집 한 번의 설정(Ctx)과 세션 한 건(Session), 그리고 기록 위치 탐색.

조정 모듈(collect.py)과 소스 모듈(sources_*.py)이 함께 쓰는 자료구조다.
진입점에 두면 소스가 진입점을 거꾸로 import해야 하므로 여기 둔다 - 의존성은
진입점에서 책임 모듈로만 흐른다.
"""
import os
from collections import Counter, OrderedDict
from datetime import datetime, timedelta, timezone

from prompts import DROP_PREFIX, INJECTED_HEAD_RE, INJECTED_MARKERS, redact

HOME = os.path.expanduser('~')


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
