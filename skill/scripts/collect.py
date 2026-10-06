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
import subprocess
import sys
from collections import OrderedDict
from datetime import datetime

from _log import log_error
from custom_files import scan_custom
from model import HOME, Ctx
from render import render
from sources_claude import merge_replays, scan_claude
from sources_codex import scan_codex
from sources_files import scan_files
from sources_git import git_toplevel, scan_git

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


# ---------------------------------------------------------------- 공통

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
