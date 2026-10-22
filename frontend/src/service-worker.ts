/// <reference types="@sveltejs/kit" />
/// <reference no-default-lib="true"/>
/// <reference lib="esnext" />
/// <reference lib="webworker" />
import { version } from '$app/env';
import { assets, immutable } from '$app/manifest';

const build = immutable.map((item) => item.path);
const files = assets.map((item) => item.path);

const worker = self as unknown as ServiceWorkerGlobalScope;
const CACHE = `expenselens-${version}`;
const SHELL = '/';

worker.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE);
    await cache.addAll([...build, ...files]);
    try { await cache.add(SHELL); } catch { /* shell is cached on first online visit instead */ }
    await worker.skipWaiting();
  })());
});

worker.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await worker.clients.claim();
  })());
});

worker.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/health') return;

  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(request);
        if (fresh.ok && url.pathname === SHELL) (await caches.open(CACHE)).put(SHELL, fresh.clone());
        return fresh;
      } catch {
        return (await caches.match(SHELL)) ?? Response.error();
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cached = await caches.match(request);
    if (cached) return cached;
    const response = await fetch(request);
    if (response.ok && (build.includes(url.pathname) || files.includes(url.pathname))) {
      (await caches.open(CACHE)).put(request, response.clone());
    }
    return response;
  })());
});
