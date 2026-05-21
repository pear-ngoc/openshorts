// Configuration for API endpoints
// If VITE_API_URL is set (e.g. in production), use it.
// Otherwise, default to empty string which means relative paths (proxied in dev).

export const API_BASE_URL = import.meta.env.VITE_API_URL || '';
// Render service: use relative path (/render) to leverage Vite proxy to http://renderer:3100
export const RENDER_SERVICE_URL = import.meta.env.VITE_RENDER_SERVICE_URL || '';

export const getApiUrl = (path) => {
    if (!path) return '';
    if (path.startsWith('http')) return path;
    // Ensure path starts with / if not present
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${API_BASE_URL}${normalizedPath}`;
};
