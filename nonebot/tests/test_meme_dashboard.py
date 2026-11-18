import io
import sys
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import meme_logic


def test_dashboard_meme_library_requires_auth_and_supports_upload(tmp_path):
    data = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(data, format='PNG')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), patch.object(d, 'meme_library', meme_logic.Library(tmp_path / 'library')), patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            assert client.get('/api/memes').status_code == 401
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
            assert client.post('/api/memes/category', json={'name': '开心'}).status_code == 403
            category = client.post('/api/memes/category', json={'name': '开心', 'description': '庆祝'}, headers=headers).json()['id']
            upload = client.post('/api/memes/image?category=' + category, content=data.getvalue(), headers=headers)
            assert upload.status_code == 200
            image_id = upload.json()['id']
            assert client.get('/api/memes/image/' + image_id).content == data.getvalue()
            assert client.post('/api/memes/rules', json={'enabled': True, 'disabled_scopes': ['group:123']}, headers=headers).status_code == 200
            assert client.get('/api/memes').json()['rules']['disabled_scopes'] == ['group:123']
            assert client.post('/api/memes/image/delete', json={'id': image_id}, headers=headers).status_code == 200
            assert client.get('/api/memes/image/' + image_id).status_code == 404


def test_dashboard_rejects_invalid_images(tmp_path):
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), patch.object(d, 'meme_library', meme_logic.Library(tmp_path / 'library')), patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
            category = client.post('/api/memes/category', json={'name': '开心'}, headers=headers).json()['id']
            assert client.post('/api/memes/image?category=' + category, content=b'bad', headers=headers).status_code == 400
