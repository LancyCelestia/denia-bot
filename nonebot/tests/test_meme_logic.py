import io
import base64
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import meme_logic as m


def picture(fmt='PNG'):
    data = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(data, format=fmt)
    return data.getvalue()


def test_library_upload_scope_and_delete(tmp_path):
    lib = m.Library(tmp_path)
    category = lib.put_category('开心', '庆祝、快乐')
    image = lib.add_image(category, picture())
    assert lib.categories()[0]['count'] == 1
    assert lib.image(image) == picture()
    assert lib.enabled('group:123')
    lib.set_rules(False, ['group:123'])
    assert not lib.enabled('group:123')
    assert not lib.enabled('private:123')
    lib.set_rules(True, ['group:123'])
    assert not lib.enabled('group:123')
    assert lib.enabled('private:123')
    lib.delete_image(image)
    assert lib.categories()[0]['count'] == 0


def test_reject_invalid_names_and_uploads(tmp_path):
    lib = m.Library(tmp_path)
    for name in ('', '../x', 'a' * 41):
        with pytest.raises(ValueError):
            lib.put_category(name, '描述')
    category = lib.put_category('开心', '描述')
    with pytest.raises(ValueError):
        lib.add_image(category, b'not an image')
    corrupt = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=')
    with pytest.raises(ValueError):
        lib.add_image(category, corrupt)
    with pytest.raises(ValueError):
        lib.add_image('../outside', picture())


def test_stream_parser_handles_split_markers_and_unknown_ids():
    parser = m.MarkerParser({'abc123'})
    chunks = ['太好', '了！[[me', 'me:abc', '123]] 今', '天真棒', '[[meme:wrong]]']
    result = ''.join(parser.feed(chunk) for chunk in chunks) + parser.finish()
    assert result == '太好了！ 今天真棒'
    assert parser.selected == 'abc123'


def test_stream_parser_keeps_plain_text_and_hides_incomplete_marker():
    parser = m.MarkerParser({'abc123'})
    assert parser.feed('正常[文字') + parser.finish() == '正常[文字'
    parser = m.MarkerParser({'abc123'})
    assert parser.feed('回复[[meme:abc') + parser.finish() == '回复'


def test_connections_close_after_read_and_write(tmp_path):
    lib = m.Library(tmp_path)
    lib.put_category('开心', '庆祝')
    lib.categories()
    db = tmp_path / 'library.sqlite3'
    db.rename(tmp_path / 'renamed.sqlite3')
