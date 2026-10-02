# Git history reader and repository path helpers.
import os
import subprocess
from collections import OrderedDict

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
