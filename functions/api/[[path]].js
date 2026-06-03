const STATIC_ROUTES = new Map([
  ['/api/health', '/_api/health.json'],
  ['/api/forecast', '/_api/forecast.json'],
  ['/api/outlook/latest', '/_api/outlook/latest.json'],
  ['/api/outlook/risk-polygons', '/_api/outlook/risk-polygons.geojson'],
  ['/api/outlook/aggregate-risk-polygons', '/_api/outlook/aggregate-risk-polygons.geojson'],
  ['/api/outlook/probability-tiles', '/_api/outlook/probability-tiles.json'],
  ['/api/outlook/verification', '/_api/outlook/verification.json'],
  ['/api/outlook/spc-day1-category', '/_api/outlook/spc-day1-category.geojson'],
  ['/api/outlook/trends', '/_api/outlook/trends.json'],
  ['/api/outlook/incremental', '/_api/outlook/incremental/index.json'],
  ['/api/outlook/incremental/available-hours', '/_api/outlook/incremental/index.json'],
  ['/api/outlook/incremental/summary', '/_api/outlook/incremental/summary.json'],
  ['/api/outlook/preview.png', '/_api/outlook/preview.png'],
]);

const HOUR_ROUTE = /^\/api\/outlook\/incremental\/hour\/(\d+)\/(risk-polygons|probability-tile|metadata)$/;

function apiHeaders(cacheControl, contentType = 'application/json; charset=utf-8') {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, If-None-Match, If-Modified-Since',
    'Cache-Control': cacheControl,
    'Content-Type': contentType,
  };
}

function resolveStaticPath(pathname, region, model = null, date = null) {
  const prefix = region === 'conus' ? '/_api/conus' : '/_api';

  // Available dates list
  if (pathname === '/api/outlook/merged-d1-available-dates') {
    return `${prefix}/outlook/merged-d1/available-dates.json`;
  }

  // Merged D1 endpoints
  const isMergedD1Verification = pathname === '/api/outlook/merged-d1-verification';
  const isMergedD1RiskPolygons = pathname === '/api/outlook/merged-d1-risk-polygons';
  const isMergedD1ProbabilityTile = pathname === '/api/outlook/merged-d1-probability-tile';
  const isSpcStormReports = pathname === '/api/outlook/spc-storm-reports';

  if (isMergedD1Verification || isMergedD1RiskPolygons || isMergedD1ProbabilityTile || isSpcStormReports) {
    const filename = isMergedD1Verification
      ? 'verification.json'
      : isMergedD1RiskPolygons
        ? 'risk-polygons.geojson'
        : isMergedD1ProbabilityTile
          ? 'probability-tile.json'
          : 'storm-reports.json';
        
    if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) {
      return `${prefix}/outlook/merged-d1/${date}/${filename}`;
    }
    return `${prefix}/outlook/merged-d1/${filename}`;
  }

  const direct = STATIC_ROUTES.get(pathname);
  if (direct) {
    return direct.replace('/_api', prefix);
  }

  const match = pathname.match(HOUR_ROUTE);
  if (!match) return null;

  const forecastHour = Number.parseInt(match[1], 10);
  const maxForecastHour = 48;
  if (!Number.isFinite(forecastHour) || forecastHour < 0 || forecastHour > maxForecastHour) return null;
  const hour = `f${String(forecastHour).padStart(2, '0')}`;
  const name = match[2] === 'risk-polygons' ? 'risk-polygons.geojson' : `${match[2]}.json`;

  if (prefix === '/_api') {
    return `/_api/outlook/incremental/hour/${hour}/${name}`;
  }
  return `${prefix}/outlook/incremental/hour/${hour}/${name}`;
}

function contentTypeFor(pathname) {
  if (pathname.endsWith('.png')) return 'image/png';
  if (pathname.endsWith('.geojson')) return 'application/geo+json; charset=utf-8';
  return 'application/json; charset=utf-8';
}

function cacheFor(apiPathname, staticPath) {
  if (apiPathname === '/api/health') return 'no-store';
  if (staticPath.endsWith('.png')) return 'public, max-age=900';
  return 'public, max-age=300';
}

async function fetchStaticAsset(context, staticPath) {
  const url = new URL(context.request.url);
  url.pathname = staticPath;
  url.search = '';
  return context.env.ASSETS.fetch(new Request(url.toString(), context.request));
}

function isMissingAssetResponse(response) {
  if (response.status === 404) return true;
  const contentType = response.headers.get('content-type') || '';
  return response.ok && contentType.toLowerCase().includes('text/html');
}

function notReady(pathname, status = 404) {
  return Response.json(
    {
      error: 'outlook not ready',
      code: 'outlook_not_ready',
      artifact: pathname.split('/').pop() || pathname,
    },
    {
      status,
      headers: apiHeaders('no-store'),
    },
  );
}

export async function onRequest(context) {
  if (context.request.method === 'OPTIONS') {
    return new Response(null, { status: 204, headers: apiHeaders('no-store') });
  }

  if (context.request.method !== 'GET' && context.request.method !== 'HEAD') {
    return new Response('Method Not Allowed', { status: 405, headers: apiHeaders('no-store', 'text/plain; charset=utf-8') });
  }

  const url = new URL(context.request.url);
  const pathname = url.pathname.replace(/\/+$/, '') || '/';
  const region = url.searchParams.get('region');
  const model = url.searchParams.get('model');
  const date = url.searchParams.get('date');

  let staticPath = resolveStaticPath(pathname, region, model, date);
  if (!staticPath) return notReady(pathname);

  let assetResponse = await fetchStaticAsset(context, staticPath);

  // If regional asset is not found, try falling back to the legacy root asset path
  if (isMissingAssetResponse(assetResponse) && region) {
    const fallbackPath = resolveStaticPath(pathname, null, null, date);
    if (fallbackPath && fallbackPath !== staticPath) {
      const fallbackResponse = await fetchStaticAsset(context, fallbackPath);
      if (!isMissingAssetResponse(fallbackResponse)) {
        assetResponse = fallbackResponse;
        staticPath = fallbackPath;
      }
    }
  }

  if (isMissingAssetResponse(assetResponse)) {
    const status = pathname === '/api/forecast' || pathname === '/api/health' ? 503 : 404;
    return notReady(pathname, status);
  }

  const headers = new Headers(assetResponse.headers);
  headers.set('Access-Control-Allow-Origin', '*');
  headers.set('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  headers.set('Access-Control-Allow-Headers', 'Content-Type, If-None-Match, If-Modified-Since');
  headers.set('Cache-Control', cacheFor(pathname, staticPath));
  headers.set('Content-Type', contentTypeFor(staticPath));

  return new Response(context.request.method === 'HEAD' ? null : assetResponse.body, {
    status: assetResponse.status,
    statusText: assetResponse.statusText,
    headers,
  });
}
