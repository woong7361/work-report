# Viewer report files, safe paths and editable configuration.
import io
import json
import os

import secrets as secret_store


# 왼쪽 목록에 보여줄 산출물과 이름
AREAS = (('daily', '일일 보고'), ('weekly', '주간 보고'),
         ('log', '한 일 목록'), ('raw', '수집 원본'))

# 보고 폴더의 custom\ 에 두면 보고서 양식과 문체를 바꾼다. 업데이트가 덮어쓰지 않는다.
CUSTOM = (('report-format.md', '보고서 양식', 'custom_format'),
          ('writing-rules.md', '글쓰기 문체', 'custom_rules'),
          ('my-reports.md', '내 보고서', 'custom_samples'))

def load_config(report_path):
    """보고 폴더의 config.json을 찾는다. 보고서는 <root>/<종류>/<월>/ 아래에 있다."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(report_path))))
    try:
        with open(os.path.join(root, 'config.json'), encoding='utf-8-sig') as fh:
            return json.load(fh), root
    except (OSError, ValueError):
        return {}, root


def list_reports(root):
    """산출물을 종류 → 월로 묶는다. 쌓여도 한 화면에 펼쳐지지 않게 한다."""
    groups = []
    for area, label in AREAS:
        base = os.path.join(root, area)
        if not os.path.isdir(base):
            continue
        months = []
        for month in sorted(os.listdir(base), reverse=True):
            mdir = os.path.join(base, month)
            if not os.path.isdir(mdir):
                continue
            items = [{'name': n[:-3], 'path': '%s/%s/%s' % (area, month, n)}
                     for n in sorted(os.listdir(mdir), reverse=True) if n.endswith('.md')]
            if items:
                months.append({'month': month, 'items': items})
        if months:
            groups.append({'area': area, 'label': label, 'months': months})
    return groups


def list_custom(root):
    """내 양식. 쓸지 말지는 설정의 토글이 정하고, 파일은 그때 생긴다."""
    cfg = read_config(root)
    out = []
    for name, label, flag in CUSTOM:
        path = os.path.join(root, 'custom', name)
        out.append({'name': label,
                    'path': 'custom/' + name,
                    'mine': bool(cfg.get(flag)),
                    'exists': os.path.isfile(path)})
    return out


def seed_custom(root, name):
    """기본값을 custom 폴더로 복사한다. 이미 있으면 건드리지 않는다."""
    if name not in [c[0] for c in CUSTOM]:
        return None
    target = os.path.join(root, 'custom', name)
    if os.path.isfile(target):
        return 'custom/' + name
    src = asset_sibling('templates', name)
    if not src:
        return None
    folder = os.path.dirname(target)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with io.open(src, encoding='utf-8') as fh:
        body = fh.read()
    with io.open(target, 'w', encoding='utf-8', newline='') as fh:
        fh.write(body)
    return 'custom/' + name


def safe_join(root, rel, exts=('.md',)):
    """보고 폴더 밖은 열지 않는다.

    읽기는 실행 기록(.log)까지 받는다 - 실패 알림을 누르면 그것을 보여 줘야 한다.
    쓰기는 .md로 묶는다. 같은 함수로 저장 경로를 정하므로 넓히면 실행 기록을
    덮어쓸 수 있게 된다.
    """
    full = os.path.normpath(os.path.join(root, rel.replace('/', os.sep)))
    if not full.lower().startswith(os.path.join(root, '').lower()):
        return None
    if not full.lower().endswith(exts) or not os.path.isfile(full):
        return None
    return full


def newest_report(root):
    """가장 최근 보고서. 일일 → 주간 → 한 일 목록 순으로 찾는다."""
    for area, _label in AREAS:
        base = os.path.join(root, area)
        if not os.path.isdir(base):
            continue
        found = []
        for dirpath, _dirs, names in os.walk(base):
            found += [os.path.join(dirpath, n) for n in names if n.endswith('.md')]
        if found:
            return max(found, key=os.path.getmtime)
    return None


def find_guide():
    """화면에서 읽는 사용 설명서.

    GUIDE.md는 skill 폴더에 들어 있다. 복사로 설치하면 저장소의 README까지
    따라오지 않기 때문이다. 연결(junction)로 설치하면 이 파일의 겉보기 경로가
    skills 폴더 안이라 거슬러 올라갈 수 없으므로 실제 위치로 풀어서 찾는다.
    """
    here = os.path.dirname(os.path.realpath(__file__))
    for name in ('GUIDE.md', 'README.md'):
        for up in ('..', os.path.join('..', '..')):
            path = os.path.normpath(os.path.join(here, up, name))
            if os.path.isfile(path):
                return path
    return None


def asset_sibling(folder, name):
    """skill 폴더 아래 지정한 폴더의 파일만 내준다."""
    if not name or name.startswith('.') or '/' in name or '\\' in name:
        return None
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.normpath(os.path.join(here, '..', folder, name))
    return path if os.path.isfile(path) else None


def asset_path(name):
    """skill/assets 안의 파일만 내준다. 글꼴과 아이콘이 거기 있다."""
    if not name or name.startswith('.') or '/' in name or '\\' in name:
        return None
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.normpath(os.path.join(here, '..', 'assets', name))
    return path if os.path.isfile(path) else None


ICON_PATH = asset_path('work-report.ico')


# 화면에서 고칠 수 있는 항목만 받는다. 설치가 채우는 기계 정보
# (*_homes, *_dirs, skill_dirs)는 손으로 고치면 깨지므로 받지 않는다.
# 설정을 한 번 열어 봤는지. "내 양식"은 켜지 않는 것도 답이라서, 토글로는
# 끝났는지 알 수 없다. 본 적이 있으면 그 단계는 끝난 것으로 둔다.
SEEN_KEYS = ('custom',)

EDITABLE = {
    'author': str, 'agent': str, 'notify': bool, 'submit_url': str, 'submit_label': str,
    'mine_only': bool, 'redact': bool, 'backfill_days': int, 'retain_months': int,
    'custom_format': bool, 'custom_rules': bool, 'custom_samples': bool,
    'exclude_repos': list, 'exclude_paths': list,
    'claude_bin': str, 'codex_bin': str, 'python_bin': str,
    'max_prompt_chars': int, 'max_prompts_per_session': int,
}
WEEKLY_KEYS = {'end_day': str, 'span_days': int}
# PMS 일일보고 폼을 채우는 기능의 설정. 비어 있으면 그 기능만 꺼진 것처럼 동작한다.
# projects 는 지난 보고서를 받아올 프로젝트들이고, 비우면 project 하나만 본다.
# token 은 여기 없다. 금고(secrets.py)로 따로 간다 - config.json 은 에이전트가
# 읽는 폴더에 있고, 비밀값이 거기 있으면 읽힌다.
PMS_KEYS = {'url': str, 'project': str, 'projects': list, 'port': int,
            'profile': str, 'chrome_bin': str}


def read_config(root):
    try:
        with open(os.path.join(root, 'config.json'), encoding='utf-8-sig') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def write_config(root, incoming):
    """들어온 값 중 허용된 것만 반영하고 나머지는 그대로 둔다."""
    cfg = read_config(root)
    for key, kind in EDITABLE.items():
        if key not in incoming:
            continue
        value = incoming[key]
        try:
            if kind is bool:
                cfg[key] = bool(value)
            elif kind is int:
                cfg[key] = int(value)
            elif kind is list:
                cfg[key] = [str(v).strip() for v in value if str(v).strip()]
            else:
                cfg[key] = str(value)
        except (TypeError, ValueError):
            continue
    # 토글을 켠 순간 고칠 파일이 있어야 한다. 첫 실행을 기다리게 하지 않는다
    for name, _label, flag in CUSTOM:
        if cfg.get(flag):
            seed_custom(root, name)

    # 비밀값은 설정 파일이 아니라 금고로. 빈 값은 "그대로 둬라"는 뜻이라 무시한다.
    token = ((incoming.get('pms') or {}).get('token') or '').strip()
    if token:
        secret_store.put(root, 'pms_token', token)

    for group, allowed in (('weekly', WEEKLY_KEYS), ('pms', PMS_KEYS)):
        incoming_group = incoming.get(group) or {}
        if not incoming_group:
            continue
        cur = dict(cfg.get(group) or {})
        for key, kind in allowed.items():
            if key not in incoming_group:
                continue
            value = incoming_group[key]
            try:
                if kind is int:
                    cur[key] = int(value or 0)
                elif kind is list:
                    cur[key] = [str(v).strip() for v in value if str(v).strip()]
                else:
                    cur[key] = str(value)
            except (TypeError, ValueError):
                pass
        cfg[group] = cur
    path = os.path.join(root, 'config.json')
    with io.open(path, 'w', encoding='utf-8', newline='') as fh:
        fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
    return cfg
