import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as d
import meme_catalog
import meme_logic


def picture(color):
    data = io.BytesIO()
    Image.new('RGB', (4, 4), color).save(data, format='PNG')
    return data.getvalue()


def make_sticker_tree(root):
    folder = root / 'stickers' / '守岸人Q版表情包'
    folder.mkdir(parents=True)
    (folder / '开心.png').write_bytes(picture('yellow'))
    (folder / '开心副本.png').write_bytes(picture('yellow'))
    (folder / '难过.png').write_bytes(picture('blue'))


def test_local_import_creates_metadata_and_skips_duplicates(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')

    first = meme_catalog.import_tree(library)

    assert first['imported'] == 2
    assert first['skipped'] == 1
    assert first['categories'] == 1
    categories = library.categories()
    assert categories[0]['name'] == '守岸人Q版表情包'
    images = library.images(categories[0]['id'])
    assert len(images) == 2
    assert all('角色:守岸人' in image['tags'] for image in images)
    assert all('风格:Q版' in image['tags'] for image in images)
    assert all(image['weight'] == 1.1 for image in images)
    assert all(image['source'].endswith('.png') for image in images)

    second = meme_catalog.import_tree(library)
    assert second['imported'] == 0
    assert second['skipped'] == 3


def test_auto_tag_library_updates_existing_images(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')
    category = library.put_category('日常', '普通聊天')
    image = library.add_image(category, picture('red'))

    result = meme_catalog.auto_tag_library(library, category)

    assert result['updated'] == 1
    saved = library.images(category)[0]
    assert saved['id'] == image
    assert '情绪:' in saved['tags']
    assert saved['weight'] in (1.0, 1.05, 1.1, 0.95)


def test_choose_filters_by_tag_and_uses_metadata_weights(tmp_path):
    library = meme_logic.Library(tmp_path)
    category = library.put_category('开心', '庆祝')
    happy = library.add_image(category, picture('yellow'), tags='情绪:开心', weight=0.2)
    library.add_image(category, picture('blue'), tags='情绪:难过', weight=5.0)

    seen = {}
    def choose(rows, weights, k):
        seen['ids'] = [row[0] for row in rows]
        seen['weights'] = list(weights)
        return [rows[0]]

    with patch.object(meme_logic.random, 'choices', choose):
        image, mime = library.choose(category, '情绪:开心')

    assert seen['ids'] == [happy]
    assert seen['weights'] == [0.2]
    assert image == picture('yellow')
    assert mime == 'image/png'


def test_metadata_rejects_bad_tags_and_weight(tmp_path):
    library = meme_logic.Library(tmp_path)
    category = library.put_category('开心', '庆祝')
    image = library.add_image(category, picture('green'))

    with pytest.raises(ValueError):
        library.set_metadata(image, tags='x' * 25)
    with pytest.raises(ValueError):
        library.set_metadata(image, weight=0)
    with pytest.raises(ValueError):
        library.set_metadata('not-an-id', tags='情绪:开心')


def test_dashboard_bulk_import_and_metadata_api(tmp_path, monkeypatch):
    make_sticker_tree(tmp_path)
    monkeypatch.setattr(meme_catalog, 'DATA_ROOT', tmp_path)
    monkeypatch.setattr(meme_catalog, 'DEFAULT_IMPORT_ROOT', tmp_path / 'stickers')
    library = meme_logic.Library(tmp_path / 'library')
    with patch.object(d, 'AUTH_FILE', tmp_path / 'auth'), \
            patch.object(d, 'meme_library', library), \
            patch.object(d, 'session_store_path', return_value=tmp_path / 'sessions'):
        d.set_password('a-strong-test-password')
        with TestClient(d.app) as client:
            login = client.post('/api/login', json={'password': 'a-strong-test-password'})
            headers = {'Origin': 'http://testserver', 'X-CSRF-Token': login.json()['csrf']}
            imported = client.post('/api/memes/import', json={}, headers=headers)
            assert imported.status_code == 200, imported.text
            assert imported.json()['imported'] == 2
            categories = client.get('/api/memes').json()['categories']
            image = categories[0]['images'][0]
            assert '角色:守岸人' in image['tags']
            saved = client.post('/api/memes/metadata', json={
                'id': image['id'], 'tags': '角色:守岸人, 情绪:开心', 'weight': 1.4,
            }, headers=headers)
            assert saved.status_code == 200
            assert saved.json()['weight'] == 1.4
            assert client.post('/api/memes/metadata', json={'id': image['id']}, headers=headers).status_code == 400
