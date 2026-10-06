"""Offline acceptance checks for module boundaries; see tests/README.md."""
import contextlib
import http.client
import http.server
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

SCRIPTS = Path(__file__).resolve().parents[1] / 'skill' / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import collect
import model
import pms
import pms_browser
import pms_form
import pms_harvest
import pms_issues
import viewer
import viewer_jobs


class RefactorAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wr-accept-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config.json').write_text('{"author":"tester"}', encoding='utf-8')

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def test_http_read_write_assets_and_origin(self):
        """Serve real HTTP: reports, settings, bins, setup, assets and rejected writes."""
        rel = 'daily/2026-10/2026-10-01.md'
        report = self.write(rel, '# test\n')
        args = ('Test', '', 'Submit', True, True)
        names = ('app.js', 'app-ui.js', 'app-setup.js', 'app.css')
        assets = {n: viewer.build_asset(n, *args) for n in names}
        handler = viewer.make_handler(str(self.root), str(report), viewer.build_page(*args),
                                      viewer.find_guide(), assets)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(path, method='GET', body=None, headers=None):
            conn = http.client.HTTPConnection(*server.server_address, timeout=10)
            try:
                conn.request(method, path, body, headers or {})
                response = conn.getresponse()
                return response.status, response.getheader('Content-Type'), response.read()
            finally:
                conn.close()

        with patch.object(viewer_jobs, 'bins_cache', {'python': 'fixture'}):
            for endpoint in ('/', '/files', '/jobs', '/config', '/bins', '/setup',
                             '/readme', '/report', '/favicon.ico', '/assets/work-report.png'):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(request(endpoint)[0], 200)
            self.assertEqual(json.loads(request('/bins')[2]), {'python': 'fixture'})
        for name in names:
            status, content_type, body = request('/page/' + name)
            self.assertEqual(status, 200)
            self.assertEqual(body.decode('utf-8'), assets[name])
            self.assertIn('text/css' if name.endswith('.css') else 'javascript', content_type)
        self.assertEqual(request('/report?path=../outside.md')[0], 404)
        self.assertEqual(request('/report', 'POST', b'blocked',
                                 {'Origin': 'https://outside.invalid'})[0], 403)
        self.assertEqual(report.read_text(encoding='utf-8'), '# test\n')
        self.assertEqual(request('/report', 'POST', b'# changed\n')[0], 200)
        self.assertEqual(json.loads(request('/report')[2])['text'], '# changed\n')
        status, _, body = request('/config', 'POST', b'{"author":"changed","skill_dirs":["bad"]}')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['author'], 'changed')
        self.assertNotIn('skill_dirs', json.loads(body))
        self.assertEqual(request('/seen?what=custom', 'POST')[0], 200)
        self.assertEqual(request('/run?mode=invalid', 'POST')[0], 400)
        self.assertEqual(request('/pms?path=missing.md', 'POST')[0], 400)
        self.assertEqual(json.loads(request('/dismiss?id=missing', 'POST')[2]), {'dropped': False})

    def test_pms_issue_output_and_exit_code(self):
        """An API-shaped response writes the issue evidence and returns success."""
        response = io.BytesIO(json.dumps({'issues': [
            {'id': 12, 'subject': 'Fixture task', 'status': {'name': 'Open'},
             'assigned_to': {'name': 'Tester'}}]}).encode())
        cfg = {'url': 'https://pms.invalid', 'project': 'fixture', 'token': 'fixture'}
        with patch.object(pms_issues.urllib.request, 'urlopen', return_value=response), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pms.do_issues(cfg, str(self.root)), 0)
        text = (self.root / 'pms/open-issues.md').read_text(encoding='utf-8')
        self.assertIn('#12 Fixture task (Open', text)
        self.assertIn('Tester', text)

    def test_harvest_keeps_user_text_and_counts_reports(self):
        """Fetching examples replaces only its marked region, preserving manual text."""
        reports = [{'date': '2026-10-01', 'issues': ['12'], 'secs': [
            {'head': '한 일', 'groups': [{'badge': '개발', 'text': 'Fixture\n- task'}]}]}]
        self.write('custom/my-reports.md', 'Manual before\n<!-- PASTE BELOW -->\nManual after\n')
        path = pms_harvest.write_samples(str(self.root), reports, 1)
        first = Path(path).read_text(encoding='utf-8')
        pms_harvest.write_samples(str(self.root), reports, 1)
        second = Path(path).read_text(encoding='utf-8')
        self.assertIn('Manual before', second)
        self.assertIn('Manual after', second)
        self.assertEqual(first.count('<!-- 2026-10-01 -->'), 1)
        self.assertEqual(second.count('<!-- 2026-10-01 -->'), 1)
        stat = pms_harvest.summarize(reports)
        self.assertEqual(stat['categories'], {'개발': 1})
        self.assertEqual(stat['rows_per_report'], {1: 1})
        self.assertEqual(stat['issue_links'], 1)
        self.assertTrue(Path(pms_harvest.write_patterns(str(self.root), reports, stat)).is_file())

    def test_pms_fill_appends_without_saving(self):
        """Existing form row stays; category/content/issue are added; no save is clicked."""
        pg = Mock()
        pg.locator.return_value.count.return_value = 1
        notes = []
        pms.fill_form(pg, {'items': [{'category': '개발', 'content': 'Fixture task'}],
                           'linked_issue_ids': [12]}, notes)
        pg.select_option.assert_called_once_with(
            'select[name="daily_report[items_attributes][1][category]"]', 'development')
        pg.fill.assert_called_once_with('#daily_report_items_1_content', 'Fixture task')
        pg.click.assert_called_once_with('a#add-work-item')
        pg.check.assert_called_once_with('input[name="daily_report[linked_issue_ids][]"][value="12"]')

    def test_pms_reuses_one_form_tab(self):
        """Duplicate PMS form tabs are closed and the first tab is reused."""
        chosen = Mock(url='https://pms.invalid/projects/fixture/daily_reports/new?date=2026-10-01')
        duplicate = Mock(url='https://pms.invalid/projects/fixture/daily_reports/new?date=2026-10-01')
        context = Mock(pages=[chosen, duplicate])
        browser = Mock(contexts=[context])
        page = pms_browser.pick_page(browser, 'https://pms.invalid/projects/fixture/daily_reports/new')
        self.assertIs(page, chosen)
        duplicate.close.assert_called_once_with()

    def test_job_exit_codes_and_dismissal(self):
        """Each PMS exit code becomes the same viewer action state; running jobs remain."""
        states = {0: 'done', 10: 'opened', 2: 'login', 3: 'config', 4: 'playwright',
                  5: 'env', 1: 'failed', None: 'running'}
        log = self.write('runlog/pms.log', '[start]\nFixture note\n')
        for code, expected in states.items():
            with self.subTest(code=code), patch.dict(viewer_jobs.jobs, {}, clear=True):
                proc = Mock()
                proc.poll.return_value = code
                viewer_jobs.jobs['fixture'] = {'id': 'fixture', 'mode': 'pms', 'state': 'running',
                    'started': 1, 'proc': proc, 'path': None, 'log': str(log)}
                self.assertEqual(viewer.job_status(str(self.root))[0]['state'], expected)
                self.assertEqual(viewer.drop_job('fixture'), code is not None)

    def test_collect_transcripts_and_output(self):
        """Synthetic Claude/Codex records preserve prompts, edits, masking and raw outputs."""
        stamp = '2026-10-01T03:00:00Z'
        repo = str(self.root / 'project')
        Path(repo).mkdir()
        claude = [{'type': 'user', 'timestamp': stamp, 'cwd': repo,
                   'message': {'content': 'Fix chart'}},
                  {'type': 'assistant', 'timestamp': stamp, 'message': {'content': [
                      {'type': 'tool_use', 'name': 'Edit', 'input': {'file_path': repo + '/chart.py'}}]}}]
        codex = [{'payload': {'timestamp': stamp, 'cwd': repo, 'session_id': 'fixture-codex'}},
                 {'type': 'response_item', 'timestamp': stamp, 'payload': {
                     'type': 'message', 'role': 'user', 'content': [{'text': 'Review chart'}]}}]
        self.write('claude/projects/project/fixture.jsonl', '\n'.join(map(json.dumps, claude)))
        self.write('codex/sessions/2026/10/01/fixture.jsonl', '\n'.join(map(json.dumps, codex)))
        cfg = dict(collect.DEFAULTS, utc_offset_hours=9, exclude_paths=[],
                   _config_path=str(self.root / 'config.json'))
        ctx = model.Ctx(cfg)
        ctx.claude_dirs = [str(self.root / 'claude/projects')]
        ctx.codex_dirs = [str(self.root / 'codex/sessions')]
        data = collect.collect(ctx, '2026-10-01', '2026-10-01')
        self.assertEqual(data['stats']['sessions'], 2)
        self.assertEqual(data['stats']['prompts'], 2)
        self.assertEqual(data['stats']['files'], 1)
        md = collect.render(data)
        self.assertIn('Fix chart', md)
        self.assertIn('Review chart', md)
        self.assertIn('12:00', md)

    def test_python_modules_have_no_unbound_global_names(self):
        """Catch lost imports in rarely visited branches after moving functions."""
        import builtins
        import importlib
        import symtable
        for path in SCRIPTS.glob('*.py'):
            module = importlib.import_module(path.stem)
            table = symtable.symtable(path.read_text(encoding='utf-8-sig'), str(path), 'exec')
            known = set(vars(module)) | set(vars(builtins))
            known.update(s.get_name() for s in table.get_symbols() if s.is_assigned())
            def check(scope):
                for symbol in scope.get_symbols():
                    if symbol.is_global() and symbol.is_referenced():
                        self.assertIn(symbol.get_name(), known, '%s: %s' % (path.name, scope.get_name()))
                for child in scope.get_children():
                    check(child)
            with self.subTest(module=path.stem):
                check(table)


if __name__ == '__main__':
    unittest.main(verbosity=2)
