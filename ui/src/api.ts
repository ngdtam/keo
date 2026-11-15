async function handleResponse(res: Response): Promise<Response> {
  if (res.status === 401) {
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const data = await res.json()
      if (data && typeof data.detail === 'string' && data.detail) {
        detail = data.detail
      }
    } catch {
      // ignore non-JSON error bodies
    }
    throw new Error(detail)
  }
  return res
}

export async function apiGet<T>(url: string): Promise<T> {
  const res = await handleResponse(
    await fetch(url, { credentials: 'same-origin' }),
  )
  return res.json() as Promise<T>
}

export async function apiPostJson<T>(url: string, body?: unknown): Promise<T> {
  const res = await handleResponse(
    await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
  )
  return res.json() as Promise<T>
}

export async function apiPostForm(
  url: string,
  data?: Record<string, string | string[] | File | Blob>,
): Promise<void> {
  const form = new FormData()
  if (data) {
    for (const [key, value] of Object.entries(data)) {
      if (Array.isArray(value)) {
        for (const item of value) {
          form.append(key, item)
        }
      } else {
        form.append(key, value)
      }
    }
  }
  await handleResponse(
    await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    }),
  )
}
