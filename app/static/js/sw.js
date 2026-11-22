// Service worker: кеширует оформление и показывает страницу "нет соединения".
// Личные данные студента не кешируются.
var CACHE = "dn-static-v8";
var ASSETS = ["/static/css/app.css?v=8", "/static/js/app.js?v=8", "/static/js/theme.js?v=8",
              "/static/icons/sprite.svg?v=8", "/static/img/logo.svg?v=8", "/offline"];

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
