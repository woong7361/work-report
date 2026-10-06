# Codex transcript reader.
import glob
import json
import os
from collections import Counter
from datetime import datetime, timedelta

from model import Session
from prompts import PATCH_RE, patch_path


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
