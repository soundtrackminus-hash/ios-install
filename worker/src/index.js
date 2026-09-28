import qrcode from 'qrcode-generator';

const CATALOG_URL = 'https://soundtrackminus-hash.github.io/ios-install/catalog.json';
const CACHE_TTL = 60;
const TOKEN_TTL = 600;
const SLUG_RE = /^[a-z0-9-]{1,64}$/;
const TOKEN_RE = /^[a-f0-9]{32}$/;
let catalogCache = { data: null, at: 0 };

async function getCatalog() {
  const now = Date.now() / 1000;
  if (catalogCache.data && now - catalogCache.at < CACHE_TTL) return catalogCache.data;
  const r = await fetch(CATALOG_URL, { cf: { cacheTtl: CACHE_TTL, cacheEverything: true } });
  if (!r.ok) throw new Error('catalog fetch failed: ' + r.status);
  const data = await r.json();
  catalogCache = { data, at: now };
  return data;
}

function randToken() {
  const b = new Uint8Array(16);
  crypto.getRandomValues(b);
  return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
}

function qrSvg(text) {
  const qr = qrcode(0, 'M');
  qr.addData(text);
  qr.make();
  const svg = qr.createSvgTag(6, 3);
  return svg.replace('<svg ', '<svg style="width:100%;height:auto;" ');
}

function cors(origin) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Cache-Control': 'no-store',
  };
}

function json(status, body, extra) {
  return new Response(JSON.stringify(body), {
    status,
    headers: Object.assign({ 'Content-Type': 'application/json; charset=utf-8' }, cors(), extra || {}),
  });
}

function page(title, bodyHtml) {
  return new Response('<!DOCTYPE html><html lang="ru"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1">' +
    '<title>' + title + '</title>' +
    '<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:440px;' +
    'margin:2rem auto;padding:0 1rem;color:#111}h1{font-size:1.3rem}' +
    '.card{background:#f6f7f9;border:1px solid #e0e2e6;border-radius:16px;padding:1rem;margin:1rem 0}' +
    'button{width:100%;background:#1c64f2;color:#fff;border:0;border-radius:12px;' +
    'padding:.9rem;font-size:1rem;cursor:pointer}.hint{color:#6b7280;font-size:.85rem;' +
    'margin-top:.8rem;line-height:1.4}</style></head><body>' + bodyHtml + '</body></html>', {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
  });
}

function installPage(app, base) {
  const absIcon = app.iconUrl.indexOf('http') === 0 ? app.iconUrl : base + app.iconUrl;
  const itms = 'itms-services://?action=download-manifest&url=' + encodeURIComponent(app.manifestUrl);
  return page('Установить ' + app.name,
    '<h1>Установка</h1><div class="card"><b>' + app.name + '</b>' +
    '<div style="color:#6b7280;font-size:.85rem;margin:.3rem 0">v' + app.version + ' · ' + app.sizeFormatted + '</div>' +
    '<div style="display:flex;align-items:center;gap:1rem;margin:.8rem 0">' +
    '<img src="' + absIcon + '" alt="" style="width:64px;height:64px;border-radius:14px">' +
    '<div>Нажмите кнопку, чтобы начать установку прямо сейчас.</div></div>' +
    '<button onclick="location.href=\'' + itms + '\'">Установить сейчас</button>' +
    '<div class="hint">После нажатия откройте «Настройки» → «Основные» → «VPN и управление устройством» ' +
    'и разрешите профиль разработчика, если iPhone попросит.</div></div>' +
    '<script>setTimeout(function(){location.href="' + itms + '";},700);</script>');
}

function expiredPage() {
  return page('Ссылка устарела',
    '<h1>Эта ссылка уже использована или истекла.</h1>' +
    '<p style="color:#6b7280">Одноразовые QR-коды действуют 10 минут и только на одну установку.</p>' +
    '<p>Откройте сайт <b>ios-install</b> снова и сгенерируйте новый QR-код.</p>');
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = url.origin;

    if (request.method === 'OPTIONS') {
      return new Response('', { status: 204, headers: cors(request.headers.get('Origin')) });
    }

    // GET /api/qr/<slug>.svg?t=<cache-buster>
    const qrMatch = url.pathname.match(/^\/api\/qr\/([a-z0-9-]+)\.svg$/);
    if (qrMatch && request.method === 'GET') {
      const slug = qrMatch[1];
      if (!SLUG_RE.test(slug)) return json(400, { error: 'invalid slug' });
      let catalog;
      try { catalog = await getCatalog(); }
      catch (e) { return json(502, { error: 'catalog unavailable' }); }
      const app = catalog.find((a) => a.slug === slug);
      if (!app) return json(404, { error: 'slug not found' });

      const token = randToken();
      await env.QR_TOKENS.put(token, JSON.stringify({ slug }), { expirationTtl: TOKEN_TTL });
      const svg = qrSvg(origin + '/i/' + token);
      return new Response(svg, {
        status: 200,
        headers: Object.assign({ 'Content-Type': 'image/svg+xml' }, cors(request.headers.get('Origin'))),
      });
    }

    // GET /i/<token> — одноразовая установка
    const installMatch = url.pathname.match(/^\/i\/([a-f0-9]{32})$/);
    if (installMatch && request.method === 'GET') {
      const token = installMatch[1];
      let val;
      try {
        val = await env.QR_TOKENS.get(token);
        if (val) await env.QR_TOKENS.delete(token);
      } catch (e) {
        return page('Ошибка', '<h1>Временная ошибка.</h1><p>Попробуйте открыть ссылку ещё раз.</p>');
      }
      if (!val) return expiredPage();
      let payload;
      try { payload = JSON.parse(val); } catch (e) { return expiredPage(); }
      let catalog, app;
      try {
        catalog = await getCatalog();
        app = catalog.find((a) => a.slug === payload.slug);
      } catch (e) { /* fallthrough */ }
      if (!app) return expiredPage();
      const base = CATALOG_URL.replace(/catalog\.json$/, '');
      return installPage(app, base);
    }

    // GET /health
    if (url.pathname === '/health') {
      return json(200, { ok: true, ts: Date.now() });
    }

    return json(404, { error: 'not found' });
  },
};