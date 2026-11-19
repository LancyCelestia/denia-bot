import io
import re
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import appearance_panel
import dashboard


TOKEN = 'panel-test-token'
NAME = re.compile(r'^[0-9]{10,16}-[a-f0-9]{8}\.(png|jpg|gif|webp)$')
AUTH = {'x-test-auth': TOKEN}
MUTATE = {'x-test-auth': TOKEN, 'x-test-csrf': TOKEN}


def png():
    output = io.BytesIO()
    Image.new('RGB', (8, 8), '#c66e4e').save(output, 'PNG')
    return output.getvalue()


def make_app(root):
    app = FastAPI()

    def authorized(req):
        if req.headers.get('x-test-auth') != TOKEN:
            raise HTTPException(401, '请登录')

    def mutate(req):
        authorized(req)
        if req.headers.get('x-test-csrf') != TOKEN:
            raise HTTPException(403, '请求来源无效')

    appearance_panel.register(app, authorized=authorized, mutate=mutate, root=root)
    return app


def test_upload_and_read_round_trip(tmp_path):
    content = png()
    with TestClient(make_app(tmp_path)) as client:
        saved = client.post('/api/appearance/background', content=content, headers=MUTATE)
        assert saved.status_code == 200
        body = saved.json()
        assert NAME.fullmatch(body['name'])
        assert body['url'] == f"/api/appearance/background/{body['name']}"
        assert (tmp_path / 'data' / 'appearance-backgrounds' / body['name']).is_file()

        served = client.get(body['url'], headers=AUTH)
        assert served.status_code == 200
        assert served.content == content
        assert served.headers['content-type'] == 'image/png'
        assert served.headers['cache-control'] == 'private, max-age=31536000, immutable'
        assert served.headers['x-content-type-options'] == 'nosniff'


def test_invalid_image_is_rejected(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/appearance/background', content=b'not an image', headers=MUTATE)
    assert response.status_code == 400
    assert not (tmp_path / 'data' / 'appearance-backgrounds').exists()


def test_svg_is_rejected(tmp_path):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8"/></svg>'
    with TestClient(make_app(tmp_path)) as client:
        assert client.post('/api/appearance/background', content=svg, headers=MUTATE).status_code == 400


def test_oversize_upload_is_rejected_with_413(tmp_path, monkeypatch):
    monkeypatch.setattr(appearance_panel, 'MAX_IMAGE_BYTES', 32)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/appearance/background', content=png(), headers=MUTATE)
    assert response.status_code == 413
    assert not (tmp_path / 'data' / 'appearance-backgrounds').exists()


def test_upload_requires_login(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/appearance/background', content=png())
    assert response.status_code == 401


def test_upload_requires_csrf(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/appearance/background', content=png(), headers=AUTH)
    assert response.status_code == 403


def test_read_requires_login(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get('/api/appearance/background/1700000000-0123abcd.png')
    assert response.status_code == 401


@pytest.mark.parametrize('name', [
    'notes.txt',
    '1700000000-0123abcd.svg',
    '1700000000-0123abcd',
    'appearance.png',
    '..%2F..%2Fsecret.png',
])
def test_invalid_or_unknown_names_return_404(tmp_path, name):
    with TestClient(make_app(tmp_path)) as client:
        response = client.get(f'/api/appearance/background/{name}', headers=AUTH)
    assert response.status_code == 404


def test_store_rejects_path_traversal(tmp_path):
    store = appearance_panel.BackgroundStore(tmp_path)
    for name in ('../escape.png', '../../data/secret.png', str(tmp_path / 'x.png'), 'x.png'):
        with pytest.raises(ValueError):
            store.read(name)
    assert not (tmp_path / 'data' / 'appearance-backgrounds').exists()


def test_store_keeps_only_the_newest_backgrounds(tmp_path):
    store = appearance_panel.BackgroundStore(tmp_path)
    names = [store.save(png())[0] for _ in range(appearance_panel.MAX_BACKGROUNDS + 3)]
    kept = sorted(path.name for path in store.directory.iterdir())
    assert len(kept) == appearance_panel.MAX_BACKGROUNDS
    assert set(kept) <= set(names)
    assert all(NAME.fullmatch(name) for name in kept)
    for name in kept:
        assert store.read(name)[1] == 'image/png'


def test_dashboard_wires_background_routes():
    paths = {getattr(route, 'path', '') for route in dashboard.app.routes}
    assert '/api/appearance/background' in paths
    assert '/api/appearance/background/{name}' in paths
    with TestClient(dashboard.app) as client:
        assert client.get('/api/appearance/background/1700000000-0123abcd.png').status_code == 401
