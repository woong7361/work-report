# Local filesystem change reader.
import os
from collections import OrderedDict
from datetime import datetime

from sources_git import _repo_of, _uncommitted

HOME = os.path.expanduser('~')


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
