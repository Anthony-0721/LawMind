export const DEFAULT_BASE_URL = '/api/law'

export class LawApiError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'LawApiError'
    this.status = options.status ?? 0
    this.detail = options.detail ?? ''
    this.body = options.body ?? ''
    this.json = options.json ?? null
  }
}

function getBaseUrl() {
  return import.meta.env?.VITE_LAW_API_BASE_URL || DEFAULT_BASE_URL
}

function joinUrl(path) {
  const base = getBaseUrl().replace(/\/+$/, '')
  return path.startsWith('/') ? `${base}${path}` : `${base}/${path}`
}

function authHeaders(sessionToken = '', password = '') {
  const headers = new Headers()
  if (sessionToken) headers.set('X-Session-Token', sessionToken)
  if (password) headers.set('X-Admin-Password', password)
  return headers
}

async function parseError(response) {
  let body = ''
  try {
    body = await response.text()
  } catch {
    body = ''
  }
  let json = null
  try {
    json = body ? JSON.parse(body) : null
  } catch {
    json = null
  }
  const detail = json && typeof json === 'object'
    ? json.detail ?? json.error ?? json.message ?? ''
    : ''
  const reason = response.statusText || `HTTP ${response.status}`
  const message = [reason, detail, body].filter(Boolean).join(' — ')
  return new LawApiError(message, { status: response.status, detail, body, json })
}

async function requestJson(path, options = {}) {
  const {
    method = 'GET',
    body,
    headers,
    ...rest
  } = options
  const finalHeaders = new Headers(headers || {})
  const hasBody = body !== undefined
  if (hasBody && !finalHeaders.has('Content-Type')) {
    finalHeaders.set('Content-Type', 'application/json')
  }
  const response = await fetch(joinUrl(path), {
    ...rest,
    method,
    headers: finalHeaders,
    body: hasBody ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw await parseError(response)
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function withToken(sessionToken = '') {
  return { headers: authHeaders(sessionToken) }
}

function withAdmin(password = '') {
  return { headers: authHeaders('', password) }
}

export function lawChat(payload, sessionToken = '') {
  return requestJson('/chat', {
    method: 'POST',
    body: payload,
    ...withToken(sessionToken),
  })
}

export function getOptions() {
  return requestJson('/options')
}

export function getLawyers(domain = '') {
  const query = domain ? `?domain=${encodeURIComponent(domain)}` : ''
  return requestJson(`/lawyers${query}`)
}

export function saveConsultation(payload, sessionToken = '') {
  const body = { ...payload }
  if (sessionToken && body.session_token === undefined) {
    body.session_token = sessionToken
  }
  return requestJson('/consultations', {
    method: 'POST',
    body,
    ...withToken(sessionToken),
  })
}

export function transferToHuman(payload, sessionToken = '') {
  const body = { ...payload }
  if (sessionToken && body.session_token === undefined) {
    body.session_token = sessionToken
  }
  return requestJson('/transfer', {
    method: 'POST',
    body,
    ...withToken(sessionToken),
  })
}

export function staffLogin(password = '') {
  return requestJson('/admin/login', {
    method: 'POST',
    body: { password },
    ...withAdmin(password),
  })
}

export function listConsultations(password = '', limit = 50) {
  return requestJson(`/admin/consultations?limit=${limit}`, withAdmin(password))
}

export function getConsultation(id, password = '') {
  return requestJson(`/admin/consultations/${encodeURIComponent(id)}`, withAdmin(password))
}

export function updateConsultationStatus(id, status, password = '') {
  return requestJson(`/admin/consultations/${encodeURIComponent(id)}/status`, {
    method: 'PATCH',
    body: { status },
    ...withAdmin(password),
  })
}

export function deleteConsultation(id, password = '') {
  return requestJson(`/admin/consultations/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    ...withAdmin(password),
  })
}

export function listLawyers(password = '') {
  return requestJson('/admin/lawyers', withAdmin(password))
}

export function createLawyer(payload, password = '') {
  return requestJson('/admin/lawyers', {
    method: 'POST',
    body: payload,
    ...withAdmin(password),
  })
}

export function updateLawyer(id, payload, password = '') {
  return requestJson(`/admin/lawyers/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: payload,
    ...withAdmin(password),
  })
}

export function toggleLawyer(id, active, password = '') {
  return requestJson(`/admin/lawyers/${encodeURIComponent(id)}/toggle`, {
    method: 'PATCH',
    body: active === undefined ? undefined : { active: Boolean(active) },
    ...withAdmin(password),
  })
}

export function listFaqs(password = '', activeOnly = false) {
  return requestJson(`/admin/faqs?active_only=${activeOnly ? 'true' : 'false'}`, withAdmin(password))
}

export function createFaq(payload, password = '') {
  return requestJson('/admin/faqs', {
    method: 'POST',
    body: payload,
    ...withAdmin(password),
  })
}

export function updateFaq(id, payload, password = '') {
  return requestJson(`/admin/faqs/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: payload,
    ...withAdmin(password),
  })
}

export function deleteFaq(id, password = '') {
  return requestJson(`/admin/faqs/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    ...withAdmin(password),
  })
}

export function toggleFaq(id, active, password = '') {
  return requestJson(`/admin/faqs/${encodeURIComponent(id)}/toggle`, {
    method: 'PATCH',
    body: active === undefined ? undefined : { active: Boolean(active) },
    ...withAdmin(password),
  })
}

export function reloadKnowledge(password = '') {
  return requestJson('/admin/knowledge/reload', {
    method: 'POST',
    ...withAdmin(password),
  })
}

export function getAdminMetrics(password = '') {
  return requestJson('/admin/metrics', withAdmin(password))
}
