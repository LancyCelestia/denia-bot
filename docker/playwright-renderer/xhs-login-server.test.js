'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {once} = require('node:events');
const {
  createXhsLoginServer,
  buildCookieHeader,
  isAllowedUrl,
} = require('./xhs-login-server');

const TOKEN = 'a'.repeat(64);
const OWNER = 'b'.repeat(64);
const OTHER_OWNER = 'c'.repeat(64);

function fakeClock() {
  let now = 0;
  let sequence = 0;
  const timers = new Map();
  return {
    now: () => now,
    setTimeout(fn, ms) {
      const id = ++sequence;
      timers.set(id, {at: now + ms, fn});
      return id;
    },
    clearTimeout(id) {
      timers.delete(id);
    },
    async tick(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) {
          timers.delete(id);
          await timer.fn();
        }
      }
    },
  };
}

function fakeBrowser(steps = [{status: 'waiting'}]) {
  let index = 0;
  let closed = false;
  return {
    get closed() {
      return closed;
    },
    async open() {
      return {qr_png: Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]).toString('base64')};
    },
    async poll() {
      const step = steps[Math.min(index, steps.length - 1)];
      index += 1;
      return typeof step === 'function' ? step() : step;
    },
    async close() {
      closed = true;
    },
  };
}

async function start(t, options = {}) {
  const clock = options.clock || fakeClock();
  const browsers = [];
  const createBrowser = options.createBrowser || (() => {
    const browser = fakeBrowser(options.steps);
    browsers.push(browser);
    return browser;
  });
  const server = createXhsLoginServer({
    token: TOKEN,
    clock,
    createBrowser,
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return {server, url: `http://127.0.0.1:${server.address().port}`, clock, browsers};
}

function post(url, path, body, token = TOKEN) {
  const headers = {'Content-Type': 'application/json'};
  if (token) headers['X-XHS-Login-Token'] = token;
  return fetch(url + path, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
}

async function begin(url) {
  const response = await post(url, '/start', {owner: OWNER});
  assert.equal(response.status, 200);
  return response.json();
}

const readyCookies = [
  {name: 'web_session', value: 'guest', domain: '.xiaohongshu.com', path: '/'},
  {name: 'web_session', value: 'real', domain: 'www.xiaohongshu.com', path: '/'},
  {name: 'a1', value: 'device', domain: '.xiaohongshu.com', path: '/'},
];

test('health is public and token protects the login endpoints', async t => {
  const {url} = await start(t);
  const health = await fetch(url + '/health');
  assert.equal(health.status, 200);
  assert.equal((await health.json()).ok, true);
  assert.equal((await post(url, '/start', {owner: OWNER}, 'bad')).status, 401);
  assert.equal((await post(url, '/start', {owner: OWNER}, '')).status, 401);
  assert.equal((await post(url, '/start', {owner: OWNER, extra: true})).status, 400);
});

test('one active session, owner binding and QR response', async t => {
  const {url} = await start(t);
  const first = await begin(url);
  assert.match(first.id, /^[0-9a-f]{64}$/);
  assert.ok(first.qr_png);
  assert.equal(first.expires_in, 180);
  assert.equal((await post(url, '/start', {owner: OWNER})).status, 409);
  assert.equal((await post(url, '/status', {owner: OTHER_OWNER, id: first.id})).status, 404);
  assert.equal((await post(url, '/status', {owner: OWNER, id: 'x'})).status, 400);
  assert.equal((await post(url, '/status', {owner: OWNER, id: 'd'.repeat(64)})).status, 404);
});

test('waiting, scanned and ready states can be claimed and retried', async t => {
  const {url, clock} = await start(t, {steps: [
    {status: 'waiting'},
    {status: 'scanned'},
    {status: 'ready', cookies: readyCookies},
  ]});
  const session = await begin(url);
  let response = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await response.json()).status, 'waiting');
  await clock.tick(1100);
  response = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await response.json()).status, 'scanned');
  await clock.tick(1100);
  response = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await response.json()).status, 'ready');

  const claim = await post(url, '/claim', {owner: OWNER, id: session.id});
  assert.equal(claim.status, 200);
  const claimed = await claim.json();
  assert.match(claimed.claim_id, /^[0-9a-f]{64}$/);
  assert.match(claimed.cookies, /web_session=real/);
  assert.equal((await post(url, '/claim', {owner: OWNER, id: session.id})).status, 409);

  const failedSave = await post(url, '/settle', {
    owner: OWNER, id: session.id, claim_id: claimed.claim_id, commit: false,
  });
  assert.equal(failedSave.status, 200);
  response = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await response.json()).status, 'ready');

  const retry = await post(url, '/claim', {owner: OWNER, id: session.id});
  const retried = await retry.json();
  const saved = await post(url, '/settle', {
    owner: OWNER, id: session.id, claim_id: retried.claim_id, commit: true,
  });
  assert.equal(saved.status, 200);
  assert.equal((await post(url, '/status', {owner: OWNER, id: session.id})).status, 404);
});

test('claim lease restores ready and terminal sessions expire', async t => {
  const {url, clock} = await start(t, {steps: [{status: 'ready', cookies: readyCookies}]});
  const session = await begin(url);
  await post(url, '/status', {owner: OWNER, id: session.id});
  const claim = await (await post(url, '/claim', {owner: OWNER, id: session.id})).json();
  await clock.tick(30_001);
  const status = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await status.json()).status, 'ready');
  const again = await post(url, '/claim', {owner: OWNER, id: session.id});
  assert.equal(again.status, 200);
  const claimed = await again.json();
  assert.notEqual(claimed.claim_id, claim.claim_id);
  await post(url, '/settle', {
    owner: OWNER, id: session.id, claim_id: claimed.claim_id, commit: true,
  });
  assert.equal((await post(url, '/status', {owner: OWNER, id: session.id})).status, 404);
});

test('QR expiry closes the browser and retains a readable terminal state', async t => {
  const {url, clock, browsers} = await start(t);
  const session = await begin(url);
  await clock.tick(180_001);
  const status = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal(status.status, 200);
  assert.equal((await status.json()).status, 'expired');
  assert.equal(browsers[0].closed, true);
  await clock.tick(30_001);
  assert.equal((await post(url, '/status', {owner: OWNER, id: session.id})).status, 404);
});

test('risk control and invalid cookies are reported without values', async t => {
  const {url} = await start(t, {steps: [{status: 'error', code: 'risk_control'}]});
  const session = await begin(url);
  let status = await post(url, '/status', {owner: OWNER, id: session.id});
  let payload = await status.json();
  assert.equal(payload.status, 'error');
  assert.equal(payload.code, 'risk_control');
  assert.equal(JSON.stringify(payload).includes('web_session'), false);

  const second = await start(t, {steps: [{status: 'ready', cookies: [
    {name: 'web_session', value: 'bad value', domain: '.xiaohongshu.com'},
  ]}]});
  const bad = await begin(second.url);
  status = await post(second.url, '/status', {owner: OWNER, id: bad.id});
  payload = await status.json();
  assert.equal(payload.status, 'error');
  assert.equal(payload.code, 'invalid_cookie');
});

test('cancel releases the browser and rejects while saving', async t => {
  const {url, browsers} = await start(t, {steps: [{status: 'ready', cookies: readyCookies}]});
  const session = await begin(url);
  await post(url, '/status', {owner: OWNER, id: session.id});
  const claim = await (await post(url, '/claim', {owner: OWNER, id: session.id})).json();
  assert.equal((await post(url, '/cancel', {owner: OWNER, id: session.id})).status, 409);
  await post(url, '/settle', {
    owner: OWNER, id: session.id, claim_id: claim.claim_id, commit: false,
  });
  assert.equal((await post(url, '/cancel', {owner: OWNER, id: session.id})).status, 200);
  const cancelled = await post(url, '/status', {owner: OWNER, id: session.id});
  assert.equal((await cancelled.json()).status, 'expired');
  assert.equal(browsers[0].closed, true);
});

test('client disconnect closes the pending browser', async t => {
  let closed = false;
  let release;
  const pending = new Promise(resolve => {
    release = resolve;
  });
  const {url} = await start(t, {createBrowser: () => ({
    async open() {
      await pending;
      return {qr_png: 'unused'};
    },
    async poll() {
      return {status: 'waiting'};
    },
    async close() {
      closed = true;
    },
  })});
  const request = http.request(url + '/start', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-XHS-Login-Token': TOKEN},
  });
  request.on('error', () => {});
  request.end(JSON.stringify({owner: OWNER}));
  for (let attempt = 0; attempt < 50 && !closed; attempt += 1) {
    await new Promise(resolve => setTimeout(resolve, 10));
    if (attempt === 2) request.destroy();
  }
  assert.equal(closed, true);
  release({qr_png: 'unused'});
});

test('cookie header prefers the exact host and requires web_session', () => {
  assert.equal(buildCookieHeader(readyCookies), 'web_session=real; a1=device');
  assert.throws(() => buildCookieHeader([]), /web_session/);
  assert.throws(() => buildCookieHeader([
    {name: 'web_session', value: 'has space', domain: '.xiaohongshu.com'},
  ]), /web_session/);
});

test('only Xiaohongshu HTTPS hosts are allowed', () => {
  assert.equal(isAllowedUrl('https://www.xiaohongshu.com/explore'), true);
  assert.equal(isAllowedUrl('https://fe-static.xhscdn.com/app.js'), true);
  assert.equal(isAllowedUrl('https://sns-video-hw.xhscdn.net/a.mp4'), true);
  assert.equal(isAllowedUrl('https://xiaohongshu.com.evil.com/'), false);
  assert.equal(isAllowedUrl('http://www.xiaohongshu.com/'), false);
  assert.equal(isAllowedUrl('https://www.xiaohongshu.com:444/'), false);
  assert.equal(isAllowedUrl('data:image/png;base64,AA'), true);
  assert.equal(isAllowedUrl('about:blank'), true);
  assert.equal(isAllowedUrl('about:srcdoc'), false);
});
