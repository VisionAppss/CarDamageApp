'use strict';

window.carscanMetricsAllowed = (async () => {
  if (new URLSearchParams(location.search).get('admin') === '1') return false;

  try {
    const response = await fetch('/api/metrics/context', {
      cache: 'no-store',
      signal: AbortSignal.timeout(3500)
    });
    if (!response.ok || (await response.json()).internal !== false) return false;
  } catch {
    return false;
  }

  window.dataLayer = window.dataLayer || [];
  window.ym = window.ym || function () { (window.ym.a ||= []).push(arguments); };
  window.ym.l = Date.now();
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://mc.yandex.ru/metrika/tag.js?id=108345172';
  document.head.append(script);
  window.ym(108345172, 'init', {
    ssr: true,
    webvisor: true,
    clickmap: true,
    ecommerce: 'dataLayer',
    referrer: document.referrer,
    url: location.href,
    accurateTrackBounce: true,
    trackLinks: true
  });
  return true;
})();
