const BACKEND_API_STORAGE_KEY = 'backend_api_url'

function normalizeBackendApiUrl(value: string) {
  return value.trim().replace(/\/+$/, '')
}

export function validateBackendApiUrl(value: string) {
  const normalized = normalizeBackendApiUrl(value)
  if (!normalized) return true
  try {
    const url = new URL(normalized)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

export function resolveBackendApiUrl() {
  const configured = localStorage.getItem(BACKEND_API_STORAGE_KEY)
  return normalizeBackendApiUrl(configured ?? import.meta.env.VITE_API_BASE_URL ?? '')
}

export function setBackendApiUrl(value: string) {
  const normalized = normalizeBackendApiUrl(value)
  if (!validateBackendApiUrl(normalized)) {
    throw new Error('Backend API URL must be an HTTP(S) origin or empty for same-origin proxy mode.')
  }
  if (normalized) {
    localStorage.setItem(BACKEND_API_STORAGE_KEY, normalized)
  } else {
    localStorage.removeItem(BACKEND_API_STORAGE_KEY)
  }
  return normalized
}

export function getBackendApiStorageKey() {
  return BACKEND_API_STORAGE_KEY
}
