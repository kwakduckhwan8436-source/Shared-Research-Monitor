/* 서비스워커 — 정적 셸만 캐시. API 데이터는 항상 네트워크에서 최신을 받는다. */
const CACHE = "reco-static-v3";
const STATIC_ASSETS = ["/", "/index.html", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC_ASSETS)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API 호출은 절대 캐시하지 않는다(항상 최신 데이터).
  if (url.pathname.startsWith("/api/")) {
    return; // 기본 네트워크 동작
  }
  // 정적 파일: 네트워크 우선, 실패 시 캐시(오프라인 대비)
  if (e.request.method === "GET") {
    e.respondWith(
      fetch(e.request)
        .then((res) => {
          if (res && res.status === 200 && url.origin === self.location.origin) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(e.request).then((r) => r || caches.match("/index.html")))
    );
  }
});
