/* 서비스워커 — 정적 셸만 캐시. API 데이터는 항상 네트워크에서 최신을 받는다.
   CACHE 버전을 올리면 옛 캐시가 자동 정리되고, 페이지에 '업데이트 있음'을 알린다. */
const CACHE = "reco-static-v4";
const STATIC_ASSETS = ["/", "/index.html", "/manifest.json", "/icon-192.png", "/icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(STATIC_ASSETS)).catch(() => {}));
  // skipWaiting 은 페이지가 명시적으로 요청할 때만(사용자가 '새로고침'을 누른 순간).
  // 자동으로 하면 사용 도중 셸이 바뀌어 화면이 깨질 수 있다.
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// 페이지가 "지금 새 버전으로 교체" 를 요청하면 대기 중 워커를 활성화한다.
self.addEventListener("message", (e) => {
  if (e.data === "SKIP_WAITING") self.skipWaiting();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // API 호출은 절대 캐시하지 않는다(항상 최신 데이터).
  if (url.pathname.startsWith("/api/")) return;      // 기본 네트워크 동작
  // 서비스워커 자신도 캐시 대상에서 제외(항상 최신 sw.js 확인).
  if (url.pathname === "/sw.js") return;
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
