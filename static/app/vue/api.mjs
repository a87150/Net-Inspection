/* Minimal REST client: same-origin session auth, CSRF on writes, one error shape. */

export class ApiError extends Error {
    constructor(message, status, payload) {
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.payload = payload;
    }
}

export function csrfToken(doc = document) {
    const input = doc.querySelector('input[name="csrfmiddlewaretoken"]');
    if (input && input.value) return input.value;
    const match = (doc.cookie || '').match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : '';
}

export function buildUrl(path, params, origin = window.location.origin) {
    const url = new URL(path, origin);
    if (params) {
        Object.entries(params).forEach(([key, value]) => {
            if (value === '' || value === null || value === undefined) return;
            url.searchParams.set(key, value);
        });
    }
    return url;
}

async function readPayload(response) {
    if (response.status === 204) return null;
    const type = response.headers.get('content-type') || '';
    if (!type.includes('application/json')) return null;
    try {
        return await response.json();
    } catch (error) {
        return null;
    }
}

export async function apiRequest(path, options = {}) {
    const {method = 'GET', body, params, signal, origin} = options;
    const init = {
        method,
        credentials: 'same-origin',
        headers: {Accept: 'application/json'},
        signal,
    };
    if (method !== 'GET' && method !== 'HEAD') {
        init.headers['Content-Type'] = 'application/json';
        init.headers['X-CSRFToken'] = csrfToken();
        init.body = JSON.stringify(body ?? {});
    }
    const response = await fetch(buildUrl(path, params, origin), init);
    const payload = await readPayload(response);
    if (!response.ok) {
        const message = (payload && payload.message) || '请求失败（' + response.status + '）';
        throw new ApiError(message, response.status, payload);
    }
    return payload;
}

export const api = {
    get: (path, params, signal) => apiRequest(path, {params, signal}),
    post: (path, body) => apiRequest(path, {method: 'POST', body}),
    delete: (path) => apiRequest(path, {method: 'DELETE'}),
};
