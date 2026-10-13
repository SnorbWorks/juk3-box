// Talking to jb-admin. Every change carries the admin header (the server
// refuses changes without it); errors come back as ApiError with the
// server's message and any per-field problems.

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.error || `Something went wrong (${status}).`);
    this.status = status;
    this.problems = body?.problems;
  }
}

let onLoggedOut = () => {};
export function whenLoggedOut(fn) { onLoggedOut = fn; }

export async function api(method, path, body) {
  const opts = { method, headers: { 'X-JB-Admin': '1' }, credentials: 'same-origin' };
  if (body instanceof FormData) {
    opts.body = body;
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch {
    throw new ApiError(0, { error: 'Can\'t reach the jukebox. Is it on?' });
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith('/api/login') && !path.startsWith('/api/password')) onLoggedOut();
    throw new ApiError(res.status, data);
  }
  return data;
}

export const get = path => api('GET', path);
export const put = (path, body) => api('PUT', path, body);
export const post = (path, body) => api('POST', path, body);
export const del = path => api('DELETE', path);

// An upload with progress (fetch can't report upload progress).
export function upload(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.setRequestHeader('X-JB-Admin', '1');
    xhr.upload.onprogress = e => e.lengthComputable && onProgress?.(e.loaded / e.total);
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else {
        if (xhr.status === 401) onLoggedOut();
        reject(new ApiError(xhr.status, data));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, { error: 'The upload stopped. Is the jukebox on?' }));
    const form = new FormData();
    form.append('file', file, file.name);
    xhr.send(form);
  });
}
