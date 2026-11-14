const CACHE = 'ashare-lens-v8'
const SHELL = ['./', './index.html', './styles.css', './app.js', './lib.js', './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png', './icons/apple-touch-icon.png']

self.addEventListener('install', event => event.waitUntil(
  caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()),
))

self.addEventListener('activate', event => event.waitUntil(
  caches.keys()
    .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
    .then(() => self.clients.claim()),
))

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return
  const url = new URL(event.request.url)
  const isSameOriginData = url.origin === self.location.origin && url.pathname.includes('/data/')
  if (isSameOriginData) {
    event.respondWith(
      fetch(event.request)
        .then(async response => {
          if (response.ok) {
            const cache = await caches.open(CACHE)
            await cache.put(event.request, response.clone())
          }
          return response
        })
        .catch(() => caches.match(event.request)),
    )
    return
  }
  event.respondWith(caches.match(event.request).then(hit => hit || fetch(event.request)))
})
