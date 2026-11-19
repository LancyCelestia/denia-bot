import json
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

            with TestClient(self._app(root)) as client:
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

            with TestClient(self._app(root)) as client, patch.object(embedding_panel, '_start_rebuild', fake_start):
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
            with TestClient(self._app(root)) as client:
                response = client.post('/api/embedding/rebuild', json={'confirm': True})
            self.assertEqual(response.status_code, 409)
            self.assertIn('API Key', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
