# -*- coding: utf-8 -*-
"""보고서를 브라우저에서 읽고 고치고, 새로 만들고, 제출하러 갈 수 있게 연다.

`.md`를 여는 프로그램은 PC마다 다르고, 메모장으로 열리면 표가 깨진다.
그래서 브라우저로 연다. 다만 브라우저는 로컬 파일을 덮어쓰지 못하므로
저장을 받을 서버를 127.0.0.1에만 띄운다. 바깥에서는 접근할 수 없다.

    python viewer.py <보고서.md>
    python viewer.py <보고 폴더>      # 가장 최근 보고서를 연다
"""
import http.server
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import quote, unquote

import secrets as secret_store
from _log import log_error

from submission import parse_submission, report_date
from viewer_files import (AREAS, CUSTOM, ICON_PATH, SEEN_KEYS, asset_path,
                          asset_sibling, find_guide, list_custom, list_reports,
                          load_config, newest_report, read_config, safe_join,
                          seed_custom, write_config)
from viewer_jobs import (drop_job, jobs, jobs_lock, job_status, start_job,
                         start_pms)
from viewer_setup import setup_status

IDLE_TIMEOUT = 2 * 60 * 60  # 이 시간 동안 아무 요청이 없으면 스스로 종료한다
last_seen = time.time()

EMPTY_DOC = ('# 아직 보고서가 없습니다' + chr(10) + chr(10) +
             '위의 **일일보고 만들기** 를 눌러 보세요.')

def mark_running(root, port):
    """떠 있는 자리를 남긴다. 알림을 누를 때마다 서버가 새로 뜨지 않게 한다."""
    path = os.path.join(root, 'runlog', 'viewer.json')
    try:
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(path, 'w', encoding='utf-8', newline='') as fh:
            fh.write(json.dumps({'port': port, 'pid': os.getpid()}))
    except OSError:
        pass

    def clear():
        try:
            os.remove(path)
        except OSError:
            pass
    return clear


def html_escape(s):
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def build_page(title, submit_url, submit_label, has_readme, pms_on=False):
    return _render_page('index.html', title, submit_url, submit_label, has_readme, pms_on)


def build_asset(name, title, submit_url, submit_label, has_readme, pms_on=False):
    """페이지가 참조하는 자산을 같은 설정값으로 렌더링한다."""
    return _render_page(name, title, submit_url, submit_label, has_readme, pms_on)


def _render_page(name, title, submit_url, submit_label, has_readme, pms_on):
    here = os.path.dirname(os.path.realpath(__file__))
    path = os.path.join(here, 'page', name)
    with io.open(path, encoding='utf-8') as fh:
        text = fh.read()
    return (text.replace('{{TITLE}}', html_escape(title))
            .replace('{{SUBMIT_URL}}', html_escape(submit_url or ''))
            .replace('{{SUBMIT_LABEL}}', html_escape(submit_label or '제출하러 가기'))
            .replace('{{HAS_README}}', 'true' if has_readme else 'false')
            .replace('{{PMS_ON}}', 'true' if pms_on else 'false'))





def make_handler(root, report_path, page, readme_path, page_assets=None):
    page_assets = page_assets or {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _touch(self):
            global last_seen
            last_seen = time.time()

        def _authorized(self):
            # 127.0.0.1에만 붙으므로 다른 PC는 닿지 못하고, 같은 PC의 프로그램은
            # 어차피 보고서 파일을 직접 열 수 있다. 남는 위험은 브라우저로 연
            # 바깥 웹페이지가 이 포트를 두드리는 경우뿐인데, 그런 요청에는
            # 브라우저가 Origin을 붙이므로 우리 것이 아니면 거절한다.
            origin = self.headers.get('Origin')
            return not origin or origin == 'http://%s' % self.headers.get('Host', '')

        def _send(self, code, body=b'', ctype='text/plain; charset=utf-8'):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def _json(self, obj):
            self._send(200, json.dumps(obj, ensure_ascii=False).encode('utf-8'),
                       'application/json; charset=utf-8')

        def _parts(self):
            path, _, qs = self.path.partition('?')
            query = {}
            for kv in qs.split('&'):
                if '=' in kv:
                    k, v = kv.split('=', 1)
                    query[k] = unquote(v)
            return path.rstrip('/').rsplit('/', 1)[-1], query

        def _target(self, query, exts=('.md',)):
            rel = query.get('path')
            return safe_join(root, rel, exts) if rel else report_path

        def do_GET(self):
            self._touch()
            if not self._authorized():
                return self._send(403, b'forbidden')
            try:
                return self._get()
            except Exception as e:
                # 창 없이 뜨므로 여기서 안 적으면 아무 데도 안 남는다
                log_error(root, 'viewer', 'GET %s' % self.path, e)
                return self._send(500, b'error')

        def _get(self):
            leaf, query = self._parts()
            asset_path_name = self.path.partition('?')[0]
            if asset_path_name.startswith('/page/'):
                name = asset_path_name.rsplit('/', 1)[-1]
                body = page_assets.get(name)
                if body is None:
                    return self._send(404, b'no page asset')
                ctype = ('text/css; charset=utf-8' if name.endswith('.css')
                          else 'application/javascript; charset=utf-8')
                return self._send(200, body.encode('utf-8'),
                                   ctype)
            if leaf == 'files':
                return self._json({'groups': list_reports(root),
                                   'custom': list_custom(root)})
            if leaf == 'jobs':
                return self._json(job_status(root))
            if leaf == 'config':
                return self._json(read_config(root))
            if leaf == 'bins':
                return self._json(resolve_bins(root))
            if leaf.endswith('.woff2'):
                # 글꼴은 함께 들어 있다. 인터넷이 막힌 PC에서도 같게 보인다
                target = asset_path(leaf)
                if not target:
                    return self._send(404, b'no asset')
                with io.open(target, 'rb') as fh:
                    return self._send(200, fh.read(), 'font/woff2')
            if leaf == 'favicon.ico':
                if not ICON_PATH:
                    return self._send(404, b'no icon')
                with io.open(ICON_PATH, 'rb') as fh:
                    return self._send(200, fh.read(), 'image/x-icon')
            if leaf == 'setup':
                return self._json(setup_status(root))
            if leaf == 'readme':
                if not readme_path:
                    return self._send(404, b'no readme')
                with io.open(readme_path, encoding='utf-8') as fh:
                    return self._send(200, fh.read().encode('utf-8'))
            if leaf == 'report':
                target = self._target(query, ('.md', '.log'))
                if not target:
                    # 경로를 줬는데 못 열었으면 실패다. 빈 문서로 덮으면 드러나지 않는다
                    if query.get('path'):
                        return self._send(404, b'not found')
                    # 갓 설치해 보고서가 하나도 없는 경우
                    return self._json({'name': 'work-report', 'path': '',
                                       'text': EMPTY_DOC})
                with io.open(target, encoding='utf-8') as fh:
                    text = fh.read()
                return self._json({'name': os.path.basename(target),
                                   'path': os.path.relpath(target, root).replace(os.sep, '/'),
                                   'text': text})
            return self._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')

        def do_POST(self):
            self._touch()
            if not self._authorized():
                return self._send(403, b'forbidden')
            try:
                return self._post()
            except Exception as e:
                log_error(root, 'viewer', 'POST %s' % self.path, e)
                return self._send(500, b'error')

        def _post(self):
            leaf, query = self._parts()
            if leaf == 'config':
                length = int(self.headers.get('Content-Length') or 0)
                incoming = json.loads(self.rfile.read(length).decode('utf-8'))
                return self._json(write_config(root, incoming))
            if leaf == 'run':
                job_id = start_job(root, query.get('mode'))
                if not job_id:
                    return self._send(400, b'bad mode')
                return self._json({'id': job_id})
            if leaf == 'seen':
                what = query.get('what')
                if what in SEEN_KEYS:
                    cfg = read_config(root)
                    seen = [x for x in (cfg.get('setup_seen') or []) if x in SEEN_KEYS]
                    if what not in seen:
                        seen.append(what)
                        cfg['setup_seen'] = seen
                        with io.open(os.path.join(root, 'config.json'), 'w',
                                     encoding='utf-8', newline='') as fh:
                            fh.write(json.dumps(cfg, ensure_ascii=False, indent=2) + os.linesep)
                return self._json({'ok': True})
            if leaf == 'pms':
                job_id, why = start_pms(root, query.get('path'), query.get('mode') or 'fill')
                if not job_id:
                    return self._send(400, (why or 'bad request').encode('utf-8'))
                return self._json({'id': job_id})
            if leaf == 'dismiss':
                return self._json({'dropped': drop_job(query.get('id'))})
            target = self._target(query)
            if not target:
                return self._send(404, b'not found')
            length = int(self.headers.get('Content-Length') or 0)
            body = self.rfile.read(length).decode('utf-8')
            with io.open(target, 'w', encoding='utf-8', newline='') as fh:
                fh.write(body)
            return self._send(200, b'saved')

        def log_message(self, *args):
            pass                      # 콘솔을 조용히 둔다

    return Handler


def main():
    if len(sys.argv) < 2:
        print('usage: viewer.py <report.md | report folder>')
        return 2

    given = os.path.abspath(sys.argv[1])
    if os.path.isdir(given):
        root = given
        report = newest_report(root)          # 아직 하나도 없을 수 있다
    elif os.path.isfile(given):
        report = given
        _cfg, root = load_config(given)
    else:
        print('not found: %s' % given)
        return 1

    cfg = read_config(root)
    readme = find_guide()
    page = build_page(os.path.basename(report) if report else 'work-report',
                      cfg.get('submit_url') or '',
                      cfg.get('submit_label') or '제출하러 가기',
                      bool(readme),
                      bool((cfg.get('pms') or {}).get('url')))
    page_assets = {
        name: build_asset(name, os.path.basename(report) if report else 'work-report',
                          cfg.get('submit_url') or '',
                          cfg.get('submit_label') or '제출하러 가기',
                          bool(readme),
                          bool((cfg.get('pms') or {}).get('url')))
        for name in ('app.js', 'app-ui.js', 'app-setup.js', 'app.css')
    }

    probe = socket.socket()
    probe.bind(('127.0.0.1', 0))       # 바깥에서는 접근할 수 없다
    port = probe.getsockname()[1]
    probe.close()

    # 여러 요청이 겹친다. 보고서를 만드는 동안에도 화면이 상태를 물어보므로
    # 한 번에 하나만 받는 서버로는 막힌다.
    class Server(http.server.ThreadingHTTPServer):
        daemon_threads = True

    server = Server(('127.0.0.1', port),
                    make_handler(root, report, page, readme, page_assets))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    clear_mark = mark_running(root, port)

    url = 'http://127.0.0.1:%d/' % port
    if report:
        url += '?path=' + quote(os.path.relpath(report, root).replace(os.sep, '/'))
    webbrowser.open(url)
    print(url)

    # 탭을 닫아도 서버는 알 수 없으므로 조용해지면 물러난다.
    # 다만 보고서를 만드는 중이면 끝날 때까지 기다린다.
    while True:
        time.sleep(5)
        with jobs_lock:
            busy = any(j['state'] == 'running' for j in jobs.values())
        if not busy and time.time() - last_seen > IDLE_TIMEOUT:
            break
    server.shutdown()
    clear_mark()
    return 0


if __name__ == '__main__':
    sys.exit(main())
