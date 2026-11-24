"""Per-user persistent memories, graph relations, and safe async extraction."""
import json
import math
import re
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent / 'data' / 'memories.sqlite3'
MAX_MEMORIES = 200
MAX_CANDIDATES = 50
MAX_OBSERVATIONS = 20
INSPECT_FLOOR = 0.55
CLASSIFY_TRIGGER = 0.60
NEAR_DUPLICATE = 0.90
RELATED_FLOOR = 0.72
CROSS_FLOOR = 0.78
CROSS_WINDOW = 1800
CROSS_LIMIT = 3
REFINE_FLOOR = 0.80
REFINE_LIMIT = 6


@contextmanager
def _connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript('''
        CREATE TABLE IF NOT EXISTS memory_users (user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            text TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, text));
        CREATE TABLE IF NOT EXISTS memory_vectors (
            memory_id INTEGER PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
            vector BLOB NOT NULL, model TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            text TEXT NOT NULL, source TEXT NOT NULL, similar_id INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id,text));
        CREATE TABLE IF NOT EXISTS memory_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, event_id TEXT NOT NULL,
            text TEXT, source TEXT NOT NULL, context TEXT NOT NULL, revision INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id,event_id));
        CREATE TABLE IF NOT EXISTS memory_preferences (user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS memory_job_origins (
            job_id INTEGER PRIMARY KEY REFERENCES memory_jobs(id) ON DELETE CASCADE,
            group_id TEXT, bot_id TEXT NOT NULL DEFAULT '',
            origin_ts INTEGER NOT NULL, participants TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS memory_observations (
            memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL, source TEXT NOT NULL, group_id TEXT,
            bot_id TEXT NOT NULL DEFAULT '', origin_ts INTEGER NOT NULL,
            participants TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(memory_id, event_id));
        CREATE TABLE IF NOT EXISTS memory_candidate_reasons (
            candidate_id INTEGER PRIMARY KEY REFERENCES memory_candidates(id) ON DELETE CASCADE,
            reason TEXT NOT NULL, weight REAL NOT NULL DEFAULT 0.5);
        CREATE TABLE IF NOT EXISTS memory_candidate_origins (
            candidate_id INTEGER PRIMARY KEY REFERENCES memory_candidates(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL, group_id TEXT, bot_id TEXT NOT NULL DEFAULT '',
            origin_ts INTEGER NOT NULL, participants TEXT NOT NULL DEFAULT '[]');
        CREATE TABLE IF NOT EXISTS memory_edges (
            a_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            b_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, weight REAL NOT NULL, origin TEXT NOT NULL, scope TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(a_id, b_id), CHECK(a_id < b_id));
        CREATE INDEX IF NOT EXISTS memory_edges_b ON memory_edges(b_id);
        CREATE INDEX IF NOT EXISTS memory_observations_context ON memory_observations(group_id, origin_ts);
        CREATE TABLE IF NOT EXISTS memory_people (
            user_id TEXT NOT NULL, group_id TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, group_id));
        CREATE TABLE IF NOT EXISTS memory_graph_state (
            user_id TEXT PRIMARY KEY, memory_count INTEGER NOT NULL DEFAULT 0);
    ''')
    try:
        with con:
            yield con
    finally:
        con.close()


def _safe_id(value):
    text = str(value or '')
    return text if re.fullmatch(r'[A-Za-z0-9_-]{1,64}', text) else ''


def _clean_name(value):
    text = ''.join(c for c in str(value or '') if c.isprintable()).strip()
    if text in ('_default', 'default'):
        return ''
    return text[:32]


def _clean_fact(text):
    text = ' '.join(str(text or '').split())
    return text if 4 <= len(text) <= 120 else None


def _clean_origin(origin, owner_id):
    if not isinstance(origin, dict):
        return None
    try:
        ts = int(origin.get('origin_ts'))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    group = origin.get('group_id')
    group = _safe_id(group) if group not in (None, '', '-1') else None
    bot = _safe_id(origin.get('bot_id')) or ''
    raw_participants = origin.get('participants')
    if not isinstance(raw_participants, (list, tuple)):
        raw_participants = ()
    participants = []
    for raw in raw_participants:
        pid = _safe_id(raw)
        if pid and pid != str(owner_id) and pid not in participants:
            participants.append(pid)
        if len(participants) >= 12:
            break
    return {'group_id': group or None, 'bot_id': bot[:64], 'origin_ts': ts,
            'participants': participants}


def _strip_fence(raw):
    return re.sub(r'^\s*```(?:json)?\s*|\s*```\s*$', '', str(raw or '').strip())


def _as_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _relation_index(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _clamp_weight(value, default):
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(weight):
        return default
    return min(1.0, max(0.5, weight))


def _memory_rows(con, user_id):
    return [dict(r) for r in con.execute('''
        SELECT m.id, m.text, v.vector FROM memories m
        LEFT JOIN memory_vectors v ON m.id=v.memory_id AND v.model='siliconflow-bge-m3-v1'
        WHERE m.user_id=?''', (str(user_id),))]


def _has_missing_vectors(con, user_id):
    return con.execute('''
        SELECT 1 FROM memories m
        LEFT JOIN memory_vectors v ON m.id=v.memory_id AND v.model='siliconflow-bge-m3-v1'
        WHERE m.user_id=? AND v.memory_id IS NULL LIMIT 1''', (str(user_id),)).fetchone() is not None


def _invalidate_graph(con, user_id):
    con.execute('DELETE FROM memory_graph_state WHERE user_id=?', (str(user_id),))


def revision(path, user_id):
    with _connect(path) as con:
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (str(user_id),)).fetchone()
        return row['revision'] if row else 0


def list_memories(path, user_id):
    with _connect(path) as con:
        return [dict(r) for r in con.execute(
            'SELECT id, text, source FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?',
            (str(user_id), MAX_MEMORIES))]


def parse_extraction(raw):
    try:
        value = json.loads(raw)
        values = value.get('memories', []) if isinstance(value, dict) else []
        if not isinstance(values, list):
            return []
        result = []
        for v in values[:3]:
            if isinstance(v, str):
                text = ' '.join(v.split())
                if 4 <= len(text) <= 120 and text not in result:
                    result.append(text)
        return result
    except (ValueError, TypeError):
        return []


def store(path, user_id, facts, source, expected_revision):
    user_id = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (user_id,)).fetchone()
        if (row['revision'] if row else 0) != expected_revision:
            return 0
        count = con.execute('SELECT COUNT(*) FROM memories WHERE user_id=?', (user_id,)).fetchone()[0]
        inserted = 0
        for fact in facts:
            if count >= MAX_MEMORIES:
                break
            cur = con.execute('INSERT OR IGNORE INTO memories(user_id,text,source) VALUES(?,?,?)',
                              (user_id, fact, source))
            inserted += cur.rowcount
            count += cur.rowcount
        if inserted:
            _invalidate_graph(con, user_id)
        return inserted


def forget(path, user_id, target):
    user_id = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        if target == '全部':
            cur = con.execute('DELETE FROM memories WHERE user_id=?', (user_id,))
            con.execute('DELETE FROM memory_candidates WHERE user_id=?', (user_id,))
        elif str(target).isdigit():
            cur = con.execute('DELETE FROM memories WHERE user_id=? AND id=?', (user_id, int(target)))
            con.execute('DELETE FROM memory_candidates WHERE user_id=? AND similar_id=?', (user_id, int(target)))
        else:
            return False
        con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (user_id,))
        con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (user_id,))
        _invalidate_graph(con, user_id)
        return cur.rowcount > 0


def correct(path, user_id, memory_id, text):
    if not str(memory_id).isdigit() or not 4 <= len(text.strip()) <= 120:
        return False
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        try:
            cur = con.execute('UPDATE memories SET text=? WHERE user_id=? AND id=?', (text.strip(), str(user_id), int(memory_id)))
        except sqlite3.IntegrityError:
            return False
        if cur.rowcount:
            con.execute('DELETE FROM memory_vectors WHERE memory_id=?', (int(memory_id),))
            con.execute('DELETE FROM memory_observations WHERE memory_id=?', (int(memory_id),))
            con.execute('DELETE FROM memory_edges WHERE a_id=? OR b_id=?', (int(memory_id), int(memory_id)))
            con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (str(user_id),))
            con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (str(user_id),))
            _invalidate_graph(con, user_id)
        return bool(cur.rowcount)


def render_memories(memories, in_group=False):
    if not memories:
        return ''
    intro = '\n\n以下是与当前用户有关的长期记忆，只是参考，可能过时或有误；用户纠正时以用户说法为准。'
    if in_group:
        intro += ' 标注私聊的内容只作理解背景，不要主动在群里透露或复述私聊细节。'
    lines = [f"- {'[私聊] ' if m['source'] == 'private' else ''}{m['text']}" for m in memories]
    return intro + '\n' + '\n'.join(lines)


def _pack(vector):
    values = [float(x) for x in vector]
    if not values or len(values) > 4096 or any(not math.isfinite(x) for x in values):
        raise ValueError('invalid vector')
    norm = math.sqrt(sum(x * x for x in values))
    if norm == 0:
        raise ValueError('zero vector')
    return struct.pack('<%sf' % len(values), *(x / norm for x in values))


def _similarity(a, b):
    if not a or not b or len(a) != len(b) or len(a) % 4:
        return -1.0
    count = len(a) // 4
    return sum(x * y for x, y in zip(struct.unpack('<%sf' % count, a), struct.unpack('<%sf' % count, b)))


def set_vector(path, user_id, memory_id, expected_text, vector):
    packed = _pack(vector)
    with _connect(path) as con:
        row = con.execute('SELECT id FROM memories WHERE id=? AND user_id=? AND text=?', (memory_id, str(user_id), expected_text)).fetchone()
        if not row:
            return False
        con.execute('INSERT OR REPLACE INTO memory_vectors(memory_id,vector,model) VALUES(?,?,?)', (memory_id, packed, 'siliconflow-bge-m3-v1'))
        return True


def missing_vectors(path, user_id, limit=20):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('''SELECT m.id,m.text FROM memories m LEFT JOIN memory_vectors v
            ON m.id=v.memory_id AND v.model='siliconflow-bge-m3-v1' WHERE m.user_id=? AND v.memory_id IS NULL ORDER BY m.id LIMIT ?''', (str(user_id),limit))]


def recall(path, user_id, vector, query, limit=5):
    packed = _pack(vector) if vector is not None else None
    with _connect(path) as con:
        rows = con.execute('''SELECT m.id,m.text,m.source,v.vector FROM memories m
            LEFT JOIN memory_vectors v ON m.id=v.memory_id AND v.model='siliconflow-bge-m3-v1'
            WHERE m.user_id=?''', (str(user_id),)).fetchall()
    scored = []
    for row in rows:
        score = _similarity(packed, row['vector']) if packed is not None else -1
        text = str(query)
        ignored = {'什么', '用户', '他们', '知道', '你好', '今天', '可以', '这些', '我的'}
        lexical = any(text[i:i+2] not in ignored and text[i:i+2].strip() == text[i:i+2]
                      and text[i:i+2] in row['text'] for i in range(max(0, len(text)-1)))
        if score >= 0.60 or (lexical and (packed is None or row['vector'] is None or score >= 0.55)):
            scored.append((max(score, 0.61 if lexical else -1), dict(row)))
    scored.sort(key=lambda x: (-x[0], -x[1]['id']))
    return [{k: v for k, v in row.items() if k != 'vector'} for _, row in scored[:limit]]


def _add_observation(con, memory_id, event_id, source, origin):
    if not event_id or not origin:
        return False
    con.execute('''INSERT OR IGNORE INTO memory_observations
        (memory_id,event_id,source,group_id,bot_id,origin_ts,participants)
        VALUES(?,?,?,?,?,?,?)''',
        (int(memory_id), str(event_id), source, origin['group_id'], origin['bot_id'],
         int(origin['origin_ts']), json.dumps(origin['participants'], ensure_ascii=False)))
    con.execute('''DELETE FROM memory_observations WHERE memory_id=? AND rowid NOT IN (
        SELECT rowid FROM memory_observations WHERE memory_id=?
        ORDER BY origin_ts DESC, created_at DESC, rowid DESC LIMIT ?)''',
        (int(memory_id), int(memory_id), MAX_OBSERVATIONS))
    return True


def _add_candidate(con, user_id, text, source, similar_id, reason, weight, event_id, origin):
    uid = str(user_id)
    row = con.execute('SELECT id FROM memory_candidates WHERE user_id=? AND text=?', (uid, text)).fetchone()
    if row:
        cid = row['id']
    else:
        count = con.execute('SELECT COUNT(*) FROM memory_candidates WHERE user_id=?', (uid,)).fetchone()[0]
        if count >= MAX_CANDIDATES:
            return None
        cur = con.execute('INSERT INTO memory_candidates(user_id,text,source,similar_id) VALUES(?,?,?,?)',
                          (uid, text, source, similar_id))
        cid = cur.lastrowid
    con.execute('INSERT OR IGNORE INTO memory_candidate_reasons(candidate_id,reason,weight) VALUES(?,?,?)',
                (cid, reason, weight))
    if event_id and origin:
        con.execute('''INSERT OR IGNORE INTO memory_candidate_origins
            (candidate_id,event_id,group_id,bot_id,origin_ts,participants)
            VALUES(?,?,?,?,?,?)''',
            (cid, str(event_id), origin['group_id'], origin['bot_id'],
             int(origin['origin_ts']), json.dumps(origin['participants'], ensure_ascii=False)))
    return cid


def _upsert_vector_edge(con, a, b, weight):
    a, b = sorted((int(a), int(b)))
    if a == b:
        return
    row = con.execute('SELECT origin,weight FROM memory_edges WHERE a_id=? AND b_id=?', (a, b)).fetchone()
    if row is None:
        con.execute('''INSERT INTO memory_edges(a_id,b_id,kind,weight,origin,scope)
            VALUES(?,?,?,?,?,?)''', (a, b, 'related', float(weight), 'vector', 'same'))
    elif row['origin'] == 'vector' and float(weight) > row['weight']:
        con.execute('UPDATE memory_edges SET weight=? WHERE a_id=? AND b_id=?', (float(weight), a, b))


def _upsert_model_edge(con, a, b, kind, weight, origin='model', scope='same'):
    a, b = sorted((int(a), int(b)))
    if a == b:
        return
    con.execute('''INSERT INTO memory_edges(a_id,b_id,kind,weight,origin,scope)
        VALUES(?,?,?,?,?,?) ON CONFLICT(a_id,b_id) DO UPDATE SET
        kind=excluded.kind, weight=excluded.weight, origin=excluded.origin, scope=excluded.scope''',
        (a, b, kind, float(weight), origin, scope))


def _link_related(con, memory_id, packed, rows):
    scored = []
    for row in rows:
        if row['id'] == memory_id or not row.get('vector'):
            continue
        score = _similarity(packed, row['vector'])
        if score >= RELATED_FLOOR:
            scored.append((score, row['id']))
    for score, other in sorted(scored, key=lambda item: (-item[0], -item[1]))[:4]:
        _upsert_vector_edge(con, memory_id, other, score)


def _link_cross(con, user_id, memory_id, packed, origin):
    if not origin or not origin.get('group_id') or not origin.get('participants'):
        return
    participants = origin['participants']
    marks = ','.join('?' for _ in participants)
    rows = con.execute(f'''SELECT m.id,m.user_id,v.vector FROM memories m
        JOIN memory_vectors v ON v.memory_id=m.id AND v.model='siliconflow-bge-m3-v1'
        WHERE m.user_id IN ({marks}) AND m.user_id != ?
          AND EXISTS (SELECT 1 FROM memory_observations o
              WHERE o.memory_id=m.id AND o.group_id=? AND ABS(o.origin_ts-?) <= ?)''',
        (*participants, str(user_id), origin['group_id'], int(origin['origin_ts']),
         CROSS_WINDOW)).fetchall()
    scored = []
    for row in rows:
        if row['id'] == memory_id:
            continue
        score = _similarity(packed, row['vector'])
        if score >= CROSS_FLOOR:
            scored.append((score, row['id']))
    for score, other in sorted(scored, key=lambda item: (-item[0], -item[1]))[:CROSS_LIMIT]:
        a, b = sorted((int(memory_id), int(other)))
        con.execute('''INSERT OR IGNORE INTO memory_edges(a_id,b_id,kind,weight,origin,scope)
            VALUES(?,?,?,?,?,?)''', (a, b, 'shared_context', float(score), 'cross', 'cross'))


def inspect_facts(path, user_id, facts, vectors):
    """Return per-fact exact/capacity/near-duplicate/similar metadata."""
    if len(facts) != len(vectors):
        raise ValueError('facts/vectors mismatch')
    packed = [_pack(vector) for vector in vectors]
    with _connect(path) as con:
        rows = _memory_rows(con, user_id)
    count = len(rows)
    result = []
    for text, vector in zip(facts, packed):
        text = _clean_fact(text)
        if text is None:
            raise ValueError('invalid fact')
        exact = next((row['id'] for row in rows if row['text'] == text), None)
        scored = []
        for row in rows:
            score = _similarity(vector, row['vector'])
            if score >= INSPECT_FLOOR:
                scored.append((score, row['id'], row['text']))
        scored.sort(key=lambda item: (-item[0], -item[1]))
        near = next((item[1] for item in scored if item[0] >= NEAR_DUPLICATE), None)
        result.append({
            'text': text,
            'exact_id': exact,
            'capacity': count >= MAX_MEMORIES,
            'near_duplicate': near,
            'similar': [{'id': mid, 'text': mtext, 'score': round(score, 4)}
                        for score, mid, mtext in scored[:4]],
        })
    return result


def apply_facts(path, user_id, source, expected_revision, items, event_id='', origin=None):
    """Apply observe/candidate/save decisions in one revision-guarded transaction."""
    uid = str(user_id)
    if source not in ('private', 'group'):
        return [{'status': 'invalid', 'id': None} for _ in items]
    clean_origin = _clean_origin(origin, uid)
    results = [{'status': 'invalid', 'id': None} for _ in items]
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (uid,)).fetchone()
        if (row['revision'] if row else 0) != expected_revision:
            return [{'status': 'stale', 'id': None} for _ in items]
        rows = _memory_rows(con, uid)
        count = len(rows)
        by_id = {item['id']: item for item in rows}
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            action = item.get('action')
            text = _clean_fact(item.get('text'))
            if action not in ('save', 'candidate', 'observe') or text is None:
                continue
            if action == 'observe':
                target = by_id.get(_as_int(item.get('memory_id')))
                if target is None or target['text'] != text:
                    results[index] = {'status': 'missing', 'id': None}
                    continue
                _add_observation(con, target['id'], event_id, source, clean_origin)
                results[index] = {'status': 'observed', 'id': target['id']}
                continue
            existing = next((row for row in rows if row['text'] == text), None)
            if existing is not None:
                _add_observation(con, existing['id'], event_id, source, clean_origin)
                results[index] = {'status': 'observed', 'id': existing['id']}
                continue
            if action == 'candidate':
                similar_id = _as_int(item.get('similar_id'))
                if similar_id not in by_id:
                    similar_id = None
                reason = item.get('reason')
                if reason not in ('similar', 'conflict', 'capacity'):
                    reason = 'similar'
                weight = _clamp_weight(item.get('weight'), 0.5)
                cid = _add_candidate(con, uid, text, source, similar_id, reason, weight, event_id, clean_origin)
                results[index] = ({'status': 'candidate', 'id': cid} if cid is not None
                                  else {'status': 'candidate_full', 'id': None})
                continue
            if count >= MAX_MEMORIES:
                cid = _add_candidate(con, uid, text, source, None, 'capacity', 0.5, event_id, clean_origin)
                results[index] = ({'status': 'candidate', 'id': cid} if cid is not None
                                  else {'status': 'candidate_full', 'id': None})
                continue
            if not isinstance(item.get('vector'), list):
                raise ValueError('invalid vector')
            packed = _pack(item['vector'])
            cur = con.execute('INSERT INTO memories(user_id,text,source) VALUES(?,?,?)', (uid, text, source))
            mid = cur.lastrowid
            con.execute('INSERT INTO memory_vectors(memory_id,vector,model) VALUES(?,?,?)',
                        (mid, packed, 'siliconflow-bge-m3-v1'))
            _add_observation(con, mid, event_id, source, clean_origin)
            _link_related(con, mid, packed, rows)
            allowed = {_as_int(value) for value in (item.get('allowed') or [])}
            for link in item.get('links') or []:
                if not isinstance(link, dict):
                    continue
                target_id = _as_int(link.get('target_id'))
                kind = link.get('kind')
                if target_id not in allowed or target_id not in by_id or target_id == mid:
                    continue
                if kind not in ('corroborates', 'refines', 'related', 'unrelated'):
                    continue
                _upsert_model_edge(con, mid, target_id, kind, _clamp_weight(link.get('weight'), 0.7))
            new_row = {'id': mid, 'text': text, 'vector': packed}
            rows.append(new_row)
            by_id[mid] = new_row
            count += 1
            _link_cross(con, uid, mid, packed, clean_origin)
            results[index] = {'status': 'saved', 'id': mid}
        if any(result['status'] == 'saved' for result in results):
            _invalidate_graph(con, uid)
        return results


def list_candidates(path, user_id):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('''
            SELECT c.id,c.text,c.source,c.similar_id,
                   COALESCE(r.reason,'similar') AS reason,
                   COALESCE(r.weight,0.5) AS confidence,
                   sm.text AS similar_text
            FROM memory_candidates c
            LEFT JOIN memory_candidate_reasons r ON r.candidate_id=c.id
            LEFT JOIN memories sm ON sm.id=c.similar_id AND sm.user_id=c.user_id
            WHERE c.user_id=? ORDER BY c.id DESC LIMIT 50''', (str(user_id),))]


def resolve_candidate(path, user_id, candidate_id, action):
    if not str(candidate_id).isdigit() or action not in ('accept', 'replace', 'discard'):
        return False
    uid = str(user_id)
    cid = int(candidate_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT * FROM memory_candidates WHERE id=? AND user_id=?', (cid, uid)).fetchone()
        if not row:
            return False
        reason_row = con.execute('SELECT reason,weight FROM memory_candidate_reasons WHERE candidate_id=?', (cid,)).fetchone()
        reason = reason_row['reason'] if reason_row else 'similar'
        weight = _clamp_weight(reason_row['weight'] if reason_row else 0.5, 0.5)
        origin_row = con.execute('SELECT * FROM memory_candidate_origins WHERE candidate_id=?', (cid,)).fetchone()
        origin = None
        if origin_row:
            try:
                participants = json.loads(origin_row['participants'] or '[]')
            except (TypeError, ValueError):
                participants = []
            if not isinstance(participants, list):
                participants = []
            origin = {'group_id': origin_row['group_id'], 'bot_id': origin_row['bot_id'] or '',
                      'origin_ts': int(origin_row['origin_ts']), 'participants': participants}
        event_id = origin_row['event_id'] if origin_row else ''
        if action == 'accept':
            existing = con.execute('SELECT id FROM memories WHERE user_id=? AND text=?', (uid, row['text'])).fetchone()
            if existing:
                mid = existing['id']
            else:
                count = con.execute('SELECT COUNT(*) FROM memories WHERE user_id=?', (uid,)).fetchone()[0]
                if count >= MAX_MEMORIES:
                    return False
                cur = con.execute('INSERT INTO memories(user_id,text,source) VALUES(?,?,?)',
                                  (uid, row['text'], row['source']))
                mid = cur.lastrowid
            _add_observation(con, mid, event_id, row['source'], origin)
            target = None
            if row['similar_id'] is not None:
                target = con.execute('SELECT id FROM memories WHERE user_id=? AND id=?',
                                     (uid, row['similar_id'])).fetchone()
            if target and target['id'] != mid:
                kind = 'contradicts' if reason == 'conflict' else 'corroborates' if reason == 'similar' else None
                if kind:
                    _upsert_model_edge(con, mid, target['id'], kind, weight)
        elif action == 'replace':
            try:
                cur = con.execute("UPDATE memories SET text=?, source=CASE WHEN source='private' OR ?='private' THEN 'private' ELSE 'group' END WHERE id=? AND user_id=?",
                                  (row['text'], row['source'], row['similar_id'], uid))
            except sqlite3.IntegrityError:
                return False
            if not cur.rowcount:
                return False
            target_id = int(row['similar_id'])
            con.execute('DELETE FROM memory_vectors WHERE memory_id=?', (target_id,))
            con.execute('DELETE FROM memory_observations WHERE memory_id=?', (target_id,))
            con.execute('DELETE FROM memory_edges WHERE a_id=? OR b_id=?', (target_id, target_id))
            _add_observation(con, target_id, event_id, row['source'], origin)
        con.execute('DELETE FROM memory_candidates WHERE id=? AND user_id=?', (cid, uid))
        con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (uid,))
        con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (uid,))
        if action in ('accept', 'replace'):
            _invalidate_graph(con, uid)
        return True


def enqueue(path, user_id, text, source, event_id, context, expected_revision=None, origin=None):
    uid = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        pref = con.execute('SELECT enabled FROM memory_preferences WHERE user_id=?', (uid,)).fetchone()
        if pref is not None and not pref['enabled']:
            return None
        con.execute("DELETE FROM memory_jobs WHERE status='done' AND created_at < datetime('now','-7 days')")
        rev = con.execute('SELECT revision FROM memory_users WHERE user_id=?', (uid,)).fetchone()
        current_revision = rev['revision'] if rev else 0
        if expected_revision is not None and expected_revision != current_revision:
            return None
        con.execute('INSERT OR IGNORE INTO memory_jobs(user_id,event_id,text,source,context,revision) VALUES(?,?,?,?,?,?)',
                    (uid,str(event_id),text[:2000],source,json.dumps(context[-3:],ensure_ascii=False),current_revision))
        job_id = con.execute('SELECT id FROM memory_jobs WHERE user_id=? AND event_id=?', (uid,str(event_id))).fetchone()[0]
        clean_origin = _clean_origin(origin, uid)
        if clean_origin:
            con.execute('''INSERT OR IGNORE INTO memory_job_origins
                (job_id,group_id,bot_id,origin_ts,participants) VALUES(?,?,?,?,?)''',
                (job_id, clean_origin['group_id'], clean_origin['bot_id'], clean_origin['origin_ts'],
                 json.dumps(clean_origin['participants'], ensure_ascii=False)))
        return job_id


def pending_jobs(path):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('''
            SELECT j.*, o.group_id AS origin_group_id, o.bot_id AS origin_bot_id,
                   o.origin_ts AS origin_ts, o.participants AS origin_participants
            FROM memory_jobs j LEFT JOIN memory_job_origins o ON o.job_id=j.id
            WHERE j.status='pending' ORDER BY j.id LIMIT 32''')]


def mark_output(path, job_id):
    """Prevent automatic replay after a model request begins."""
    with _connect(path) as con:
        return con.execute("UPDATE memory_jobs SET status='inflight',last_error='ModelRequestInFlight' WHERE id=? AND status='pending'", (job_id,)).rowcount == 1


def recover_inflight(path):
    """Require manual retry after interruption of a possibly sent request."""
    with _connect(path) as con:
        return con.execute("UPDATE memory_jobs SET status='review',last_error='InterruptedRequest' WHERE status='inflight'").rowcount


def defer_job(path, job_id, error):
    """Keep a job pending when no model request was sent."""
    with _connect(path) as con:
        con.execute("UPDATE memory_jobs SET last_error=? WHERE id=? AND status='pending'", (str(error)[:80], job_id))


def fail_job(path, job_id, error):
    """记一次失败。

    发送前后分开记：可能已经把内容发出去过的（inflight / review）只进 `review`，
    等用户明确判断；发送前就失败的仍走原来的三次上限，落到 `failed`。
    两者都不会被按创建时间的清理删掉（见 `enqueue`）。
    """
    with _connect(path) as con:
        con.execute(
            "UPDATE memory_jobs SET attempts=attempts+1,last_error=?,status=CASE "
            "WHEN status='inflight' THEN 'review' "
            "WHEN attempts+1>=3 THEN 'failed' ELSE 'pending' END "
            "WHERE id=? AND status IN ('pending','inflight')",
            (str(error)[:80], job_id))


def complete_job(path, job_id):
    with _connect(path) as con:
        con.execute("UPDATE memory_jobs SET status='done',text=NULL,context='[]',last_error=NULL WHERE id=?", (job_id,))
        con.execute('DELETE FROM memory_job_origins WHERE job_id=?', (job_id,))


def is_enabled(path, user_id):
    with _connect(path) as con:
        row = con.execute('SELECT enabled FROM memory_preferences WHERE user_id=?', (str(user_id),)).fetchone()
        return bool(row['enabled']) if row else True


def set_enabled(path, user_id, enabled):
    uid = str(user_id)
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('INSERT INTO memory_preferences(user_id,enabled) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled', (uid, int(bool(enabled))))
        if not enabled:
            con.execute('INSERT INTO memory_users(user_id,revision) VALUES(?,1) ON CONFLICT(user_id) DO UPDATE SET revision=revision+1', (uid,))
            con.execute("DELETE FROM memory_jobs WHERE user_id=? AND status != 'done'", (uid,))


def job_status(path, user_id):
    with _connect(path) as con:
        counts = {r['status']: r['n'] for r in con.execute("SELECT status,COUNT(*) n FROM memory_jobs WHERE user_id=? AND status!='done' GROUP BY status", (str(user_id),))}
        if counts.get('inflight'):
            counts['pending'] = counts.get('pending', 0) + counts.pop('inflight')
        # review = 可能已经发出去、只能人来定的任务，和失败一起报出来
        if counts.get('review'):
            counts['failed'] = counts.get('failed', 0) + counts.pop('review')
        return counts


def retry_failed(path, user_id):
    """用户明确要求重试：failed 与 review 都重新排队（唯一的重放入口）。"""
    with _connect(path) as con:
        cur = con.execute("UPDATE memory_jobs SET status='pending',attempts=0,last_error=NULL WHERE user_id=? AND status IN ('failed','review')", (str(user_id),))
        return cur.rowcount


def remember_people(path, group_id, names):
    gid = _safe_id(group_id or '-1')
    if not gid or not isinstance(names, dict):
        return 0
    rows = []
    for user_id, name in names.items():
        uid = _safe_id(user_id)
        clean = _clean_name(name)
        if uid and clean:
            rows.append((uid, gid, clean))
    if not rows:
        return 0
    with _connect(path) as con:
        con.executemany('''INSERT INTO memory_people(user_id,group_id,display_name) VALUES(?,?,?)
            ON CONFLICT(user_id,group_id) DO UPDATE SET
            display_name=excluded.display_name, updated_at=CURRENT_TIMESTAMP''', rows)
    return len(rows)


def parse_relations(raw, allowed):
    try:
        data = json.loads(_strip_fence(raw))
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get('links'), list):
        return []
    priority = {'contradicts': 0, 'corroborates': 1, 'refines': 2, 'related': 3, 'unrelated': 4}
    chosen = {}
    for link in data['links'][:20]:
        if not isinstance(link, dict):
            continue
        src, dst = _relation_index(link.get('from')), _relation_index(link.get('to'))
        if src is None or dst is None:
            continue
        if src not in allowed or dst not in (allowed[src] or ()):
            continue
        kind = link.get('kind')
        if not isinstance(kind, str) or kind not in priority:
            continue
        weight = _clamp_weight(link.get('weight'), 0.7)
        key = (src, dst)
        candidate = (priority[kind], -weight, kind, weight)
        if key not in chosen or candidate < chosen[key]:
            chosen[key] = candidate
    return [{'from': src, 'to': dst, 'kind': kind, 'weight': weight}
            for (src, dst), (_, _, kind, weight) in sorted(chosen.items())]


def describe_user(path, user_id):
    uid = str(user_id)
    with _connect(path) as con:
        mem_rows = [dict(r) for r in con.execute(
            'SELECT id,text,source,created_at FROM memories WHERE user_id=? ORDER BY id DESC LIMIT ?',
            (uid, MAX_MEMORIES))]
        ids = [row['id'] for row in mem_rows]
        ids_set = set(ids)
        observations = []
        edges = []
        other_rows = []
        people_rows = []
        if ids:
            marks = ','.join('?' for _ in ids)
            observations = [dict(r) for r in con.execute(
                f'''SELECT memory_id,source,group_id,bot_id,origin_ts,participants
                    FROM memory_observations WHERE memory_id IN ({marks})
                    ORDER BY memory_id, origin_ts DESC, created_at DESC''', ids)]
            edges = [dict(r) for r in con.execute(
                f'''SELECT a_id,b_id,kind,weight,origin,scope FROM memory_edges
                    WHERE a_id IN ({marks}) OR b_id IN ({marks})''', ids + ids)]
            other_ids = sorted({edge['b_id'] for edge in edges if edge['b_id'] not in ids_set} |
                               {edge['a_id'] for edge in edges if edge['a_id'] not in ids_set})
            if other_ids:
                other_marks = ','.join('?' for _ in other_ids)
                other_rows = [dict(r) for r in con.execute(
                    f'SELECT id,user_id,text FROM memories WHERE id IN ({other_marks})', other_ids)]
            participant_ids = set()
            for obs in observations:
                try:
                    raw = json.loads(obs.get('participants') or '[]')
                except (TypeError, ValueError):
                    raw = []
                if not isinstance(raw, list):
                    raw = []
                obs['participants'] = [pid for pid in (_safe_id(value) for value in raw) if pid]
                participant_ids.update(obs['participants'])
            if participant_ids:
                people_marks = ','.join('?' for _ in participant_ids)
                people_rows = [dict(r) for r in con.execute(
                    f'SELECT user_id,group_id,display_name FROM memory_people WHERE user_id IN ({people_marks})',
                    sorted(participant_ids))]
    names_by_group = {(row['user_id'], row['group_id']): row['display_name'] for row in people_rows}
    names_any = {}
    for row in people_rows:
        names_any.setdefault(row['user_id'], row['display_name'])
    obs_by_memory = {}
    for obs in observations:
        obs_by_memory.setdefault(obs['memory_id'], []).append(obs)
    all_meta = {row['id']: row for row in mem_rows}
    all_meta.update({row['id']: row for row in other_rows})
    relations = {mid: [] for mid in ids}
    relation_order = {'contradicts': 0, 'corroborates': 1, 'refines': 2, 'related': 3, 'shared_context': 4}
    same_edges = []
    cross_edges = []
    for edge in edges:
        if edge['kind'] == 'unrelated':
            continue
        if edge['scope'] == 'cross':
            cross_edges.append(edge)
        elif edge['a_id'] in ids_set and edge['b_id'] in ids_set:
            same_edges.append(edge)
        for own, other in ((edge['a_id'], edge['b_id']), (edge['b_id'], edge['a_id'])):
            if own not in relations:
                continue
            meta = all_meta.get(other)
            if not meta:
                continue
            relations[own].append({
                'id': other,
                'user_id': meta.get('user_id', uid),
                'text': meta['text'],
                'kind': edge['kind'],
                'weight': round(float(edge['weight']), 4),
                'origin': edge['origin'],
                'scope': edge['scope'],
            })
    for items in relations.values():
        items.sort(key=lambda rel: (relation_order.get(rel['kind'], 9), -rel['weight'], -rel['id']))
    memories = []
    for row in mem_rows:
        obs = obs_by_memory.get(row['id'], [])
        origin = None
        if obs:
            latest = obs[0]
            group_key = latest['group_id'] or '-1'
            participants = [
                {'id': pid, 'name': names_by_group.get((pid, group_key)) or names_any.get(pid, '')}
                for pid in latest['participants']
            ]
            origin = {'group_id': latest['group_id'], 'bot_id': latest['bot_id'],
                      'origin_ts': int(latest['origin_ts']), 'participants': participants}
        memories.append({**row, 'origin': origin, 'observation_count': len(obs),
                         'relations': relations.get(row['id'], [])})
    stats = {
        'memories': len(mem_rows),
        'relations': len(same_edges),
        'conflicts': sum(1 for edge in same_edges if edge['kind'] == 'contradicts'),
        'cross_relations': len(cross_edges),
        'observations': sum(len(items) for items in obs_by_memory.values()),
    }
    return {'memories': memories, 'candidates': list_candidates(path, uid),
            'enabled': is_enabled(path, uid), 'stats': stats}


def backfill_edges(path, user_id):
    uid = str(user_id)
    with _connect(path) as con:
        rows = _memory_rows(con, uid)
        count = len(rows)
        if not count or _has_missing_vectors(con, uid):
            return 0
        state = con.execute('SELECT memory_count FROM memory_graph_state WHERE user_id=?', (uid,)).fetchone()
        if state and state['memory_count'] == count:
            return 0
    pairs = {}
    for index, left in enumerate(rows):
        for right in rows[index + 1:]:
            if not left.get('vector') or not right.get('vector'):
                continue
            score = _similarity(left['vector'], right['vector'])
            if score >= RELATED_FLOOR:
                key = (left['id'], right['id']) if left['id'] < right['id'] else (right['id'], left['id'])
                pairs[key] = max(score, pairs.get(key, -1.0))
    by_memory = {}
    for (a, b), score in pairs.items():
        by_memory.setdefault(a, []).append((score, b))
        by_memory.setdefault(b, []).append((score, a))
    selected = {}
    for mid, items in by_memory.items():
        for score, other in sorted(items, key=lambda item: (-item[0], -item[1]))[:4]:
            key = (mid, other) if mid < other else (other, mid)
            selected[key] = max(score, selected.get(key, -1.0))
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        if con.execute('SELECT COUNT(*) FROM memories WHERE user_id=?', (uid,)).fetchone()[0] != count:
            return 0
        state = con.execute('SELECT memory_count FROM memory_graph_state WHERE user_id=?', (uid,)).fetchone()
        if state and state['memory_count'] == count:
            return 0
        if _has_missing_vectors(con, uid):
            return 0
        for (a, b), score in selected.items():
            _upsert_vector_edge(con, a, b, score)
        con.execute('''INSERT INTO memory_graph_state(user_id,memory_count) VALUES(?,?)
            ON CONFLICT(user_id) DO UPDATE SET memory_count=excluded.memory_count''', (uid, count))
        return len(selected)


def legacy_edge_candidates(path, user_id, limit=REFINE_LIMIT, floor=REFINE_FLOOR):
    with _connect(path) as con:
        return [dict(r) for r in con.execute('''
            SELECT e.a_id,e.b_id,e.weight,ma.text AS a_text,mb.text AS b_text
            FROM memory_edges e
            JOIN memories ma ON ma.id=e.a_id
            JOIN memories mb ON mb.id=e.b_id
            WHERE e.origin='vector' AND e.kind='related' AND e.weight>=?
              AND e.scope='same' AND ma.user_id=? AND mb.user_id=?
            ORDER BY e.weight DESC, e.a_id, e.b_id LIMIT ?''',
            (float(floor), str(user_id), str(user_id), int(limit)))]


def refine_edges(path, user_id, decisions):
    uid = str(user_id)
    if not decisions:
        return 0
    written = 0
    with _connect(path) as con:
        con.execute('BEGIN IMMEDIATE')
        for item in decisions:
            if not isinstance(item, dict):
                continue
            a, b = _as_int(item.get('a')), _as_int(item.get('b'))
            kind = item.get('kind')
            if a is None or b is None or a == b:
                continue
            if kind not in ('corroborates', 'refines', 'contradicts', 'related', 'unrelated'):
                continue
            if not con.execute('SELECT 1 FROM memories WHERE id=? AND user_id=?', (a, uid)).fetchone():
                continue
            if not con.execute('SELECT 1 FROM memories WHERE id=? AND user_id=?', (b, uid)).fetchone():
                continue
            _upsert_model_edge(con, a, b, kind, _clamp_weight(item.get('weight'), 0.7))
            written += 1
    return written


def graph_users(path, limit=20):
    limit = max(1, int(limit))
    with _connect(path) as con:
        backfill = [row[0] for row in con.execute('''
            SELECT m.user_id FROM memories m
            LEFT JOIN memory_graph_state g ON g.user_id=m.user_id
            WHERE NOT EXISTS (SELECT 1 FROM memory_preferences p
                              WHERE p.user_id=m.user_id AND p.enabled=0)
            GROUP BY m.user_id
            HAVING COUNT(*) != COALESCE(MAX(g.memory_count), -1)
            ORDER BY m.user_id LIMIT ?''', (limit,))]
        refine = [row[0] for row in con.execute('''
            SELECT DISTINCT ma.user_id FROM memory_edges e
            JOIN memories ma ON ma.id=e.a_id
            JOIN memories mb ON mb.id=e.b_id
            WHERE e.origin='vector' AND e.kind='related' AND e.weight>=?
              AND e.scope='same' AND ma.user_id=mb.user_id
              AND NOT EXISTS (SELECT 1 FROM memory_preferences p
                              WHERE p.user_id=ma.user_id AND p.enabled=0)
            LIMIT ?''', (REFINE_FLOOR, limit))]
    result = []
    for uid in backfill + refine:
        if uid not in result:
            result.append(uid)
    return result[:limit]
