// No offline cache: never store HTML CSRF tokens, broker data or credentials.
self.addEventListener("install",()=>self.skipWaiting());
self.addEventListener("activate",event=>event.waitUntil(self.clients.claim()));
self.addEventListener("fetch",event=>{if(event.request.method==="GET"&&event.request.mode==="navigate")event.respondWith(fetch(event.request).catch(()=>new Response("CCS local server is offline. Restart it in your terminal.",{headers:{"Content-Type":"text/plain"}})));});