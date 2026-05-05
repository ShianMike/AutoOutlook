const rawApiBase = import.meta.env.VITE_AUTOOUTLOOK_API_BASE?.trim() ?? '';

export const API_BASE = rawApiBase.replace(/\/+$/, '');

export function apiUrl(path: string): string {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  return `${API_BASE}${normalizedPath}`;
}
