import { get } from 'svelte/store';
import { t, translateServerError } from './i18n';
import type { Category, Expense, ExpenseWrite, Report, Tag, User } from './types';

let csrfToken = '';

export class NetworkError extends Error {
  constructor() { super(get(t)('common.offline')); }
}
export const isNetworkError = (e: unknown): e is NetworkError => e instanceof NetworkError;

export interface SessionInfo { id: string; current: boolean; created_at: string | null; last_seen_at: string | null; user_agent: string; method: string; }
export interface PasskeyInfo { id: number; name: string; created_at: string; last_used_at: string | null; }
export interface BudgetItem { category_id: number; name: string; color: string; budget: string; spent: string; ratio: number; }
export interface RatesInfo { base: string; auto: boolean; rates: { currency: string; rate: string; updated_at: string }[]; used_currencies: string[]; }
export interface TelegramPrefs { linked: boolean; language?: 'it' | 'en'; notify_period?: 'off' | 'weekly' | 'monthly'; notify_budget?: boolean; }

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  if (['POST', 'PUT', 'DELETE'].includes(init.method ?? 'GET') && csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { ...init, headers, credentials: 'include' });
  } catch {
    throw new NetworkError();
  }
  if (!response.ok) {
    const tr = get(t);
    const fallback = response.status === 429 ? tr('common.rateLimit') : tr('common.genericError');
    const payload = await response.json().catch(() => ({ detail: fallback }));
    throw new Error(typeof payload.detail === 'string' ? translateServerError(payload.detail) : fallback);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  async session(): Promise<User> {
    const user = await request<User>('/auth/me');
    csrfToken = (await request<{ csrf_token: string }>('/auth/session')).csrf_token;
    return user;
  },
  async login(email: string, password: string, totp_code?: string): Promise<User> {
    const data = await request<{ user: User; csrf_token: string }>('/auth/login', {
      method: 'POST', body: JSON.stringify({ email, password, totp_code })
    });
    csrfToken = data.csrf_token;
    return data.user;
  },
  async register(input: { email: string; password: string; display_name: string; household_name?: string; invite_token?: string }): Promise<User> {
    const data = await request<{ user: User; csrf_token: string }>('/auth/register', {
      method: 'POST', body: JSON.stringify(input)
    });
    csrfToken = data.csrf_token;
    return data.user;
  },
  async createHouseholdInvite(): Promise<{ token: string; expires_in_seconds: number }> {
    return request('/auth/household/invite', { method: 'POST' });
  },
  householdMembers(): Promise<User[]> { return request('/household/members'); },
  updateHouseholdMember(id: number, input: { email: string; display_name: string }): Promise<User> {
    return request(`/household/members/${id}`, { method: 'PUT', body: JSON.stringify(input) });
  },
  deleteHouseholdMember(id: number): Promise<void> {
    return request(`/household/members/${id}`, { method: 'DELETE' });
  },
  mfaSetup(): Promise<{ secret: string; qr_data_url: string }> {
    return request('/auth/mfa/setup', { method: 'POST' });
  },
  mfaEnable(code: string): Promise<{ recovery_codes: string[] }> {
    return request('/auth/mfa/enable', { method: 'POST', body: JSON.stringify({ code }) });
  },
  mfaDisable(code: string): Promise<void> {
    return request('/auth/mfa/disable', { method: 'POST', body: JSON.stringify({ code }) });
  },
  changePassword(current_password: string, new_password: string, totp_code?: string): Promise<{ revoked: number }> {
    return request('/auth/password', { method: 'POST', body: JSON.stringify({ current_password, new_password, totp_code: totp_code || null }) });
  },
  sessions(): Promise<SessionInfo[]> { return request('/auth/sessions'); },
  revokeSession(id: string): Promise<void> { return request(`/auth/sessions/${encodeURIComponent(id)}`, { method: 'DELETE' }); },
  revokeOtherSessions(): Promise<{ revoked: number }> { return request('/auth/sessions/revoke-others', { method: 'POST' }); },
  passkeys(): Promise<PasskeyInfo[]> { return request('/auth/passkeys'); },
  passkeyRegisterOptions(): Promise<{ options: Record<string, any>; challenge_token: string }> {
    return request('/auth/passkeys/register/options', { method: 'POST' });
  },
  passkeyRegisterVerify(body: { challenge_token: string; credential: unknown; name: string }): Promise<void> {
    return request('/auth/passkeys/register/verify', { method: 'POST', body: JSON.stringify(body) });
  },
  deletePasskey(id: number): Promise<void> { return request(`/auth/passkeys/${id}`, { method: 'DELETE' }); },
  passkeyLoginOptions(): Promise<{ options: Record<string, any>; challenge_token: string }> {
    return request('/auth/passkeys/login/options', { method: 'POST' });
  },
  async passkeyLoginVerify(body: { challenge_token: string; credential: unknown }): Promise<User> {
    const data = await request<{ user: User; csrf_token: string }>('/auth/passkeys/login/verify', {
      method: 'POST', body: JSON.stringify(body)
    });
    csrfToken = data.csrf_token;
    return data.user;
  },
  budgets(): Promise<{ currency: string; items: BudgetItem[] }> { return request('/budgets'); },
  setBudget(categoryId: number, monthly_budget: number | null): Promise<Category> {
    return request(`/categories/${categoryId}/budget`, { method: 'PUT', body: JSON.stringify({ monthly_budget }) });
  },
  rates(): Promise<RatesInfo> { return request('/rates'); },
  setRate(currency: string, rate: number): Promise<void> {
    return request(`/rates/${encodeURIComponent(currency)}`, { method: 'PUT', body: JSON.stringify({ rate }) });
  },
  deleteRate(currency: string): Promise<void> { return request(`/rates/${encodeURIComponent(currency)}`, { method: 'DELETE' }); },
  refreshRates(): Promise<{ updated: number }> { return request('/rates/refresh', { method: 'POST' }); },
  telegramPrefs(): Promise<TelegramPrefs> { return request('/telegram/preferences'); },
  setTelegramPrefs(body: Partial<TelegramPrefs>): Promise<TelegramPrefs> {
    return request('/telegram/preferences', { method: 'PUT', body: JSON.stringify(body) });
  },
  async logout(): Promise<void> { await request('/auth/logout', { method: 'POST' }); csrfToken = ''; },
  expenses(params = ''): Promise<Expense[]> { return request(`/expenses${params}`); },
  createExpense(body: ExpenseWrite): Promise<Expense> {
    return request('/expenses', { method: 'POST', body: JSON.stringify(body) });
  },
  updateExpense(id: number, body: ExpenseWrite): Promise<Expense> {
    return request(`/expenses/${id}`, { method: 'PUT', body: JSON.stringify(body) });
  },
  duplicateExpense(id: number): Promise<Expense> {
    return request(`/expenses/${id}/duplicate`, { method: 'POST' });
  },
  deleteExpense(id: number): Promise<void> { return request(`/expenses/${id}`, { method: 'DELETE' }); },
  categories(): Promise<Category[]> { return request('/categories'); },
  tags(): Promise<Tag[]> { return request('/tags'); },
  report(start: string, end: string, currency: string): Promise<Report> {
    return request(`/reports?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}&currency=${encodeURIComponent(currency)}`);
  },
  createTelegramLink(): Promise<{ token: string; expires_in_seconds: number }> {
    return request('/auth/telegram/link-token', { method: 'POST' });
  },
  async extract(file: File): Promise<{ document_key: string; extraction: import('./types').ReceiptExtraction }> {
    const data = new FormData();
    data.set('file', file);
    return request('/documents/extract', { method: 'POST', body: data });
  },
  async createTaxonomy(kind: 'categories' | 'tags', name: string, color?: string,
                       category_ids: number[] = []): Promise<void> {
    await request(`/${kind}`, {
      method: 'POST',
      body: JSON.stringify({ name, ...(color ? { color } : {}), category_ids })
    });
  },
  updateTagCategories(id: number, category_ids: number[]): Promise<Tag> {
    return request(`/tags/${id}`, {
      method: 'PUT', body: JSON.stringify({ category_ids })
    });
  },
  async deleteTaxonomy(kind: 'categories' | 'tags', id: number): Promise<void> {
    await request(`/${kind}/${id}`, { method: 'DELETE' });
  }
};
