'use strict';
// Original MIT implementation for the dashboard's Xiaohongshu QR login.
// This service is separate from the image renderer: the renderer keeps
// JavaScript and external network disabled, while this service intentionally
// drives the official login page in an isolated, token-protected browser.
const http = require('node:http');
const crypto = require('node:crypto');
const {readJson, HttpError} = require('./server');

const HEX64 = /^[0-9a-f]{64}$/;
const MAX_BODY = 8 * 1024;
const QR_TTL_MS = 180_000;
const READY_TTL_MS = 300_000;
const CLAIM_TTL_MS = 30_000;
const TERMINAL_TTL_MS = 30_000;
const POLL_INTERVAL_MS = 1_000;
const MAX_COOKIES = 100;
const MAX_COOKIE_HEADER = 32_768;
const ALLOWED_HOSTS = ['xiaohongshu.com', 'xhscdn.com', 'xhscdn.net'];
const LOGIN_URL = 'https://www.xiaohongshu.com/explore';

const systemClock = {
  now: () => Date.now(),
  setTimeout: (fn, ms) => setTimeout(fn, ms),
  clearTimeout: handle => clearTimeout(handle),
};

function sendJson(res, status, data) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(data));
}

function isHex64(value) {
  return typeof value === 'string' && HEX64.test(value);
}

function randomId() {
  return crypto.randomBytes(32).toString('hex');
}

function ownerHash(adminSession) {
  return crypto.createHash('sha256').update(String(adminSession)).digest('hex');
}

function safeTokenEqual(supplied, expected) {
  if (typeof supplied !== 'string' || typeof expected !== 'string') return false;
  if (supplied.length !== expected.length) return false;
  return crypto.timingSafeEqual(Buffer.from(supplied), Buffer.from(expected));
}

function isAllowedUrl(raw) {
  let parsed;
  try {
    parsed = new URL(String(raw));
  } catch {
    return false;
  }
  if (parsed.protocol === 'data:') return true;
  if (parsed.protocol === 'about:') return parsed.href === 'about:blank';
  if (parsed.protocol !== 'https:') return false;
  if (parsed.port && parsed.port !== '443') return false;
  const host = parsed.hostname.toLowerCase();
  return ALLOWED_HOSTS.some(base => host === base || host.endsWith('.' + base));
}

function cookieDomain(value) {
  return String(value || '').replace(/^\./, '').toLowerCase();
}

function isCookieDomain(value) {
  const domain = cookieDomain(value);
  return domain === 'xiaohongshu.com' || domain.endsWith('.xiaohongshu.com');
}

function domainRank(cookie) {
  const domain = cookieDomain(cookie.domain);
  if (domain === 'www.xiaohongshu.com') return 3;
  if (domain === 'xiaohongshu.com') return 2;
  return 1;
}

function validCookieName(name) {
  return typeof name === 'string'
    && name.length > 0
    && name.length <= 256
    && /^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(name);
}

function validCookieValue(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 4096
    && /^[\x21-\x7e]+$/.test(value)
    && !value.includes(';');
}

function buildCookieHeader(cookies) {
  const ordered = (Array.isArray(cookies) ? cookies : [])
    .filter(cookie => cookie && isCookieDomain(cookie.domain))
    .sort((a, b) => domainRank(b) - domainRank(a)
      || String(b.domain || '').length - String(a.domain || '').length
      || String(b.path || '/').length - String(a.path || '/').length);
  const values = new Map();
  for (const cookie of ordered) {
    if (!validCookieName(cookie.name) || !validCookieValue(cookie.value)) continue;
    if (!values.has(cookie.name)) values.set(cookie.name, cookie.value);
  }
  if (!values.get('web_session')) throw new HttpError(422, 'missing web_session');
  if (values.size > MAX_COOKIES) throw new HttpError(422, 'too many cookies');
  const header = [...values].map(([name, value]) => `${name}=${value}`).join('; ');
  if (header.length > MAX_COOKIE_HEADER) throw new HttpError(422, 'cookie header too large');
  return header;
}

function createDefaultBrowser(chromium, mode) {
  if (!chromium || typeof chromium.launch !== 'function') {
    throw new Error('playwright chromium is unavailable');
  }
  let browser = null;
  let context = null;
  let page = null;
  let closed = false;
  let guestSessions = new Set();

  async function open() {
    browser = await chromium.launch({headless: true, chromiumSandbox: mode === 'sandbox'});
    context = await browser.newContext({
      locale: 'zh-CN',
      timezoneId: 'Asia/Shanghai',
      viewport: {width: 1280, height: 800},
      serviceWorkers: 'block',
      acceptDownloads: false,
    });
    await context.route('**/*', async route => {
      if (isAllowedUrl(route.request().url())) await route.continue();
      else await route.abort();
    });
    page = await context.newPage();
    page.setDefaultTimeout(30_000);
    page.on('dialog', dialog => dialog.dismiss().catch(() => {}));
    await page.goto(LOGIN_URL, {waitUntil: 'domcontentloaded', timeout: 30_000});

    const qr = page.locator('.login-container img.qrcode-img');
    try {
      await qr.waitFor({state: 'visible', timeout: 5_000});
    } catch {
      const login = page.getByRole('button', {name: '登录'}).first();
      if (await login.count()) await login.click().catch(() => {});
      await qr.waitFor({state: 'visible', timeout: 25_000});
    }
    await page.waitForFunction(() => {
      const image = document.querySelector('.login-container img.qrcode-img');
      return Boolean(image && image.complete && image.naturalWidth > 0);
    }, null, {timeout: 10_000});

    const initial = await context.cookies('https://www.xiaohongshu.com/');
    guestSessions = new Set(
      initial.filter(cookie => cookie.name === 'web_session' && cookie.value)
        .map(cookie => cookie.value),
    );
    const png = await qr.screenshot({type: 'png'});
    if (!Buffer.isBuffer(png) || png.length < 8
        || png[0] !== 0x89 || png[1] !== 0x50 || png[2] !== 0x4e || png[3] !== 0x47) {
      throw new Error('login page did not return a PNG');
    }
    return {qr_png: png.toString('base64')};
  }

  async function poll() {
    if (closed || !page || page.isClosed()) {
      return {status: 'error', code: 'upstream_error'};
    }
    const body = await page.locator('body').innerText().catch(() => '');
    if (body.includes('请通过验证')) return {status: 'error', code: 'risk_control'};
    const cookies = await context.cookies('https://www.xiaohongshu.com/');
    const current = cookies.find(cookie => cookie.name === 'web_session' && cookie.value);
    const loggedIn = await page.locator('.main-container .user .link-wrapper .channel')
      .count().catch(() => 0);
    if (loggedIn > 0 && current && !guestSessions.has(current.value)) {
      return {status: 'ready', cookies};
    }
    if (body.includes('二维码已过期')) return {status: 'expired'};
    if (body.includes('扫码成功')) return {status: 'scanned'};
    return {status: 'waiting'};
  }

  async function close() {
    if (closed) return;
    closed = true;
    try {
      await context?.close();
    } catch {}
    try {
      await browser?.close();
    } catch {}
    context = null;
    browser = null;
    page = null;
    guestSessions.clear();
  }

  return {open, poll, close};
}

function createXhsLoginServer({
  chromium,
  token,
  clock = systemClock,
  createBrowser,
  mode = 'sandbox',
} = {}) {
  if (!isHex64(token)) throw new Error('XHS_LOGIN_TOKEN must be 64 lowercase hex characters');
  if (mode !== 'sandbox' && mode !== 'container') throw new Error('XHS_LOGIN_MODE must be sandbox or container');
  const browserFactory = createBrowser
    || (() => createDefaultBrowser(chromium, mode));
  if (!createBrowser && (!chromium || typeof chromium.launch !== 'function')) {
    throw new Error('chromium or createBrowser is required');
  }

  const sessions = new Map();
  let activeId = null;

  function clearTimer(session) {
    if (session.timer !== null) {
      clock.clearTimeout(session.timer);
      session.timer = null;
    }
  }

  function schedule(session, at, callback) {
    clearTimer(session);
    session.timer = clock.setTimeout(() => {
      session.timer = null;
      try {
        callback();
      } catch {}
    }, Math.max(0, at - clock.now()));
  }

  function removeSession(session) {
    clearTimer(session);
    session.cancelled = true;
    session.cookies = null;
    session.qr_png = '';
    const browser = session.browser;
    session.browser = null;
    if (browser) Promise.resolve(browser.close()).catch(() => {});
    sessions.delete(session.id);
    if (activeId === session.id) activeId = null;
  }

  function finish(session, status, {message = '', code = null} = {}) {
    if (session.status === 'expired' && status === 'expired') return;
    session.status = status;
    session.message = message;
    session.code = code;
    session.qr_png = '';
    session.cookies = null;
    session.claimId = null;
    session.claimUntil = 0;
    const browser = session.browser;
    session.browser = null;
    if (browser) Promise.resolve(browser.close()).catch(() => {});
    if (activeId === session.id) activeId = null;
    schedule(session, clock.now() + TERMINAL_TTL_MS, () => sessions.delete(session.id));
  }

  function expireReady(session) {
    if (session.status === 'ready') {
      finish(session, 'expired', {message: '登录凭证已过期'});
    }
  }

  function setReady(session, cookies) {
    clearTimer(session);
    session.status = 'ready';
    session.message = '登录成功';
    session.code = null;
    session.qr_png = '';
    session.cookies = cookies;
    session.readyUntil = clock.now() + READY_TTL_MS;
    const browser = session.browser;
    session.browser = null;
    if (browser) Promise.resolve(browser.close()).catch(() => {});
    schedule(session, session.readyUntil, () => expireReady(session));
  }

  function applyPoll(session, result) {
    if (session.status !== 'waiting' && session.status !== 'scanned') return;
    const status = result && result.status;
    if (status === 'waiting') {
      session.status = 'waiting';
      session.message = '等待扫码';
    } else if (status === 'scanned') {
      session.status = 'scanned';
      session.message = '已扫码，请确认';
    } else if (status === 'expired') {
      finish(session, 'expired', {message: '二维码已过期'});
    } else if (status === 'error') {
      finish(session, 'error', {code: result.code || 'upstream_error', message: '登录失败'});
    } else if (status === 'ready') {
      let header;
      try {
        header = buildCookieHeader(result.cookies);
      } catch {
        finish(session, 'error', {code: 'invalid_cookie', message: '登录成功但 Cookie 无效'});
        return;
      }
      setReady(session, header);
    }
  }

  async function refresh(session) {
    if (session.status !== 'waiting' && session.status !== 'scanned') return;
    if (session.polling) return;
    if (clock.now() - session.polledAt < POLL_INTERVAL_MS) return;
    if (!session.browser) {
      finish(session, 'error', {code: 'upstream_error', message: '登录页已关闭'});
      return;
    }
    session.polling = true;
    try {
      const result = await session.browser.poll();
      session.polledAt = clock.now();
      applyPoll(session, result);
    } catch {
      finish(session, 'error', {code: 'upstream_error', message: '登录页读取失败'});
    } finally {
      session.polling = false;
    }
  }

  function beginSession(owner) {
    if (activeId) throw new HttpError(409, 'login already in progress');
    const id = randomId();
    const session = {
      id,
      owner,
      status: 'waiting',
      message: '正在生成二维码',
      code: null,
      qr_png: '',
      cookies: null,
      browser: null,
      polledAt: -Infinity,
      polling: false,
      expiresAt: clock.now() + QR_TTL_MS,
      readyUntil: 0,
      claimId: null,
      claimUntil: 0,
      timer: null,
      cancelled: false,
    };
    sessions.set(id, session);
    activeId = id;
    schedule(session, session.expiresAt, () => {
      if (session.status === 'waiting' || session.status === 'scanned') {
        finish(session, 'expired', {message: '二维码已过期'});
      }
    });
    return session;
  }

  async function openSession(session) {
    let browser;
    try {
      browser = browserFactory();
    } catch {
      finish(session, 'error', {code: 'upstream_error', message: '浏览器不可用'});
      throw new HttpError(503, 'browser unavailable');
    }
    session.browser = browser;
    try {
      const result = await browser.open();
      if (session.cancelled) throw new HttpError(408, 'login cancelled');
      if (!result || typeof result.qr_png !== 'string' || !result.qr_png) {
        throw new Error('missing qr');
      }
      session.qr_png = result.qr_png;
      session.status = 'waiting';
      session.message = '等待扫码';
      session.polledAt = -Infinity;
      return {id: session.id, qr_png: session.qr_png, expires_in: Math.floor(QR_TTL_MS / 1000)};
    } catch (error) {
      if (session.cancelled) {
        removeSession(session);
        throw new HttpError(408, 'login cancelled');
      }
      finish(session, 'error', {code: 'upstream_error', message: '生成二维码失败'});
      throw new HttpError(502, 'qr unavailable');
    }
  }

  function requireSession(owner, id) {
    if (!isHex64(owner) || !isHex64(id)) throw new HttpError(400, 'invalid session');
    const session = sessions.get(id);
    if (!session || session.owner !== owner) throw new HttpError(404, 'unknown session');
    return session;
  }

  function claimSession(session) {
    if (session.status !== 'ready') throw new HttpError(409, 'login not ready');
    const claimId = randomId();
    session.status = 'saving';
    session.claimId = claimId;
    session.claimUntil = clock.now() + CLAIM_TTL_MS;
    schedule(session, session.claimUntil, () => {
      if (session.status !== 'saving' || session.claimId !== claimId) return;
      session.claimId = null;
      if (clock.now() < session.readyUntil) {
        session.status = 'ready';
        schedule(session, session.readyUntil, () => expireReady(session));
      } else {
        finish(session, 'expired', {message: '登录凭证已过期'});
      }
    });
    return {claim_id: claimId, cookies: session.cookies};
  }

  function settleSession(session, claimId, commit) {
    if (session.status !== 'saving' || session.claimId !== claimId) {
      throw new HttpError(409, 'claim mismatch');
    }
    if (commit) {
      removeSession(session);
      return;
    }
    session.claimId = null;
    if (clock.now() < session.readyUntil) {
      session.status = 'ready';
      schedule(session, session.readyUntil, () => expireReady(session));
    } else {
      finish(session, 'expired', {message: '登录凭证已过期'});
    }
  }

  function cancelSession(session) {
    if (session.status === 'saving') throw new HttpError(409, 'login is being saved');
    if (session.status === 'waiting' || session.status === 'scanned' || session.status === 'ready') {
      finish(session, 'expired', {message: '已取消'});
    }
    return {ok: true};
  }

  function authorized(req) {
    const supplied = req.headers['x-xhs-login-token']
      || String(req.headers.authorization || '').replace(/^Bearer\s+/i, '');
    return safeTokenEqual(supplied, token);
  }

  function bodyObject(body) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      throw new HttpError(400, 'invalid request');
    }
    return body;
  }

  function only(body, allowed) {
    for (const key of Object.keys(body)) {
      if (!allowed.has(key)) throw new HttpError(400, 'unknown field');
    }
  }

  const server = http.createServer(async (req, res) => {
    const path = (req.url || '/').split('?')[0];
    if (req.method === 'GET' && path === '/health') {
      return sendJson(res, 200, {ok: true, ready: true, active: activeId ? 1 : 0});
    }
    if (!['/start', '/status', '/cancel', '/claim', '/settle'].includes(path)) {
      return sendJson(res, 404, {error: 'not found'});
    }
    if (req.method !== 'POST') return sendJson(res, 405, {error: 'method not allowed'});
    if (!authorized(req)) return sendJson(res, 401, {error: 'unauthorized'});
    try {
      const body = bodyObject(await readJson(req, MAX_BODY));
      if (path === '/start') {
        only(body, new Set(['owner']));
        if (!isHex64(body.owner)) throw new HttpError(400, 'invalid owner');
        const session = beginSession(body.owner);
        res.on('close', () => {
          if (!res.writableEnded) removeSession(session);
        });
        const payload = await openSession(session);
        return sendJson(res, 200, payload);
      }
      if (path === '/status') {
        only(body, new Set(['owner', 'id']));
        const session = requireSession(body.owner, body.id);
        await refresh(session);
        const payload = {status: session.status};
        if (session.status === 'error' && session.code) payload.code = session.code;
        return sendJson(res, 200, payload);
      }
      if (path === '/cancel') {
        only(body, new Set(['owner', 'id']));
        return sendJson(res, 200, cancelSession(requireSession(body.owner, body.id)));
      }
      if (path === '/claim') {
        only(body, new Set(['owner', 'id']));
        return sendJson(res, 200, claimSession(requireSession(body.owner, body.id)));
      }
      only(body, new Set(['owner', 'id', 'claim_id', 'commit']));
      if (!isHex64(body.claim_id) || typeof body.commit !== 'boolean') {
        throw new HttpError(400, 'invalid settle request');
      }
      settleSession(requireSession(body.owner, body.id), body.claim_id, body.commit);
      return sendJson(res, 200, {ok: true});
    } catch (error) {
      const status = error instanceof HttpError ? error.status : 500;
      if (!(error instanceof HttpError)) console.error('[xhs-login] internal error');
      return sendJson(res, status, {error: status === 500 ? 'login service failed' : error.message});
    }
  });
  server.requestTimeout = 45_000;
  server.headersTimeout = 10_000;
  server.on('close', () => {
    for (const session of [...sessions.values()]) removeSession(session);
    sessions.clear();
    activeId = null;
  });
  return server;
}

module.exports = {
  createXhsLoginServer,
  createDefaultBrowser,
  buildCookieHeader,
  isAllowedUrl,
  ownerHash,
  HttpError,
};

if (require.main === module) {
  const token = String(process.env.XHS_LOGIN_TOKEN || '');
  const mode = String(process.env.XHS_LOGIN_MODE || 'sandbox');
  if (!isHex64(token)) {
    console.error('[xhs-login] XHS_LOGIN_TOKEN must be 64 lowercase hex characters');
    process.exit(1);
  }
  if (mode !== 'sandbox' && mode !== 'container') {
    console.error('[xhs-login] XHS_LOGIN_MODE must be sandbox or container');
    process.exit(1);
  }
  const server = createXhsLoginServer({
    chromium: require('playwright').chromium,
    token,
    mode,
  });
  const host = mode === 'container' ? '0.0.0.0' : '127.0.0.1';
  server.listen(Number(process.env.PORT || 3102), host);
  for (const signal of ['SIGTERM', 'SIGINT']) {
    process.on(signal, () => {
      server.close(() => process.exit(0));
      setTimeout(() => process.exit(1), 5_000).unref();
    });
  }
}
