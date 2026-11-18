"""Standard SQLite transactions; all access is serialized by Service's asyncio lock.

The directory must belong to the service account on a local disk. The OS lock
prevents two instances using this database. This is ordinary single-process
storage, not a crash-proof filesystem or a transport transaction.
"""
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3

from .model import BiliError, Item, Scope


SCAN_SCHEMA = """CREATE TABLE scans(
 sub_id INTEGER PRIMARY KEY REFERENCES subscriptions(id) ON DELETE CASCADE,
 cursor TEXT,started REAL NOT NULL,pages INTEGER NOT NULL,seen TEXT NOT NULL)"""


SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO meta VALUES('source_stop','0');
CREATE TABLE subscriptions(
 id INTEGER PRIMARY KEY AUTOINCREMENT,bot TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
 uid TEXT NOT NULL,name TEXT NOT NULL,baseline REAL NOT NULL,last_check REAL NOT NULL DEFAULT 0,
 last_error TEXT NOT NULL DEFAULT '',gap INTEGER NOT NULL DEFAULT 0,
 UNIQUE(bot,kind,target,uid));
CREATE TABLE deliveries(
 sub_id INTEGER NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,dynamic_id TEXT NOT NULL,
 pub_ts REAL NOT NULL,payload TEXT,status TEXT NOT NULL CHECK(status IN
 ('baseline','pending','sending','sent','unknown','retry','failed','skipped')),
 attempts INTEGER NOT NULL DEFAULT 0,next_at REAL NOT NULL DEFAULT 0,
 receipt TEXT,note TEXT NOT NULL DEFAULT '',PRIMARY KEY(sub_id,dynamic_id));
CREATE INDEX delivery_queue ON deliveries(status,next_at,pub_ts);
"""+SCAN_SCHEMA+""";
CREATE TABLE target_cycles(bot TEXT NOT NULL,kind TEXT NOT NULL,target TEXT NOT NULL,
 cycle INTEGER NOT NULL,attempts INTEGER NOT NULL,last_uid TEXT NOT NULL,
 PRIMARY KEY(bot,kind,target));
PRAGMA user_version=31;
"""


class Store:
    def __init__(self,path):
        self.path = Path(path).absolute()
        self.stopped = False
        self.db = None
        self.lease = None
        try:
            # Do not follow a configured symlink into somebody else's state.
            if any(p.is_symlink() for p in (self.path,*self.path.parents)):
                raise OSError('symlink state path')
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            lock = self.path.with_suffix(self.path.suffix+'.lock')
            if lock.is_symlink(): raise OSError('symlink lock')
            self.lease = os.fdopen(os.open(lock,os.O_RDWR|os.O_CREAT,0o600),'r+b')
            if os.name == 'nt':
                import msvcrt
                if os.fstat(self.lease.fileno()).st_size==0:
                    self.lease.write(b'0');self.lease.flush()
                self.lease.seek(0)
                msvcrt.locking(self.lease.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.db = sqlite3.connect(self.path,timeout=1)
            self.db.row_factory = sqlite3.Row
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA max_page_count=65536')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version==0:
                if self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                    raise sqlite3.DatabaseError('unrecognized existing database')
                self.db.executescript(SCHEMA)
            elif version==30:
                # Explicit BEGIN makes the DDL and cursor fan-out one transaction.
                # Replay is safe: existing delivery IDs remain in the ledger.
                with self.transaction():
                    self.db.execute('BEGIN IMMEDIATE')
                    self.db.execute('ALTER TABLE scans RENAME TO uid_scans_v30')
                    self.db.execute(SCAN_SCHEMA)
                    self.db.execute('INSERT INTO scans SELECT s.id,c.cursor,c.started,c.pages,c.seen FROM subscriptions s JOIN uid_scans_v30 c ON c.uid=s.uid')
                    self.db.execute('DROP TABLE uid_scans_v30')
                    self.db.execute('PRAGMA user_version=31')
            elif version!=31:
                raise sqlite3.DatabaseError('unsupported database version')
            if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                raise sqlite3.DatabaseError('integrity failure')
            with self.transaction():
                self.db.execute("UPDATE deliveries SET status='unknown',payload=NULL,note='RESTART_UNKNOWN' WHERE status='sending'")
            self.source_stopped = self.db.execute("SELECT value FROM meta WHERE key='source_stop'").fetchone()[0]=='1'
        except (OSError,sqlite3.Error,TypeError,BiliError) as exc:
            self.close()
            raise BiliError('STORAGE_FAULT') from exc

    def require(self):
        if self.stopped or self.db is None:
            raise BiliError('STORAGE_FAULT')

    @contextmanager
    def transaction(self):
        self.require()
        try:
            with self.db:
                yield
        except (sqlite3.Error,OSError) as exc:
            self.stopped=True
            raise BiliError('STORAGE_FAULT') from exc

    def rows(self,sql,params=()):
        self.require()
        try:
            return [dict(r) for r in self.db.execute(sql,params).fetchall()]
        except (sqlite3.Error,OSError) as exc:
            self.stopped=True
            raise BiliError('STORAGE_FAULT') from exc

    def subscriptions(self,scope=None):
        return self.rows('SELECT * FROM subscriptions'+(' WHERE bot=? AND kind=? AND target=?' if scope else '')+' ORDER BY id',scope.key if scope else ())

    def add(self,scope,uid,name,baseline,items):
        with self.transaction():
            existing=self.db.execute('SELECT * FROM subscriptions WHERE bot=? AND kind=? AND target=? AND uid=?',(*scope.key,uid)).fetchone()
            if existing: return dict(existing)
            subs=self.subscriptions()
            if (len(subs)>=256 or len(self.subscriptions(scope))>=32 or
                len({r['uid'] for r in subs}|{uid})>64 or
                len({(r['bot'],r['kind'],r['target']) for r in subs}|{scope.key})>64):
                raise BiliError('CAPACITY')
            if self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]+len(items)>100000:
                raise BiliError('CAPACITY')
            cur=self.db.execute('INSERT INTO subscriptions(bot,kind,target,uid,name,baseline) VALUES(?,?,?,?,?,?)',(*scope.key,uid,name,baseline))
            self.db.executemany("INSERT OR IGNORE INTO deliveries(sub_id,dynamic_id,pub_ts,status) VALUES(?,?,?,'baseline')",
                [(cur.lastrowid,i.id,i.ts) for i in items])
            return dict(self.db.execute('SELECT * FROM subscriptions WHERE id=?',(cur.lastrowid,)).fetchone())

    def remove(self,scope,uid):
        with self.transaction():
            self.db.execute('DELETE FROM subscriptions WHERE bot=? AND kind=? AND target=? AND uid=?',(*scope.key,uid))
            # Both deliveries and that subscription's scan cascade, even if
            # another bot/group still subscribes to the same UID.
            # Keep target-cycle attempts until the next cycle, including re-subscribe.

    def scan(self,uid,*,sub_id=None):
        result=self.rows('SELECT c.* FROM scans c JOIN subscriptions s ON s.id=c.sub_id WHERE s.uid=? AND (? IS NULL OR s.id=?) ORDER BY s.id',
            (uid,sub_id,sub_id))
        return result[0] if result else None

    def ingest(self,uid,items,now,*,cursor,started,pages,seen,sub_id=None):
        capacity=False
        for sub in self.rows('SELECT * FROM subscriptions WHERE uid=? AND (? IS NULL OR id=?) ORDER BY id',(uid,sub_id,sub_id)):
            try:
                with self.transaction():
                    self._ingest_page(sub,items,now)
                    self.db.execute('INSERT OR REPLACE INTO scans VALUES(?,?,?,?,?)',(sub['id'],cursor,started,pages,json.dumps(seen)))
                    self.db.execute("UPDATE subscriptions SET last_check=?,last_error='' WHERE id=?",(now,sub['id']))
            except BiliError as exc:
                if exc.code!='CAPACITY':raise
                capacity=True
                self.record_error(uid,'CAPACITY',sub_id=sub['id'])
        if capacity:raise BiliError('CAPACITY')

    def _ingest_page(self,sub,items,now):
        # Called inside one subscription's transaction. A full queue rolls
        # back this entire page and its cursor, never another target's page.
        for value in items:
            if value.ts<=sub['baseline']:
                continue
            if self.db.execute('SELECT 1 FROM deliveries WHERE sub_id=? AND dynamic_id=?',(sub['id'],value.id)).fetchone():
                continue
            if value.ts>now+300:
                raise BiliError('REMOTE_SCHEMA')
            if self.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]>=100000:
                raise BiliError('CAPACITY')
            status='pending' if value.ts>=now-86400 else 'skipped'
            if status=='pending':
                total=self.db.execute("SELECT count(*) FROM deliveries WHERE status IN ('pending','retry','sending')").fetchone()[0]
                local=self.db.execute("SELECT count(*) FROM deliveries d JOIN subscriptions s ON s.id=d.sub_id WHERE d.status IN ('pending','retry','sending') AND s.bot=? AND s.kind=? AND s.target=?",(sub['bot'],sub['kind'],sub['target'])).fetchone()[0]
                own=self.db.execute("SELECT count(*) FROM deliveries WHERE sub_id=? AND status IN ('pending','retry','sending')",(sub['id'],)).fetchone()[0]
                if total>=1000 or local>=100 or own>=100:
                    raise BiliError('CAPACITY')
            else:
                self.db.execute('UPDATE subscriptions SET gap=1 WHERE id=?',(sub['id'],))
            payload=json.dumps(asdict(value),ensure_ascii=False) if status=='pending' else None
            self.db.execute('INSERT INTO deliveries(sub_id,dynamic_id,pub_ts,payload,status) VALUES(?,?,?,?,?)',
                (sub['id'],value.id,value.ts,payload,status))

    def record_error(self,uid,code,*,sub_id=None):
        with self.transaction():
            self.db.execute('UPDATE subscriptions SET last_error=?,gap=CASE WHEN ?=\'SCAN_GAP\' THEN 1 ELSE gap END WHERE (? IS NULL OR uid=?) AND (? IS NULL OR id=?)',
                (code,code,uid,uid,sub_id,sub_id))
            if code=='REMOTE_RESTRICTED':
                self.db.execute("UPDATE meta SET value='1' WHERE key='source_stop'")
        if code=='REMOTE_RESTRICTED':self.source_stopped=True

    def resume_source(self):
        with self.transaction():
            self.db.execute("UPDATE meta SET value='0' WHERE key='source_stop'")
        self.source_stopped=False

    def claim(self,scope,now,interval):
        with self.transaction():
            if self.source_stopped: return None
            expired=self.rows("SELECT DISTINCT sub_id FROM deliveries WHERE status IN ('pending','retry') AND pub_ts<?",(now-86400,))
            for row in expired: self.db.execute('UPDATE subscriptions SET gap=1 WHERE id=?',(row['sub_id'],))
            self.db.execute("UPDATE deliveries SET status='skipped',payload=NULL,note='WINDOW_EXPIRED' WHERE status IN ('pending','retry') AND pub_ts<?",(now-86400,))
            cycle=int(now//interval)
            credit=self.db.execute('SELECT * FROM target_cycles WHERE bot=? AND kind=? AND target=?',scope.key).fetchone()
            attempts=credit['attempts'] if credit and credit['cycle']==cycle else 0
            last=credit['last_uid'] if credit else ''
            if attempts>=5:return None
            candidates=self.rows("SELECT d.*,s.uid,s.bot,s.kind,s.target FROM deliveries d JOIN subscriptions s ON s.id=d.sub_id WHERE s.bot=? AND s.kind=? AND s.target=? AND d.status IN ('pending','retry') AND d.next_at<=? ORDER BY s.uid,d.pub_ts,d.dynamic_id",(*scope.key,now))
            if not candidates:return None
            row=next((r for r in candidates if r['uid']>last),candidates[0])
            try:
                value=Item(**json.loads(row['payload']))
                if value.id!=row['dynamic_id'] or value.uid!=row['uid']:raise ValueError
            except (TypeError,ValueError,BiliError) as exc:
                self.stopped=True
                raise BiliError('STORAGE_FAULT') from exc
            self.db.execute("UPDATE deliveries SET status='sending',attempts=attempts+1 WHERE sub_id=? AND dynamic_id=?",(row['sub_id'],row['dynamic_id']))
            self.db.execute('INSERT OR REPLACE INTO target_cycles VALUES(?,?,?,?,?,?)',(*scope.key,cycle,attempts+1,row['uid']))
            row['attempts']+=1
            row['item']=value
            return row

    def complete(self,row,receipt,now):
        status=receipt.kind if receipt.kind in {'sent','unknown'} else 'retry' if receipt.kind=='rejected' and row['attempts']<3 else 'failed'
        next_at=now+(60 if row['attempts']==1 else 300) if status=='retry' else 0
        with self.transaction():
            self.db.execute("UPDATE deliveries SET status=?,next_at=?,receipt=?,note=?,payload=CASE WHEN ?='retry' THEN payload ELSE NULL END WHERE sub_id=? AND dynamic_id=? AND status='sending'",
                (status,next_at,receipt.message_id,receipt.note,status,row['sub_id'],row['dynamic_id']))

    def statuses(self,scope):
        return {r['status']:r['n'] for r in self.rows("SELECT d.status,count(*) n FROM deliveries d JOIN subscriptions s ON s.id=d.sub_id WHERE s.bot=? AND s.kind=? AND s.target=? AND d.status!='baseline' GROUP BY d.status",scope.key)}

    def close(self):
        try:
            if self.db is not None:self.db.close()
        finally:
            self.db=None
            if self.lease is not None:self.lease.close()
            self.lease=None
