# Viewer setup status assembly.
import importlib.util
import io
import json
import os

import secrets as secret_store

from submission import parse_submission
from viewer_files import AREAS, CUSTOM, SEEN_KEYS, read_config


def setup_status(root):
    """지금 무엇이 되어 있고 무엇이 비었는지.

    차례대로 넘기는 마법사 대신 상태를 보여 준다. 사람마다 도착 지점이 다르고,
    이미 해 둔 것을 다시 묻는 화면은 길잡이가 아니라 방해다. 여기서는 사실만
    모으고, 무엇을 권할지는 화면이 정한다.
    """
    cfg = read_config(root)
    pms = cfg.get('pms') or {}
    counts = {}
    for area, _label in AREAS:
        base = os.path.join(root, area)
        n = 0
        for _dirpath, _dirs, names in os.walk(base):
            n += len([x for x in names if x.endswith('.md')])
        counts[area] = n

    # 가장 최근 일일보고와 거기 제출문이 있는지. "제출할 것이 있나"의 답이다.
    latest, has_submission = None, False
    daily = os.path.join(root, 'daily')
    found = []
    for dirpath, _dirs, names in os.walk(daily):
        found += [os.path.join(dirpath, n) for n in names if n.endswith('.md')]
    if found:
        newest = max(found, key=os.path.getmtime)
        latest = os.path.relpath(newest, root).replace(os.sep, '/')
        try:
            with io.open(newest, encoding='utf-8') as fh:
                has_submission = bool(parse_submission(fh.read())['items'])
        except OSError:
            pass

    # 로그인했는지는 띄워 보지 않으면 모른다. 쿠키가 남아 있는지로 "한 적 있다"까지만.
    profile = pms.get('profile') or os.path.join(root, 'browser')
    signed_in = os.path.isfile(os.path.join(profile, 'Default', 'Network', 'Cookies'))

    fetched = os.path.join(root, 'pms', 'my-daily-reports.json')
    harvest = {'count': 0, 'latest': None}
    try:
        with io.open(fetched, encoding='utf-8-sig') as fh:
            got = json.load(fh).get('reports') or []
        harvest = {'count': len(got), 'latest': got[0]['date'] if got else None}
    except (OSError, ValueError, KeyError, IndexError):
        pass

    try:
        import importlib.util
        playwright = importlib.util.find_spec('playwright') is not None
    except Exception:
        playwright = False

    issues = os.path.join(root, 'pms', 'open-issues.md')
    return {'reports': counts, 'latest_daily': latest, 'has_submission': has_submission,
            'custom': {name: bool(cfg.get(flag)) for name, _l, flag in CUSTOM},
            'seen': [x for x in (cfg.get('setup_seen') or []) if x in SEEN_KEYS],
            'author': cfg.get('author') or '',
            # 토큰은 있는지만 알린다. 값은 화면으로 돌려보내지 않는다.
            'pms': {'url': pms.get('url') or '', 'project': pms.get('project') or '',
                    'has_token': secret_store.has(root, 'pms_token'),
                    'issues_at': os.path.isfile(issues),
                    'signed_in': signed_in, 'playwright': playwright, 'harvest': harvest}}
