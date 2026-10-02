# Collected evidence to Markdown rendering.
import os

from sources_files import MAX_FILES


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
            if c.get('missing'):
                L.append('  기본 양식에 있는 절이 내 파일에는 없다: %s'
                         % ', '.join(c['missing']))
                L.append('  업데이트로 생긴 절이다. 필요하면 기본 양식에서 옮겨 적어라'
                         '(토글을 끄면 기본 양식을 쓴다).')
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
