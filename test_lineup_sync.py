"""Regression checks for empty SSOT and pipeline failure propagation; no network."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import toss_capture as capture

ROOT = Path(__file__).parent

class LineupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.md = self.root / 'lineup.md'
        self.stocks = self.root / 'stocks.json'
        for name, value in [('DANTE_MD', self.md), ('STOCKS_FILE', self.stocks)]:
            p = patch.object(capture, name, value)
            p.start()
            self.addCleanup(p.stop)

    def document(self, line):
        self.md.write_text('## 6. 현재 종목 현황\n' + line + '\n과거 삭제 LG씨엔에스 (064400)\n## 7. 기타\n', encoding='utf8')

    def test_empty_ssot_ignores_history(self):
        for line in ['> - **활성**: (기존 전부 삭제)', '> - **활성**:']:
            self.document(line)
            self.assertEqual(capture.parse_holdings_from_md(), [])

    def test_one_stock_ignores_history(self):
        self.document('> - **활성**: 로보티즈 (108490)')
        self.assertEqual([s['code'] for s in capture.parse_holdings_from_md()], ['108490'])

    def test_parse_failure_is_distinct(self):
        self.assertIsNone(capture.parse_holdings_from_md())
        for text in ['## 7. 기타\n> - **활성**: 로보티즈 (108490)\n', '## 6. 현재 종목 현황\n과거 (064400)\n## 7. 기타\n']:
            self.md.write_text(text, encoding='utf8')
            self.assertIsNone(capture.parse_holdings_from_md())

    def test_resolve_empty_clears_seed_and_keeps_bowl3_separate(self):
        self.document('> - **활성**: (기존 전부 삭제)')
        self.stocks.write_text(json.dumps({'stocks': [{'name': '과거', 'code': '064400', 'tag': 'active'}]}), encoding='utf8')
        bowl = [{'name': '관찰', 'code': '123456', 'tag': 'bowl3'}]
        with patch.object(capture, 'load_bowl3_watch', return_value=bowl), patch.object(capture, 'load_stocks_seed', side_effect=AssertionError('must not restore seed')):
            self.assertEqual(capture.resolve_stocks(), bowl)
        self.assertEqual(json.loads(self.stocks.read_text(encoding='utf8'))['stocks'], [])
        self.assertEqual(len(json.loads(self.stocks.with_suffix('.json.bak').read_text(encoding='utf8'))['stocks']), 1)

    def test_unconfirmed_empty_still_refused(self):
        self.assertFalse(capture.sync_stocks_json([]))
        self.assertFalse(self.stocks.exists())

    def test_reduction_guard_retained(self):
        self.stocks.write_text(json.dumps({'stocks': [{'tag': 'active'}] * 5}), encoding='utf8')
        self.assertFalse(capture.sync_stocks_json([{'tag': 'active', 'code': '108490'}]))
        self.assertEqual(len(json.loads(self.stocks.read_text(encoding='utf8'))['stocks']), 5)

    @unittest.skipUnless(os.name == 'nt', 'Windows batch regression')
    def test_batch_captures_producer_failure(self):
        source = (ROOT / 'run_daily_dante.bat').read_text(encoding='utf8')
        # Execute the exact production analysis/error branch with a failing local JS stub.
        start = source.index('REM Preserve the producer exit code', source.index('echo [2/5]'))
        end = source.index('REM [2e]', start)
        block = source[start:end]
        (self.root / 'claude_dante_analyze.js').write_text('process.exit(7);', encoding='utf8')
        runner = self.root / 'failure.bat'
        script = '@echo off\nset "LOGFILE=check.log"\nset "TODAY=test"\nset "HHMM=0000"\nset "DRY_FLAG="\n' + block + '\necho UNEXPECTED_CONTINUE\nexit /b 0\n:summary\necho STATUS=%PIPE_STATUS% REASON=%PIPE_REASON%\nexit /b 1\n'
        runner.write_bytes(script.replace('\n', '\r\n').encode('ascii'))
        result = subprocess.run(['cmd', '/d', '/c', str(runner)], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('STATUS=fail', result.stdout)
        self.assertIn('Step 2', result.stdout)
        self.assertNotIn('UNEXPECTED_CONTINUE', result.stdout)

if __name__ == '__main__':
    unittest.main()
