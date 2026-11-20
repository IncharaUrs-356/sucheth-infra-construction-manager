const CACHE='sucheth-infra-android-v1';
const SHELL=['/','/static/style.css','/static/app.js','/manifest.json','/static/icons/icon-192.png','/static/icons/icon-512.png'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(key=>key!==CACHE).map(key=>caches.delete(key))))));
self.addEventListener('fetch',event=>{const req=event.request;if(req.method!=='GET'||new URL(req.url).origin!==self.location.origin)return;const url=new URL(req.url);if(url.pathname.startsWith('/api/')||url.pathname==='/login'||url.pathname==='/logout')return;if(url.pathname.startsWith('/static/')){event.respondWith(caches.match(req).then(hit=>hit||fetch(req).then(response=>{const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(req,copy));return response})));return;}event.respondWith(fetch(req).catch(()=>caches.match('/')))});
