import type { ApiResponse, GlobalContext } from '@/types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const json = (await res.json()) as ApiResponse<T>
  if (json.code !== 0) {
    throw new Error(json.message || `Request failed: ${path}`)
  }
  return json.data as T
}

export const api = {
  // Health
  health: () => request<any>('/health'),

  // GCP
  getGcpContext: () => request<GlobalContext>('/api/gcp/context'),
  updateGcpContext: (updates: Record<string, any>) =>
    request<string[]>('/api/gcp/context', { method: 'PUT', body: JSON.stringify(updates) }),
  updateGcpModule: (module: string, data: Record<string, any>) =>
    request<string[]>(`/api/gcp/module/${module}`, { method: 'POST', body: JSON.stringify(data) }),
  listPresets: () => request<Record<string, string>>('/api/gcp/presets'),
  loadPreset: (name: string) =>
    request<string>(`/api/gcp/presets/${name}/load`, { method: 'POST' }),
  listProfiles: () =>
    request<Record<string, { id: string; name: string; age: number; occupation: string }>>(
      '/api/gcp/profiles',
    ),
  loadProfile: (key: string) =>
    request<string>(`/api/gcp/profiles/${key}/load`, { method: 'POST' }),
}
