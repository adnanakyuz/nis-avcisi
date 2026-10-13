// Niş Avcısı service worker: önce internetten en güncel hali dener, internet yoksa son kaydedileni gösterir.
// Uygulama mağazası (Play Store) paketlemesi için de gerekli.
const ONBELLEK = "nis-avcisi-v1";
const TEMEL = ["./", "index.html", "profil.html", "gizlilik.html", "manifest.json", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(ONBELLEK).then((c) => c.addAll(TEMEL)).catch(() => {}));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((adlar) => Promise.all(adlar.filter((a) => a !== ONBELLEK).map((a) => caches.delete(a))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const istek = e.request;
  if (istek.method !== "GET" || new URL(istek.url).origin !== self.location.origin) return;
  e.respondWith(
    fetch(istek)
      .then((yanit) => {
        const kopya = yanit.clone();
        caches.open(ONBELLEK).then((c) => c.put(istek, kopya));
        return yanit;
      })
      .catch(() => caches.match(istek).then((m) => m || caches.match("index.html")))
  );
});
