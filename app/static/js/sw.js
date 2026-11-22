// Service worker: кеширует оформление и показывает страницу "нет соединения".
// Личные данные студента не кешируются.
// Версию (хеш содержимого файлов) подставляет сервер при выдаче этого файла, см. app/web.py
var V = "__ASSET_VERSION__";
var CACHE = "dn-static-" + V;
var ASSETS = ["/static/css/app.css?v=" + V, "/static/js/app.js?v=" + V, "/static/js/theme.js?v=" + V,
              "/static/icons/sprite.svg?v=" + V, "/static/img/logo.svg?v=" + V, "/offline"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(ASSETS); }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (url.pathname.indexOf("/static/") === 0) {
    e.respondWith(caches.match(req).then(function (hit) { return hit || fetch(req); }));
    return;
  }
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(function () { return caches.match("/offline"); }));
  }
});
