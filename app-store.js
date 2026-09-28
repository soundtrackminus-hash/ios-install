(function () {
  'use strict';

  var container = document.getElementById('apps-container');
  var APPS = [];

  function installUrl(app) {
    return 'itms-services://?action=download-manifest&url=' + encodeURIComponent(app.manifestUrl);
  }

  function cardHtml(app) {
    return '' +
      '<article class="app-card" data-tags="' + (app.searchTags || '') + ' ' + (app.name || '').toLowerCase() + '">' +
        '<div class="app-card__head">' +
          '<span class="app-card__icon"><img src="' + app.iconUrl + '" alt="" loading="lazy"></span>' +
          '<div class="app-card__info">' +
            '<div class="app-card__name" title="' + (app.name || '') + '">' + (app.name || '') + '</div>' +
            '<div class="app-card__meta">v' + (app.version || '?') + ' · ' + (app.sizeFormatted || '') + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="app-card__actions">' +
          '<button class="btn btn-install" onclick="window.location=\'' + installUrl(app).replace(/'/g, "%27") + '\'">Скачать и установить</button>' +
          '<div class="btn-row">' +
            '<button class="btn btn-ghost" data-copy="' + installUrl(app).replace(/"/g, "&quot;") + '" data-label="Скопировать ссылку">Скопировать ссылку</button>' +
            '<button class="btn btn-ghost" data-qr="' + encodeURIComponent(app.slug) + '" data-qrname="' + (app.name || '').replace(/"/g, "&quot;") + '">QR-код</button>' +
          '</div>' +
        '</div>' +
      '</article>';
  }

  function render() {
    var q = (document.getElementById('search').value || '').toLowerCase();
    var sort = document.getElementById('sort').value;
    var list = APPS.filter(function (a) {
      return (a.name || '').toLowerCase().indexOf(q) !== -1 ||
             (a.searchTags || '').toLowerCase().indexOf(q) !== -1;
    });
    if (sort === 'name-asc') {
      list.sort(function (a, b) { return (a.name || '').localeCompare(b.name || '', 'ru'); });
    } else if (sort === 'size-desc') {
      list.sort(function (a, b) { return (b.size || 0) - (a.size || 0); });
    } else if (sort === 'size-asc') {
      list.sort(function (a, b) { return (a.size || 0) - (b.size || 0); });
    }
    container.innerHTML = list.map(cardHtml).join('');
    document.getElementById('shown-count').textContent = list.length;
    document.getElementById('total-count').textContent = APPS.length;
    mount();
  }

  function mount() {
    var copyBtns = container.querySelectorAll('[data-copy]');
    Array.prototype.forEach.call(copyBtns, function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var url = btn.getAttribute('data-copy');
        btn.textContent = '✓ Готово';
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).then(function () {
            setTimeout(function () { btn.textContent = btn.getAttribute('data-label'); }, 1500);
          }).catch(function () {
            fallbackCopy(url, btn);
          });
        } else {
          fallbackCopy(url, btn);
        }
      });
    });
    container.querySelectorAll('[data-qr]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        showQr(btn.getAttribute('data-qr'), btn.getAttribute('data-qrname'));
      });
    });
  }

  function fallbackCopy(url, btn) {
    var ta = document.createElement('textarea');
    ta.value = url;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
    btn.textContent = '✓ Готово';
    setTimeout(function () { btn.textContent = btn.getAttribute('data-label'); }, 1500);
  }

  function showQr(slug, name) {
    var modal = document.getElementById('qr-modal');
    document.getElementById('qr-title').textContent = name;
    var img = document.getElementById('qr-image');
    var hint = document.getElementById('qr-hint');
    var worker = window.QR_WORKER || '';
    if (!worker || /_REPLACE_/.test(worker)) {
      img.style.display = 'none';
      hint.textContent = 'Генерация QR-кода недоступна (сервер не настроен).';
      modal.classList.remove('hidden');
      document.getElementById('qr-close').onclick = function () { hideQr(); };
      modal.onclick = function (e) { if (e.target === modal) hideQr(); };
      return;
    }
    img.style.display = '';
    hint.textContent = 'Код одноразовый и действует 10 минут. Наведите камеру телефона, чтобы начать установку.';
    img.src = worker + '/api/qr/' + encodeURIComponent(slug) + '.svg?t=' + Date.now();
    modal.classList.remove('hidden');
    document.getElementById('qr-close').onclick = function () { hideQr(); };
    modal.onclick = function (e) { if (e.target === modal) hideQr(); };
  }

  function hideQr() {
    document.getElementById('qr-modal').classList.add('hidden');
  }

  document.getElementById('search').addEventListener('input', render);
  document.getElementById('sort').addEventListener('change', render);

  fetch('catalog.json').then(function (r) { return r.json(); }).then(function (list) {
    APPS = Array.isArray(list) ? list : [];
    render();
  }).catch(function () {
    container.innerHTML = '<p style="padding:1rem;color:#6b7280">Не удалось загрузить каталог.</p>';
  });
})();