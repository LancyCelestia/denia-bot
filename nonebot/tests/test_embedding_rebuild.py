import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import embedding_panel


class EmbeddingRebuildPanelTest(unittest.TestCase):
    def _app(self, root: Path) -> FastAPI:
        app = FastAPI()
        embedding_panel.register(
            app,
            authorized=lambda req, allow_change=False: 'token',
            mutate=lambda req, allow_change=False: 'token',
            root=root,
        )
        return app

    def test_sources_are_recursive_and_profile_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (repo / 'knowledge' / 'nested').mkdir(parents=True)
            (repo / 'knowledge' / 'a.md').write_text('a', encoding='utf-8')
            (repo / 'knowledge' / 'nested' / 'b.txt').write_text('b', encoding='utf-8')
            (repo / 'knowledge' / 'ignored.jpg').write_bytes(b'not text')
            profile = repo / 'rag' / 'imports' / 'character-profiles-test' / 'chunks.jsonl'
            profile.parent.mkdir(parents=True)
            profile.write_text('{"embedding_text":"one"}\n\n{"embedding_text":"two"}\n', encoding='utf-8')
            active = repo / 'rag' / 'index-siliconflow-test'
            active.mkdir()
            (root / 'data').mkdir()
            (root / 'data' / 'rag-rebuild.json').write_text(
                json.dumps({'state': 'success', 'active_index': str(active)}), encoding='utf-8')

            with patch.object(embedding_panel, '_serving_index', return_value=None), \
                    TestClient(self._app(root)) as client:
                data = client.get('/api/embedding/rebuild').json()
            names = [item['name'] for item in data['sources']]
            self.assertEqual(names, ['knowledge/a.md', 'knowledge/nested/b.txt',
                                     'rag/imports/character-profiles-test/chunks.jsonl'])
            self.assertEqual(data['knowledge_count'], 2)
            self.assertEqual(data['profile_count'], 2)
            self.assertEqual(data['active_index'], str(active))

    def test_confirmation_is_required_and_start_is_delegated(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (repo / 'knowledge').mkdir()
            (repo / 'knowledge' / 'a.md').write_text('a', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'embedding_secrets.json').write_text(
                json.dumps({'api_key': 'sk-test-123456'}), encoding='utf-8')

            def fake_start(root, *, sources, active, state, log_path):
                state.update({'state': 'running', 'pid': 123, 'sources': [x['name'] for x in sources['sources']]})
                return state

            with patch.object(embedding_panel, '_serving_index', return_value=None), \
                    TestClient(self._app(root)) as client, \
                    patch.object(embedding_panel, '_start_rebuild', fake_start):
                unconfirmed = client.post('/api/embedding/rebuild', json={'confirm': False})
                self.assertEqual(unconfirmed.status_code, 400)
                started = client.post('/api/embedding/rebuild', json={'confirm': True})
                self.assertEqual(started.status_code, 200)
                self.assertEqual(started.json()['rebuild']['state'], 'running')
                self.assertEqual(started.json()['rebuild']['sources'], ['knowledge/a.md'])

    def test_rebuild_requires_configured_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (repo / 'knowledge').mkdir()
            (repo / 'knowledge' / 'a.md').write_text('a', encoding='utf-8')
            (root / 'data').mkdir()
            with patch.object(embedding_panel, '_serving_index', return_value=None), \
                    TestClient(self._app(root)) as client:
                response = client.post('/api/embedding/rebuild', json={'confirm': True})
            self.assertEqual(response.status_code, 409)
            self.assertIn('API Key', response.json()['detail'])

    def test_active_index_prefers_health_over_state_and_newest_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (root / 'data').mkdir()
            older = repo / 'rag' / 'index-siliconflow-old'
            newer = repo / 'rag' / 'index-siliconflow-new'
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (root / 'data' / 'rag-rebuild.json').write_text(
                json.dumps({'state': 'success', 'active_index': str(older)}), encoding='utf-8')
            with patch.object(embedding_panel, '_serving_index', return_value=newer):
                self.assertEqual(embedding_panel._active_index(root), newer)

    def test_active_index_does_not_guess_the_newest_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (root / 'data').mkdir()
            (repo / 'rag' / 'index-siliconflow-new').mkdir(parents=True)
            with patch.object(embedding_panel, '_serving_index', return_value=None), \
                    patch.dict(os.environ, {}, clear=True):
                self.assertIsNone(embedding_panel._active_index(root))

    def test_serving_index_reads_the_health_index_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            index = repo / 'rag' / 'index-siliconflow'
            index.mkdir(parents=True)

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json.dumps({'index': str(index)}).encode()

            class Opener:
                def open(self, url, timeout):
                    self.url = url
                    return Response()

            opener = Opener()
            with patch.object(embedding_panel.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(embedding_panel._serving_index(root), index)
            self.assertTrue(opener.url.endswith('/health'))

    def test_rebuild_command_passes_the_active_index_and_health_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (root / 'data').mkdir()
            (repo / 'knowledge').mkdir()
            (repo / 'knowledge' / 'a.md').write_text('a', encoding='utf-8')
            (repo / 'rag').mkdir(parents=True)
            (repo / 'rag' / 'rebuild_index.py').write_text('# builder\n', encoding='utf-8')
            active = repo / 'rag' / 'index-siliconflow'
            active.mkdir(parents=True)
            sources = {'sources': [], 'profile': ''}
            captured = {}

            class FakeProcess:
                pid = 123

            def fake_popen(command, **kwargs):
                captured['command'] = command
                return FakeProcess()

            with patch.object(embedding_panel.subprocess, 'Popen', side_effect=fake_popen), \
                    patch.object(embedding_panel, '_python_for', return_value='python-x'):
                embedding_panel._start_rebuild(
                    root, sources=sources, active=active, state={},
                    log_path=root / 'data' / 'rebuild.log',
                )
            command = captured['command']
            self.assertIn('--activate', command)
            self.assertEqual(command[command.index('--active') + 1], str(active))
            self.assertTrue(command[command.index('--health-url') + 1].endswith('/health'))


    def test_rebuild_defaults_to_cloud_index_when_no_active_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            root = repo / 'nonebot'
            root.mkdir(parents=True)
            (root / 'data').mkdir()
            (repo / 'knowledge').mkdir()
            (repo / 'knowledge' / 'a.md').write_text('a', encoding='utf-8')
            (repo / 'rag').mkdir(parents=True)
            (repo / 'rag' / 'rebuild_index.py').write_text('# builder\n', encoding='utf-8')
            sources = {'sources': [], 'profile': ''}
            captured = {}

            class FakeProcess:
                pid = 123

            def fake_popen(command, **kwargs):
                captured['command'] = command
                return FakeProcess()

            with patch.object(embedding_panel.subprocess, 'Popen', side_effect=fake_popen), \
                    patch.object(embedding_panel, '_python_for', return_value='python-x'):
                embedding_panel._start_rebuild(
                    root, sources=sources, active=None, state={},
                    log_path=root / 'data' / 'rebuild.log',
                )
            command = captured['command']
            default = repo / 'rag' / 'index-siliconflow'
            self.assertIn('--activate', command)
            self.assertEqual(command[command.index('--active') + 1], str(default))
            output = Path(command[command.index('--output') + 1])
            self.assertEqual(output.parent, default.parent)
            self.assertTrue(output.name.startswith('index-siliconflow.rebuild-'))


if __name__ == '__main__':
    unittest.main()
