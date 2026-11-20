import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard
import theme_panel


TOKEN = 'theme-panel-test-token'
AUTH = {'x-test-auth': TOKEN}
MUTATE = {
    'x-test-auth': TOKEN,
    'x-test-csrf': TOKEN,
    'Origin': 'http://testserver',
}
HTML = Path(__file__).resolve().parents[1] / 'dashboard.html'


def make_app(root):
    app = FastAPI()

    def authorized(req):
        if req.headers.get('x-test-auth') != TOKEN:
            raise HTTPException(401, '请登录')

    def mutate(req):
        authorized(req)
        if req.headers.get('x-test-csrf') != TOKEN:
            raise HTTPException(403, '请求来源无效')
        if req.headers.get('origin') != 'http://testserver':
            raise HTTPException(403, '请求来源无效')

    theme_panel.register(app, authorized=authorized, mutate=mutate, root=root)
    return app


def valid_config():
    return theme_panel.default_config()


def preset_path(root):
    return Path(root) / 'data' / 'theme-presets.json'


def snapshot(root):
    """Map every path under `root` to a content hash/size (directories: None)."""
    state = {}
    root = Path(root)
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            state[relative] = None
            continue
        content = path.read_bytes()
        state[relative] = (len(content), hashlib.sha256(content).hexdigest())
    return state


def test_routes_registered(tmp_path):
    app = make_app(tmp_path)
    paths = {route.path for route in app.routes}
    assert '/api/theme/presets' in paths
    assert '/api/theme/presets/apply' in paths
    assert '/api/theme/presets/delete' in paths


def test_dashboard_wires_theme_routes():
    routes = {
        (getattr(route, 'path', ''), method)
        for route in dashboard.app.routes
        for method in (getattr(route, 'methods', None) or ())
    }
    assert ('/api/theme/presets', 'GET') in routes
    assert ('/api/theme/presets', 'POST') in routes
    assert ('/api/theme/presets/apply', 'POST') in routes
    assert ('/api/theme/presets/delete', 'POST') in routes


def test_list_requires_auth_and_contains_builtin_presets(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        assert client.get('/api/theme/presets').status_code == 401
        response = client.get('/api/theme/presets', headers=AUTH)
    assert response.status_code == 200
    data = response.json()
    assert data['count'] == 4
    assert set(data['names']) == set(theme_panel.BUILTIN_PRESETS)
    assert data['invalid'] == []
    assert all(item['builtin'] is True and item['config'] for item in data['presets'])


def test_save_apply_delete_round_trip_and_atomic_write(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '我的夜间', 'config': valid_config(), 'overwrite': False}
        saved = client.post('/api/theme/presets', json=body, headers=MUTATE)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {'name': '我的夜间', 'builtin': False}

        path = preset_path(tmp_path)
        assert path.is_file()
        raw = path.read_text(encoding='utf-8')
        assert '"我的夜间"' in raw  # ensure_ascii=False
        assert json.loads(raw)['presets']['我的夜间']['misc']['radius'] is None
        assert not list(path.parent.glob('*.tmp'))
        if os.name != 'nt':
            assert path.stat().st_mode & 0o777 == 0o600

        listed = client.get('/api/theme/presets', headers=AUTH).json()
        assert '我的夜间' in listed['names']
        assert any(item['name'] == '我的夜间' and item['builtin'] is False for item in listed['presets'])

        applied = client.post('/api/theme/presets/apply', json={'name': '我的夜间'}, headers=MUTATE)
        assert applied.status_code == 200
        assert applied.json()['config'] == valid_config()

        deleted = client.post('/api/theme/presets/delete', json={'name': '我的夜间'}, headers=MUTATE)
        assert deleted.status_code == 200
        assert '我的夜间' not in client.get('/api/theme/presets', headers=AUTH).json()['names']
        assert '我的夜间' not in json.loads(path.read_text(encoding='utf-8'))['presets']


def test_apply_does_not_write_active_config(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        builtin_before = snapshot(tmp_path)
        builtin = client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=MUTATE)
        assert builtin.status_code == 200
        assert builtin.json()['config']['skin'] == 'default'
        assert snapshot(tmp_path) == builtin_before
        assert builtin_before == {}

        saved = client.post('/api/theme/presets', json={'name': '本地预设', 'config': valid_config()}, headers=MUTATE)
        assert saved.status_code == 200, saved.text
        before = snapshot(tmp_path)
        applied = client.post('/api/theme/presets/apply', json={'name': '本地预设'}, headers=MUTATE)
        assert applied.status_code == 200
        assert snapshot(tmp_path) == before
    assert set(before) == {'data', 'data/theme-presets.json'}


def test_write_requires_csrf_and_origin(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': 'x', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=AUTH).status_code == 403
        no_origin = {'x-test-auth': TOKEN, 'x-test-csrf': TOKEN}
        assert client.post('/api/theme/presets', json=body, headers=no_origin).status_code == 403
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=AUTH).status_code == 403
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=no_origin).status_code == 403


def test_delete_requires_csrf_and_origin(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        saved = client.post(
            '/api/theme/presets',
            json={'name': '待删除', 'config': valid_config()},
            headers=MUTATE,
        )
        assert saved.status_code == 200, saved.text
        no_origin = {'x-test-auth': TOKEN, 'x-test-csrf': TOKEN}
        assert client.post('/api/theme/presets/delete', json={'name': '待删除'}, headers=AUTH).status_code == 403
        assert client.post('/api/theme/presets/delete', json={'name': '待删除'}, headers=no_origin).status_code == 403
        deleted = client.post('/api/theme/presets/delete', json={'name': '待删除'}, headers=MUTATE)
        assert deleted.status_code == 200
        assert '待删除' not in client.get('/api/theme/presets', headers=AUTH).json()['names']


def test_overwrite_is_explicit(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '同名', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=MUTATE).status_code == 200
        duplicate = client.post('/api/theme/presets', json=body, headers=MUTATE)
        assert duplicate.status_code == 409
        overwrite = client.post('/api/theme/presets', json={**body, 'overwrite': True}, headers=MUTATE)
        assert overwrite.status_code == 200


def test_builtin_names_are_reserved(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        body = {'name': '暖纸默认', 'config': valid_config()}
        assert client.post('/api/theme/presets', json=body, headers=MUTATE).status_code == 409
        assert client.post('/api/theme/presets/delete', json={'name': '暖纸默认'}, headers=MUTATE).status_code == 409
        assert client.post('/api/theme/presets/apply', json={'name': '暖纸默认'}, headers=MUTATE).status_code == 200


@pytest.mark.parametrize('name', ['', '   ', 'a/b', 'a\\b', '.hidden', 'hidden.', 'a\nb', 'a:b', 'a' * 33])
def test_invalid_names_are_rejected(tmp_path, name):
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/theme/presets', json={'name': name, 'config': valid_config()}, headers=MUTATE)
    assert response.status_code in (400, 422)


@pytest.mark.parametrize('changes', [
    {'mode': 'sepia'},
    {'skin': 'unknown'},
    {'style': 'huge'},
    {'misc': {'radius': 25}},
    {'misc': {'radius': -1}},
    {'misc': {'radius': 1.5}},
    {'misc': {'sidebarLayout': 'overlay'}},
    {'misc': {'iconColor': 'red'}},
    {'background': {'kind': 'unknown'}},
    {'background': {'preset': 'unknown'}},
    {'background': {'scope': 'wide'}},
    {'background': {'kind': 'url', 'value': 'file:///tmp/a.png'}},
    {'background': {'kind': 'upload', 'value': '/etc/passwd'}},
    {'background': {'kind': 'url', 'value': 'https://example.com/a"b'}},
    {'background': {'kind': 'url', 'value': 'http://[::1'}},
    {'background': {'kind': 'url', 'value': 'https://[::1]:bad'}},
])
def test_invalid_configs_are_rejected(tmp_path, changes):
    config = valid_config()
    config.update(changes)
    with TestClient(make_app(tmp_path)) as client:
        response = client.post('/api/theme/presets', json={'name': 'bad', 'config': config}, headers=MUTATE)
    assert response.status_code == 400, response.text


def test_config_and_file_size_limits(tmp_path, monkeypatch):
    with TestClient(make_app(tmp_path)) as client:
        monkeypatch.setattr(theme_panel, 'MAX_CONFIG_BYTES', 10)
        response = client.post('/api/theme/presets', json={'name': 'big', 'config': valid_config()}, headers=MUTATE)
        assert response.status_code == 400
    with TestClient(make_app(tmp_path)) as client:
        monkeypatch.setattr(theme_panel, 'MAX_CONFIG_BYTES', 4096)
        monkeypatch.setattr(theme_panel, 'MAX_FILE_BYTES', 10)
        response = client.post('/api/theme/presets', json={'name': 'big', 'config': valid_config()}, headers=MUTATE)
        assert response.status_code == 409


def test_preset_count_limit(tmp_path):
    with TestClient(make_app(tmp_path)) as client:
        for index in range(theme_panel.MAX_PRESETS):
            response = client.post(
                '/api/theme/presets',
                json={'name': f'p{index}', 'config': valid_config()},
                headers=MUTATE,
            )
            assert response.status_code == 200
        response = client.post('/api/theme/presets', json={'name': 'overflow', 'config': valid_config()}, headers=MUTATE)
    assert response.status_code == 409


def test_corrupt_file_is_reported_and_not_overwritten(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{not json', encoding='utf-8')
    with TestClient(make_app(tmp_path)) as client:
        response = client.get('/api/theme/presets', headers=AUTH)
        saved = client.post('/api/theme/presets', json={'name': 'x', 'config': valid_config()}, headers=MUTATE)
    assert response.status_code == 500
    assert '主题预设文件' in response.json()['detail']
    assert saved.status_code == 500
    assert path.read_text(encoding='utf-8') == '{not json'


def test_non_utf8_file_is_reported_and_not_overwritten(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = b'{"version": 1, "presets": {}}\xff\xfe'
    path.write_bytes(original)
    with TestClient(make_app(tmp_path)) as client:
        listed = client.get('/api/theme/presets', headers=AUTH)
        saved = client.post('/api/theme/presets', json={'name': 'x', 'config': valid_config()}, headers=MUTATE)
    assert listed.status_code == 500
    assert 'UTF-8' in listed.json()['detail']
    assert saved.status_code == 500
    assert path.read_bytes() == original


def test_oversize_file_is_reported_and_not_overwritten(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    original = b'{"version": 1, "presets": {}}' + b' ' * theme_panel.MAX_FILE_BYTES
    path.write_bytes(original)
    with TestClient(make_app(tmp_path)) as client:
        listed = client.get('/api/theme/presets', headers=AUTH)
        saved = client.post('/api/theme/presets', json={'name': 'x', 'config': valid_config()}, headers=MUTATE)
    assert listed.status_code == 500
    assert '超过' in listed.json()['detail']
    assert saved.status_code == 500
    assert path.read_bytes() == original


def test_invalid_user_entry_is_listed_but_not_applied(tmp_path):
    path = preset_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 1, 'presets': {'坏预设': {'mode': 'sepia'}}}, ensure_ascii=False), encoding='utf-8')
    with TestClient(make_app(tmp_path)) as client:
        listed = client.get('/api/theme/presets', headers=AUTH)
        applied = client.post('/api/theme/presets/apply', json={'name': '坏预设'}, headers=MUTATE)
    assert listed.status_code == 200
    data = listed.json()
    assert data['invalid'] == ['坏预设']
    assert '坏预设' in data['names']
    assert all(item['name'] != '坏预设' for item in data['presets'])
    assert applied.status_code == 500


def test_dashboard_html_has_four_tabs_and_keeps_existing_ids():
    text = HTML.read_text(encoding='utf-8')
    for tab in ('presets', 'appearance', 'background', 'misc'):
        assert f'data-appearance-tab="{tab}"' in text
    for marker in (
        'id="appearancePopover"',
        'id="appearanceButton"',
        'id="appearanceButtonMobile"',
        'name="appearanceMode"',
        'name="appearanceSkin"',
        'name="appearanceStyle"',
        'id="appearanceBackgroundPreset"',
        'id="appearanceBackgroundUrl"',
        'id="appearanceBackgroundFile"',
        'id="appearanceBgState"',
        'id="themePresetName"',
        'id="themePresetList"',
        'id="appearanceRadius"',
        'name="appearanceSidebarLayout"',
        'name="appearanceIconColor"',
        'aria-label="主题设置"',
    ):
        assert marker in text
    assert text.count('id="appearancePopover"') == 1
    assert text.count('id="appearanceButton"') == 1


def test_radius_control_is_a_number_input():
    text = HTML.read_text(encoding='utf-8')
    assert '<select id="appearanceRadius"' not in text
    tag = re.search(r'<input[^>]*id="appearanceRadius"[^>]*>', text)
    assert tag, 'appearanceRadius input missing'
    markup = tag.group(0)
    for attribute in ('type="number"', 'min="0"', 'max="24"', 'step="1"', 'aria-label="圆角"'):
        assert attribute in markup


_NODE_STUB = r'''
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync(process.argv[1],'utf8');
class Element{
  constructor(tag='div'){
    this.tagName=tag;this.children=[];this.value='';this.dataset={};this.hidden=false;this._attrs={};this._text='';this._handlers={};
    this.style={values:{},setProperty(name,value){this.values[name]=value},removeProperty(name){delete this.values[name]},getPropertyValue(name){return this.values[name]||''}};
    this.classList={toggle(){},add(){},remove(){},contains(){return false}};
  }
  set textContent(value){this._text=String(value);this.children=[]}
  get textContent(){return this._text+this.children.map(child=>typeof child==='string'?child:child.textContent).join('')}
  set innerHTML(value){throw Error('innerHTML used')}
  append(...values){this.children.push(...values)}
  replaceChildren(...values){this._text='';this.children=[...values]}
  addEventListener(name,handler){(this._handlers[name]=this._handlers[name]||[]).push(handler)}
  setAttribute(name,value){this._attrs[name]=value;this[name]=value}
  getAttribute(name){return this._attrs[name]}
  removeAttribute(name){delete this._attrs[name]}
  querySelector(){const link=new Element('a');link.hash='';return link}
  querySelectorAll(){return []}
  contains(){return false}
  getContext(){return null}
  closest(){return null}
}
function makeContext(initial){
  const elements=new Map();
  const store=JSON.parse(JSON.stringify(initial||{}));
  const localStorage={
    getItem:key=>key in store?JSON.stringify(store[key]):null,
    setItem(key,value){store[key]=JSON.parse(value)},
    removeItem(key){delete store[key]},
  };
  const document={
    documentElement:new Element('html'),hidden:false,
    getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id)},
    createElement(tag){return new Element(tag)},
    createTextNode(text){return String(text)},
    querySelector(){return null},
    querySelectorAll(){return []},
    dispatchEvent(){},
  };
  const context={
    document,localStorage,
    window:{matchMedia:()=>({matches:false,addEventListener(){}}),addEventListener(){},requestAnimationFrame(){return 1},cancelAnimationFrame(){}},
    location:{protocol:'http:',hostname:'127.0.0.1'},
    getComputedStyle:()=>({getPropertyValue:()=>''}),
    fetch:async()=>({ok:false,status:500,json:async()=>({})}),
    console,URLSearchParams,setTimeout,clearTimeout,
    confirm(){return true},
    prompt(){return null},
  };
  context.window.document=document;
  vm.createContext(context);
  vm.runInContext(html.match(/<script id="appearanceBoot">([\s\S]*?)<\/script>/)[1],context);
  return {context,document};
}
function runApp(context){
  vm.runInContext(html.match(/<script id="appScript">([\s\S]*?)<\/script>/)[1],context);
}
'''


def test_client_migrates_legacy_local_storage_and_keeps_numeric_radius():
    script = _NODE_STUB + r'''
const {context}=makeContext({'qqbot.dashboard.appearance.v1':{mode:'dark',skin:'macos'}});
const migrated=context.window.Appearance.read();
context.localStorage.setItem('qqbot.dashboard.appearance.v1',JSON.stringify({misc:{radius:7}}));
const legacy=context.window.Appearance.read();
console.log(JSON.stringify({migrated,legacy}));
'''
    result = subprocess.run(['node', '-e', script, str(HTML)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['migrated']['misc'] == {'radius': None, 'sidebarLayout': 'docked', 'iconColor': 'colored'}
    assert payload['legacy']['misc'] == {'radius': 7, 'sidebarLayout': 'docked', 'iconColor': 'colored'}


def test_radius_control_and_preset_names_render_without_html():
    script = _NODE_STUB + r'''
const label='<img src=x onerror=alert(1)>';
const {context,document}=makeContext({'qqbot.dashboard.appearance.v1':{misc:{radius:7}}});
runApp(context);
context.syncAppearanceControls();
const input=document.getElementById('appearanceRadius');
const shown=input.value;
input.value='30';
(input._handlers.change||[]).forEach(handler=>handler());
const clamped=context.window.Appearance.read().misc.radius;
const clampedShown=input.value;
context.renderThemePresets({presets:[{name:label,builtin:false,config:{}}]});
const list=document.getElementById('themePresetList');
function walk(node,out){out.push(node);for(const child of node.children||[]){if(child&&typeof child==='object')walk(child,out)}return out}
const nodes=walk(list,[]);
console.log(JSON.stringify({
  shown:shown,
  clamped:clamped,
  clampedShown:clampedShown,
  labels:nodes.filter(node=>node.className==='appearance-preset-name').map(node=>node.textContent),
  images:nodes.filter(node=>String(node.tagName).toLowerCase()==='img').length,
  text:list.textContent,
}));
'''
    result = subprocess.run(['node', '-e', script, str(HTML)], capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['shown'] == '7'
    assert payload['clamped'] == 24
    assert payload['clampedShown'] == '24'
    assert payload['images'] == 0
    assert payload['labels'] == ['<img src=x onerror=alert(1)>']
    assert payload['text'].startswith('<img src=x onerror=alert(1)>')
