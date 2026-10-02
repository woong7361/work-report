# Claude Code transcript reader.
import glob
import json
import os
from collections import Counter
from datetime import datetime, timedelta

from prompts import EDIT_TOOLS


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


def scan_claude(ctx, dfrom, dto):
    """~/.claude/projects/<경로>/<세션>.jsonl"""
    from collect import Session
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
