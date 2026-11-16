// Service worker do OndeToBus.
//
// Estratégia:
//  - HTML, CSS e JS do app: REDE PRIMEIRO (sempre tenta baixar a versão nova;
//    se estiver sem internet, usa a cópia guardada). Assim, depois de um
//    deploy, o usuário já recebe o código novo, sem precisar recarregar 2x.
//  - Ícones e manifest: cache primeiro (quase nunca mudam).
//  - /api/*: sempre rede (dados de ônibus em tempo real não podem vir de cache).
//
// Sempre que mudar o app, troque o CACHE_NAME (v3, v4...) para limpar o cache antigo.
const CACHE_NAME = "ondetobus-v3";

// Só pré-carrega o essencial; se algum arquivo não existir, ignora (não quebra).
const APP_SHELL = ["/", "/static/app.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      Promise.allSettled(APP_SHELL.map((url) => cache.add(url)))
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  if (request.method !== "GET") return;

  // Chamadas de API: sempre rede, nunca cache.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(request));
    return;
  }

  // Recursos externos (fontes, Leaflet via unpkg, OSRM): o navegador cuida.
  if (url.origin !== self.location.origin) return;

  const isCode =
    request.mode === "navigate" ||
    url.pathname === "/" ||
    /\.(js|css|html)$/.test(url.pathname);

  if (isCode) {
    // Rede primeiro; cópia guardada só se estiver offline.
    event.respondWith(
      caches.open(CACHE_NAME).then(async (cache) => {
        try {
          const response = await fetch(request);
          if (response && response.status === 200) cache.put(request, response.clone());
          return response;
        } catch (e) {
          return (await cache.match(request)) || Response.error();
        }
      })
    );
    return;
  }

  // Ícones, manifest etc.: cache primeiro, atualizando em segundo plano.
  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response && response.status === 200) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
