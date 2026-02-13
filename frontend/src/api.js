export async function backendFetch(path, { session, headers = {}, ...rest } = {}) {
  const url = `${import.meta.env.VITE_BACKEND_URL || 'http://localhost:8000'}${path}`
  const h = { ...headers }
  if (session?.access_token) h['Authorization'] = `Bearer ${session.access_token}`
  const res = await fetch(url, { headers: h, ...rest })
  return res
}
