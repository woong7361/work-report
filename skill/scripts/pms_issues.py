# PMS open issue collection.
import io
import json
import os
import urllib.error
import urllib.request

from pms_config import EXIT_CONFIG, EXIT_ENV, EXIT_OK, Stop

NL = chr(10)


def open_issues(pms, project=None):
    """연결할 수 있는 일감(열린 것)을 API로 받아 온다.

    폼의 체크박스 목록과 같은 것이다. 보고서를 쓰는 시점에는 브라우저가 없고,
    예약 실행이면 창을 띄울 수도 없다. 토큰이 하는 일이 이것이다.
    """
    if not pms.get('token'):
        raise Stop(EXIT_CONFIG, 'PMS 토큰이 없다. 일감 목록은 토큰으로만 받아 온다.',
                   ['PMS에서 "내 계정 > API 접근키"를 복사해 설정의 PMS 토큰에 넣어라.',
                    '토큰이 없어도 보고서와 폼 채우기는 그대로 된다. 일감만 비어 있게 된다.'])
    project = project or pms['project']
    url = '%s/issues.json?project_id=%s&status_id=open&limit=100' % (pms['url'], project)
    req = urllib.request.Request(url, headers={'X-Redmine-API-Key': pms['token']})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Stop(EXIT_CONFIG, 'PMS 토큰이 받아들여지지 않았다 (%d).' % e.code,
                       ['설정의 PMS 토큰을 다시 확인해라. 다시 발급받아야 할 수도 있다.'])
        raise Stop(EXIT_ENV, 'PMS가 일감 목록을 주지 않았다 (%d).' % e.code, [url])
    except Exception as e:
        raise Stop(EXIT_ENV, 'PMS에 닿지 못했다: %s' % e, [url])
    out = []
    for i in data.get('issues') or []:
        out.append({'id': i['id'], 'subject': i.get('subject') or '',
                    'status': (i.get('status') or {}).get('name', ''),
                    'tracker': (i.get('tracker') or {}).get('name', ''),
                    'assigned': ((i.get('assigned_to') or {}).get('name') or '')})
    return out


def do_issues(pms, root):
    """에이전트가 보고서를 쓸 때 읽을 수 있게 파일로 남긴다."""
    found = open_issues(pms)
    folder = os.path.join(root, 'pms')
    if not os.path.isdir(folder):
        os.makedirs(folder)
    path = os.path.join(folder, 'open-issues.md')
    import datetime
    L = ['# 연결할 수 있는 일감',
         '',
         '%s 기준, %s 프로젝트에서 열려 있는 일감이다. 담당자와 상관없이 전부 나온다.'
         % (datetime.date.today().strftime('%Y-%m-%d'), pms['project']),
         '제출문의 "연결된 일감" 줄에는 **그날 한 일과 분명히 맞는 것만** 적는다.',
         '맞는 것이 없으면 그 줄을 만들지 않는다. 비슷해 보인다고 고르지 않는다.',
         '']
    for i in found:
        L.append('- #%s %s (%s%s)' % (i['id'], i['subject'], i['status'],
                                      ', 담당 ' + i['assigned'] if i['assigned'] else ''))
    if not found:
        L.append('- (열린 일감이 없다)')
    io.open(path, 'w', encoding='utf-8', newline='').write(NL.join(L) + NL)
    print('열린 일감 %d개 -> %s' % (len(found), path))
    return EXIT_OK
