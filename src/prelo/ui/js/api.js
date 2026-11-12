// Cliente da API local. O token vai num cookie (definido ao abrir a janela),
// então basta mandar as requisições para a mesma origem.

export class ApiError extends Error {
  constructor(data, status) {
    super(data.message || 'Erro inesperado.');
    this.code = data.code || 'erro';
    this.hint = data.hint || '';
    this.status = status;
  }
}

async function req(method, path, body, { keepalive = false } = {}) {
  const init = { method, credentials: 'same-origin', headers: {}, keepalive };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    throw new ApiError({ code: 'offline', message: 'A API local não respondeu.', hint: 'Feche e abra o Prelo de novo.' }, 0);
  }
  if (!r.ok) {
    let data = {};
    try { data = await r.json(); } catch (e) { /* corpo vazio */ }
    if (!data.message && data.detail) data.message = typeof data.detail === 'string' ? data.detail : 'Requisição inválida.';
    throw new ApiError(data, r.status);
  }
  const type = r.headers.get('content-type') || '';
  return type.includes('application/json') ? r.json() : r;
}

const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null)).toString();

export const api = {
  health: () => req('GET', '/api/health'),
  formats: () => req('GET', '/api/formats'),
  profiles: () => req('GET', '/api/profiles'),
  options: () => req('GET', '/api/options'),
  books: () => req('GET', '/api/books'),
  book: (id) => req('GET', `/api/books/${id}`),
  addPaths: (paths) => req('POST', '/api/books', { paths }),
  upload: (files) => {
    const fd = new FormData();
    for (const f of files) fd.append('files', f, f.name);
    return req('POST', '/api/books/upload', fd);
  },
  patchBook: (id, body) => req('PATCH', `/api/books/${id}`, body),
  deleteBook: (id) => req('DELETE', `/api/books/${id}`),
  pages: (id) => req('GET', `/api/books/${id}/pages`),
  coverUrl: (id) => `/api/books/${id}/cover`,
  previewUrl: (id, page, params = {}) => `/api/books/${id}/pages/${page}/preview?${qs(params)}`,
  readerInfo: (id, params) => req('GET', `/api/books/${id}/reader?${qs(params)}`),
  readerPageUrl: (id, page, params = {}) => `/api/books/${id}/reader/pages/${page}?${qs(params)}`,
  readerLinks: (id, page, params) => req('GET', `/api/books/${id}/reader/pages/${page}/links?${qs(params)}`),
  // keepalive: a posição chega à API mesmo se a janela fechar logo depois
  saveReading: (id, body, keepalive = false) => req('PUT', `/api/books/${id}/reader/position`, body, { keepalive }),
  jobs: () => req('GET', '/api/jobs'),
  newJob: (body) => req('POST', '/api/jobs', body),
  cancelJob: (id) => req('POST', `/api/jobs/${id}/cancel`),
  deleteJob: (id) => req('DELETE', `/api/jobs/${id}`),
  reveal: (id) => req('POST', `/api/jobs/${id}/reveal`),
  downloadUrl: (id) => `/api/jobs/${id}/download`,
  settings: () => req('GET', '/api/settings'),
  saveSettings: (body) => req('PUT', '/api/settings', body),
  quit: () => req('POST', '/api/quit'),
};
