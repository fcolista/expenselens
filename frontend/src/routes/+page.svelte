<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from 'svelte/store';
  import { api, isNetworkError } from '../lib/api';
  import type { BudgetItem, RatesInfo } from '../lib/api';
  import AccountExtras from '../lib/AccountExtras.svelte';
  import Icon, { CATEGORY_ICONS } from '../lib/Icon.svelte';
  import { clearSnapshot, loadSnapshot, queue, saveSnapshot } from '../lib/offline';
  import type { QueuedExpense } from '../lib/offline';
  import { loginWithPasskey, PasskeyCancelled, passkeysSupported } from '../lib/passkey';
  import SwipeRow from '../lib/SwipeRow.svelte';
  import { detectLang, isSessionError, lang, locale, setLang, t } from '../lib/i18n';
  import type { Lang } from '../lib/i18n';
  import type { Category, Expense, ExpenseWrite, Report, Tag, User } from '../lib/types';

  type View = 'expenses' | 'reports' | 'categories' | 'family' | 'settings';
  type Theme = 'system' | 'light' | 'dark';

  function localDate(value: Date): string {
    return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, '0')}-${String(value.getDate()).padStart(2, '0')}`;
  }

  function todayLocal(): string {
    return localDate(new Date());
  }

  const tt = (key: string, params?: Record<string, string | number>) => get(t)(key, params);
  const money = (value: number | string, currency: string, loc: string) =>
    Number(value).toLocaleString(loc, { style: 'currency', currency });

  let user: User | null = null;
  let expenses: Expense[] = [];
  let hasMoreExpenses = false;
  let categories: Category[] = [];
  let tags: Tag[] = [];
  let members: User[] = [];
  let report: Report | null = null;
  let view: View = 'expenses';
  let loading = true;
  let busy = false;
  let error = '';
  let notice = '';
  let theme: Theme = 'system';
  let registering = false;
  let showForm = false;
  let showFilters = false;
  let previewUrl = '';
  let showSecurity = false;
  let mfaQr = '';
  let mfaSecret = '';
  let recoveryCodes: string[] = [];
  let mfaCode = '';
  let editing: Expense | null = null;
  let period = 'month';
  let reportCurrency = 'EUR';
  let search = '';
  let start = localDate(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  let end = todayLocal();
  let expenseStart = '';
  let expenseEnd = '';
  let expenseCategory = '';
  let expenseSort = 'newest';
  let categoryName = '';
  let categoryColor = '#8fa39a';
  let categoryIcon = '';
  let categoryIconDraft = '';
  let categoryColorDraft = '#8fa39a';
  let editingCategoryId: number | null = null;
  let categoryNameDraft = '';
  let tagName = '';
  let tagCategoryIds: number[] = [];
  let tagCategoryDrafts: Record<number, number[]> = {};
  let editingMemberId: number | null = null;
  let memberDraft = { email: '', display_name: '' };
  let form = emptyForm();
  let offline = false;
  let queued: QueuedExpense[] = [];
  let syncing = false;
  let ratesInfo: RatesInfo | null = null;
  let budgets: BudgetItem[] = [];
  let budgetDrafts: Record<number, string> = {};
  let otherCurrency = '';
  const QUICK_CURRENCIES = ['EUR', 'USD', 'GBP', 'CHF'];

  function lastCurrency(): string {
    try { return localStorage.getItem('expenselens-currency') || 'EUR'; } catch { return 'EUR'; }
  }

  function emptyForm() {
    return {
      amount: '', currency: lastCurrency(), spent_on: todayLocal(),
      merchant: '', description: '', category_id: null as number | null, tag_ids: [] as number[],
      payment_method: '', visibility: 'shared' as 'shared' | 'personal', review_status: 'reviewed' as 'reviewed' | 'needs_review',
      document_key: null as string | null, tax_amount: '',
      items: [] as { description: string; quantity: string; unit_price: string; total: string }[]
    };
  }

  onMount(async () => {
    setLang(detectLang());
    const savedTheme = localStorage.getItem('expenselens-theme');
    theme = savedTheme === 'dark' || savedTheme === 'light' ? savedTheme : 'system';
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', () => { offline = true; });
    try {
      user = await api.session();
      await loadData();
      await syncQueue();
    } catch (e) {
      const snapshot = loadSnapshot<{ user: User; categories: Category[]; tags: Tag[]; expenses: Expense[] }>();
      if (isNetworkError(e) && snapshot) {
        offline = true;
        ({ user, categories, tags, expenses } = snapshot);
        queued = await queue.all().catch(() => []);
      } else {
        user = null;
        const detail = message(e);
        if (!isSessionError(detail)) error = detail;
      }
    } finally {
      loading = false;
    }
  });

  async function goOnline() {
    try {
      user = await api.session();
      offline = false;
      await syncQueue();
      await loadData();
    } catch { /* still unreachable or signed out: stay in offline mode */ }
  }

  async function refreshQueue() {
    queued = await queue.all().catch(() => []);
  }

  async function syncQueue() {
    if (syncing) return;
    await refreshQueue();
    const waiting = queued.filter((item) => !item.error);
    if (!waiting.length) return;
    syncing = true;
    let done = 0;
    try {
      for (const item of waiting) {
        try {
          await api.createExpense(item.payload);
          await queue.remove(item.id!);
          done += 1;
        } catch (e) {
          if (isNetworkError(e) || isSessionError(message(e))) break;
          await queue.put({ ...item, error: message(e) });
        }
      }
    } finally {
      syncing = false;
      await refreshQueue();
    }
    if (done) { notice = tt('off.synced', { n: done }); await loadData(); }
  }

  async function discardFailed() {
    for (const item of queued.filter((entry) => entry.error)) await queue.remove(item.id!);
    await refreshQueue();
  }

  async function retryFailed() {
    for (const item of queued.filter((entry) => entry.error)) await queue.put({ ...item, error: undefined });
    await syncQueue();
  }

  function chooseTheme(value: Theme) {
    theme = value;
    if (value === 'system') localStorage.removeItem('expenselens-theme');
    else localStorage.setItem('expenselens-theme', value);
  }

  $: if (typeof document !== 'undefined') {
    if (theme === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = theme;
  }

  async function loadData() {
    if (!user) return;
    error = '';
    try {
      [categories, tags] = await Promise.all([api.categories(), api.tags()]);
      syncTagDrafts();
      members = user.is_admin ? await api.householdMembers() : [];
      await loadExpenses();
      await loadReport();
      await loadExtras();
      saveSnapshot({ user, categories, tags, expenses: expenses.slice(0, 50) });
    } catch (e) { error = message(e); }
  }

  async function loadExtras() {
    try {
      [ratesInfo, { items: budgets }] = await Promise.all([api.rates(), api.budgets()]);
      budgetDrafts = Object.fromEntries(categories.map((item) => [item.id, item.monthly_budget ? String(Number(item.monthly_budget)) : '']));
    } catch { /* optional data */ }
  }

  async function saveBudget(category: Category) {
    const raw = (budgetDrafts[category.id] ?? '').replace(',', '.').trim();
    const value = raw ? Number(raw) : null;
    if (value !== null && !(value > 0)) { error = tt('rates.invalid'); return; }
    error = ''; notice = '';
    try {
      await api.setBudget(category.id, value);
      categories = await api.categories();
      await loadExtras();
      notice = tt('budget.saved');
    } catch (e) { error = message(e); }
  }

  function syncTagDrafts() {
    tagCategoryDrafts = Object.fromEntries(tags.map((tag) => [tag.id, [...tag.category_ids]]));
  }

  async function loadReport() {
    try { report = await api.report(start, end, reportCurrency); }
    catch (e) { error = message(e); }
  }

  function expenseParams(offset: number): URLSearchParams {
    const params = new URLSearchParams();
    if (expenseStart) params.set('start', expenseStart);
    if (expenseEnd) params.set('end', expenseEnd);
    if (expenseCategory) params.set('category_id', expenseCategory);
    if (search.trim()) params.set('search', search.trim());
    params.set('sort', expenseSort);
    params.set('limit', '50');
    params.set('offset', String(offset));
    return params;
  }

  function exportQuery(): string {
    const params = expenseParams(0);
    params.delete('limit');
    params.delete('offset');
    return params.toString();
  }

  async function loadExpenses() {
    try {
      expenses = await api.expenses(`?${expenseParams(0).toString()}`);
      hasMoreExpenses = expenses.length === 50;
    } catch (e) { error = message(e); }
  }

  async function loadMoreExpenses() {
    try {
      const page = await api.expenses(`?${expenseParams(expenses.length).toString()}`);
      expenses = [...expenses, ...page];
      hasMoreExpenses = page.length === 50;
    } catch (e) { error = message(e); }
  }

  async function clearFilters() {
    search = '';
    expenseStart = '';
    expenseEnd = '';
    expenseCategory = '';
    expenseSort = 'newest';
    await loadExpenses();
  }

  function message(e: unknown): string {
    return e instanceof Error ? e.message : tt('common.genericError');
  }

  function goTo(next: View) {
    view = next;
    error = '';
    notice = '';
    if (typeof window !== 'undefined') window.scrollTo({ top: 0 });
  }

  function tagsForCategory(categoryId: number | null): Tag[] {
    return categoryId === null
      ? tags
      : tags.filter((tag) => !tag.category_ids.length || tag.category_ids.includes(categoryId));
  }

  async function submitAuth(event: SubmitEvent) {
    event.preventDefault();
    const data = new FormData(event.currentTarget as HTMLFormElement);
    busy = true; error = '';
    try {
      user = registering
        ? await api.register({
            email: String(data.get('email')), password: String(data.get('password')),
            display_name: String(data.get('display_name')),
            household_name: String(data.get('household_name')) || undefined,
            invite_token: String(data.get('invite_token')) || undefined
          })
        : await api.login(String(data.get('email')), String(data.get('password')), String(data.get('totp_code') || '') || undefined);
      await loadData();
    } catch (e) { error = message(e); }
    finally { busy = false; }
  }

  async function passkeyLogin() {
    busy = true; error = '';
    try { user = await loginWithPasskey(); await loadData(); }
    catch (e) { if (!(e instanceof PasskeyCancelled)) error = message(e); }
    finally { busy = false; }
  }

  async function logout() {
    try {
      await api.logout(); user = null; expenses = []; view = 'expenses';
      clearSnapshot(); await queue.clear().catch(() => undefined); queued = [];
    }
    catch (e) { error = message(e); }
  }

  function openNew() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
    editing = null;
    form = emptyForm();
    showForm = true;
    error = ''; notice = '';
  }

  function openEdit(expense: Expense) {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
    editing = expense;
    form = {
      amount: String(expense.amount), currency: expense.currency, spent_on: expense.spent_on,
      merchant: expense.merchant, description: expense.description,
      category_id: expense.category_id, tag_ids: expense.tag_ids ?? [],
      payment_method: expense.payment_method ?? '', visibility: expense.visibility,
      review_status: expense.review_status, document_key: expense.document_key, tax_amount: expense.tax_amount ?? '',
      items: (expense.items ?? []).map((item) => ({
        description: item.description, quantity: item.quantity ?? '',
        unit_price: item.unit_price ?? '', total: item.total ?? ''
      }))
    };
    showForm = true;
    error = ''; notice = '';
  }

  function closeExpenseForm() {
    showForm = false;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = '';
  }

  function addItem() {
    form.items = [...form.items, { description: '', quantity: '', unit_price: '', total: '' }];
  }

  async function upload(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    busy = true; error = ''; notice = '';
    try {
      const optimized = await optimizeImage(file);
      const { document_key, extraction } = await api.extract(optimized);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = URL.createObjectURL(optimized);
      form.document_key = document_key;
      if (extraction.merchant) form.merchant = extraction.merchant;
      if (extraction.amount) form.amount = String(extraction.amount);
      if (extraction.spent_on) form.spent_on = extraction.spent_on;
      if (extraction.currency) form.currency = extraction.currency;
      if (extraction.tax_amount) form.tax_amount = String(extraction.tax_amount);
      if (extraction.description) form.description = extraction.description;
      if (extraction.payment_method) form.payment_method = extraction.payment_method;
      form.items = extraction.items.map((item) => ({
        description: item.description, quantity: item.quantity ?? '',
        unit_price: item.unit_price ?? '', total: item.total ?? ''
      }));
      const suggested = categories.find((item) => item.name.toLowerCase() === extraction.suggested_category?.toLowerCase());
      if (suggested) form.category_id = suggested.id;
      form.review_status = 'needs_review';
      notice = tt('form.analyzed');
    } catch (e) { error = message(e); }
    finally { busy = false; input.value = ''; }
  }

  async function optimizeImage(file: File): Promise<File> {
    if (!file.type.startsWith('image/') || file.size < 700_000) return file;
    let bitmap: ImageBitmap;
    try { bitmap = await createImageBitmap(file); }
    catch { return file; }
    const scale = Math.min(1, 1800 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const context = canvas.getContext('2d');
    if (!context) { bitmap.close(); return file; }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.82));
    return blob && blob.size < file.size ? new File([blob], `${file.name.replace(/\.[^.]+$/, '')}.jpg`, { type: 'image/jpeg' }) : file;
  }

  async function saveExpense(event: SubmitEvent) {
    event.preventDefault();
    busy = true; error = ''; notice = '';
    const payload: ExpenseWrite = {
      ...form, amount: Number(form.amount), tax_amount: form.tax_amount ? Number(form.tax_amount) : null,
      category_id: form.category_id ? Number(form.category_id) : null,
      payment_method: form.payment_method || null,
      items: form.items.filter((item) => item.description.trim()).map((item) => ({
        description: item.description.trim(), quantity: item.quantity ? Number(item.quantity) : null,
        unit_price: item.unit_price ? Number(item.unit_price) : null,
        total: item.total ? Number(item.total) : null
      }))
    };
    const wasEditing = editing !== null;
    try {
      try { localStorage.setItem('expenselens-currency', form.currency); } catch { /* ignore */ }
      if (editing) await api.updateExpense(editing.id, payload);
      else if (offline) { await queueOffline(payload); return; }
      else {
        try { await api.createExpense(payload); }
        catch (e) { if (isNetworkError(e)) { offline = true; await queueOffline(payload); return; } throw e; }
      }
      closeExpenseForm();
      notice = tt(wasEditing ? 'exp.updated' : 'exp.saved');
      await loadData();
    } catch (e) { error = message(e); }
    finally { busy = false; }
  }

  async function queueOffline(payload: ExpenseWrite) {
    await queue.add(payload);
    await refreshQueue();
    closeExpenseForm();
    notice = tt('off.savedLocal');
  }

  async function removeExpense(expense: Expense) {
    if (!confirm(tt('exp.deleteConfirm', { name: expense.merchant }))) return;
    try {
      await api.deleteExpense(expense.id);
      closeExpenseForm();
      notice = tt('exp.deleted');
      await loadData();
    } catch (e) { error = message(e); }
  }

  async function duplicate(expense: Expense) {
    try {
      await api.duplicateExpense(expense.id);
      closeExpenseForm();
      notice = tt('exp.duplicated');
      await loadData();
    } catch (e) { error = message(e); }
  }

  function choosePeriod(value: string) {
    period = value;
    const today = new Date();
    const y = today.getFullYear(), m = today.getMonth();
    if (value === 'today') start = end = todayLocal();
    if (value === 'week') {
      const monday = new Date(y, m, today.getDate() - ((today.getDay() + 6) % 7));
      start = localDate(monday); end = todayLocal();
    }
    if (value === 'month') {
      start = localDate(new Date(y, m, 1)); end = todayLocal();
    }
    if (value === 'year') {
      start = localDate(new Date(y, 0, 1)); end = todayLocal();
    }
    loadReport();
  }

  async function removeTag(tag: Tag) {
    if (!confirm(tt('tag.deleteConfirm', { name: tag.name }))) return;
    error = ''; notice = '';
    try {
      await api.deleteTaxonomy('tags', tag.id);
      tags = await api.tags();
      syncTagDrafts();
      notice = tt('tag.deleted');
      await loadExpenses();
    } catch (e) { error = message(e); }
  }

  async function createTag(event: SubmitEvent) {
    event.preventDefault();
    if (!tagName.trim()) return;
    busy = true; error = ''; notice = '';
    try {
      await api.createTaxonomy('tags', tagName.trim(), undefined, tagCategoryIds);
      tagName = '';
      tagCategoryIds = [];
      tags = await api.tags();
      syncTagDrafts();
      notice = tt('tag.added');
    } catch (e) { error = message(e); }
    finally { busy = false; }
  }

  function toggleTagCategory(tagId: number, categoryId: number) {
    const current = tagCategoryDrafts[tagId] ?? [];
    tagCategoryDrafts = {
      ...tagCategoryDrafts,
      [tagId]: current.includes(categoryId)
        ? current.filter((id) => id !== categoryId)
        : [...current, categoryId]
    };
  }

  async function saveTagCategories(tag: Tag) {
    error = ''; notice = '';
    try {
      const updated = await api.updateTagCategories(
        tag.id, tagCategoryDrafts[tag.id] ?? []
      );
      tags = tags.map((item) => item.id === updated.id ? updated : item);
      tagCategoryDrafts = {
        ...tagCategoryDrafts,
        [updated.id]: [...updated.category_ids]
      };
      notice = tt('tag.updated', { name: tag.name });
    } catch (e) { error = message(e); }
  }

  async function createCategory(event: SubmitEvent) {
    event.preventDefault();
    if (!categoryName.trim()) return;
    busy = true; error = ''; notice = '';
    try {
      await api.createTaxonomy('categories', categoryName.trim(), categoryColor, [], categoryIcon);
      categoryName = '';
      categoryIcon = '';
      categories = await api.categories();
      notice = tt('cat.added');
    } catch (e) { error = message(e); }
    finally { busy = false; }
  }

  async function deleteCategory(category: Category) {
    if (!confirm(tt('cat.deleteConfirm', { name: category.name }))) return;
    error = ''; notice = '';
    try {
      await api.deleteTaxonomy('categories', category.id);
      categories = await api.categories();
      if (expenseCategory === String(category.id)) expenseCategory = '';
      notice = tt('cat.deleted');
      await loadExpenses();
      await loadReport();
    } catch (e) { error = message(e); }
  }

  function startRenameCategory(category: Category) {
    editingCategoryId = category.id;
    categoryNameDraft = category.name;
    categoryIconDraft = category.icon ?? '';
    categoryColorDraft = category.color;
  }

  async function renameCategory(category: Category) {
    const name = categoryNameDraft.trim();
    if (!name) return;
    error = ''; notice = '';
    try {
      await api.renameCategory(category.id, name, categoryIconDraft, categoryColorDraft);
      categories = await api.categories();
      editingCategoryId = null;
      categoryNameDraft = '';
      notice = tt('cat.updated');
    } catch (e) { error = message(e); }
  }

  function editMember(member: User) {
    editingMemberId = member.id;
    memberDraft = { email: member.email, display_name: member.display_name };
    error = '';
  }

  async function saveMember(member: User) {
    error = ''; notice = '';
    try {
      const updated = await api.updateHouseholdMember(member.id, {
        email: memberDraft.email.trim(),
        display_name: memberDraft.display_name.trim()
      });
      members = members.map((item) => item.id === updated.id ? updated : item);
      if (user?.id === updated.id) user = updated;
      editingMemberId = null;
      notice = tt('fam.updated');
    } catch (e) { error = message(e); }
  }

  async function removeMember(member: User) {
    if (!confirm(tt('fam.removeConfirm', { name: member.display_name }))) return;
    error = ''; notice = '';
    try {
      await api.deleteHouseholdMember(member.id);
      members = members.filter((item) => item.id !== member.id);
      await loadExpenses();
      await loadReport();
      notice = tt('fam.removed');
    } catch (e) { error = message(e); }
  }

  async function inviteFamily() {
    try {
      const { token } = await api.createHouseholdInvite();
      await navigator.clipboard.writeText(token);
      notice = tt('fam.inviteCopied');
    } catch (e) { error = message(e); }
  }

  async function inviteTelegram() {
    try {
      const { token } = await api.createTelegramLink();
      const instruction = `/start ${token}`;
      try {
        await navigator.clipboard.writeText(instruction);
        notice = tt('set.telegramCopied');
      } catch {
        notice = tt('set.telegramSend', { text: instruction });
      }
    } catch (e) { error = message(e); }
  }

  async function setupMfa() {
    error = ''; recoveryCodes = [];
    try {
      const result = await api.mfaSetup();
      mfaQr = result.qr_data_url;
      mfaSecret = result.secret;
      showSecurity = true;
    } catch (e) { error = message(e); }
  }

  function openSecurity() {
    if (user?.mfa_enabled) showSecurity = true;
    else setupMfa();
  }

  function changeMfaCode(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    mfaCode = input.value.replace(/\D/g, '').slice(0, 6);
    input.value = mfaCode;
  }

  function markReviewed(event: Event) {
    form.review_status = (event.currentTarget as HTMLInputElement).checked ? 'reviewed' : 'needs_review';
  }

  async function enableMfa() {
    error = '';
    if (!/^\d{6}$/.test(mfaCode)) { error = tt('mfa.invalidCode'); return; }
    try {
      recoveryCodes = (await api.mfaEnable(mfaCode)).recovery_codes;
      mfaCode = '';
      if (user) user.mfa_enabled = true;
      notice = tt('mfa.on');
    } catch (e) { error = message(e); }
  }

  async function disableMfa() {
    error = '';
    if (!/^\d{6}$/.test(mfaCode)) { error = tt('mfa.invalidCode'); return; }
    try {
      await api.mfaDisable(mfaCode);
      if (user) user.mfa_enabled = false;
      mfaCode = '';
      showSecurity = false;
      notice = tt('mfa.disabledNotice');
    } catch (e) { error = message(e); }
  }

  function closeSecurity() {
    showSecurity = false;
    recoveryCodes = [];
    mfaCode = '';
  }

  const paymentLabels: Record<string, string> = {
    Contanti: 'form.cash', Carta: 'form.card', Elettronico: 'form.electronic'
  };

  const initial = (text: string) => (text.trim()[0] ?? '?').toUpperCase();

  $: navItems = [
    { id: 'expenses' as View, label: $t('tab.expenses'), icon: 'list' },
    { id: 'reports' as View, label: $t('tab.reports'), icon: 'chart' },
    { id: 'categories' as View, label: $t('tab.categories'), icon: 'tag' },
    ...(user?.is_admin ? [{ id: 'family' as View, label: $t('tab.family'), icon: 'people' }] : []),
    { id: 'settings' as View, label: $t('tab.settings'), icon: 'gear' }
  ];
  $: titles = {
    expenses: $t('exp.title'), reports: $t('rep.title'), categories: $t('cat.title'),
    family: $t('fam.title'), settings: $t('set.title')
  } as Record<View, string>;
  $: visibleExpenses = expenses.filter((item) => `${item.merchant} ${item.description}`.toLowerCase().includes(search.toLowerCase()));
  $: rateMap = Object.fromEntries([['EUR', 1], ...(ratesInfo?.rates ?? []).map((item) => [item.currency, Number(item.rate)])]) as Record<string, number>;
  $: monthTotal = visibleExpenses.reduce((sum, item) => sum + (rateMap[item.currency] ? Number(item.amount) * rateMap[item.currency] : 0), 0);
  $: missingRates = [...new Set(visibleExpenses.map((item) => item.currency).filter((code) => !rateMap[code]))];
  $: converted = visibleExpenses.some((item) => item.currency !== 'EUR' && rateMap[item.currency]);
  $: formCurrencies = [...new Set([...QUICK_CURRENCIES.slice(0, 1), form.currency, ...(ratesInfo?.rates ?? []).map((item) => item.currency), ...QUICK_CURRENCIES])].slice(0, 5);
  $: formRate = form.currency !== 'EUR' ? rateMap[form.currency] : undefined;
  $: formConverted = formRate && Number(form.amount.replace(',', '.')) > 0 ? Number(form.amount.replace(',', '.')) * formRate : 0;
  $: failedCount = queued.filter((item) => item.error).length;
  $: waitingCount = queued.length - failedCount;
  $: activeFilters = [expenseStart, expenseEnd, expenseCategory].filter(Boolean).length + (expenseSort !== 'newest' ? 1 : 0);
  $: peakDay = report ? Math.max(1, ...report.by_day.map((entry: { total: string }) => Number(entry.total))) : 1;
</script>

<svelte:head>
  <title>{$t('app.title')}</title>
  <meta name="description" content={$t('app.description')} />
</svelte:head>

{#if loading}
  <main class="content" aria-label={$t('common.loading')}>
    <div class="skeleton" style="height:48px;margin-top:20px"></div>
    <div class="skeleton" style="height:260px;margin-top:16px"></div>
  </main>
{:else if !user}
  <main class="content" style="display:flex;min-height:100dvh;align-items:center;justify-content:center;padding-top:24px;padding-bottom:24px">
    <section style="width:100%;max-width:420px">
      <div style="text-align:center;margin-bottom:24px">
        <img src="/favicon.svg" alt="" width="84" height="84" style="margin:0 auto 14px;border-radius:19px;box-shadow:0 4px 14px rgba(0,0,0,.18)" />
        <h1 class="large-title" style="margin:0">{$t('app.name')}</h1>
        <p class="subtitle" style="margin:6px 0 0">{$t('app.tagline')}</p>
      </div>
      {#if error}<p class="alert alert-err" role="alert">{error}</p>{/if}
      <form on:submit={submitAuth}>
        <div class="group">
          {#if registering}
            <label class="row"><span style="width:110px;flex:none">{$t('auth.name')}</span><input class="field" style="background:transparent" name="display_name" required maxlength="100" autocomplete="name" /></label>
            <label class="row"><span style="width:110px;flex:none">{$t('auth.household')}</span><input class="field" style="background:transparent" name="household_name" maxlength="100" placeholder={$t('auth.householdHint')} /></label>
            <label class="row"><span style="width:110px;flex:none">{$t('auth.invite')}</span><input class="field" style="background:transparent" name="invite_token" autocomplete="off" /></label>
          {/if}
          <label class="row"><span style="width:110px;flex:none">{$t('auth.email')}</span><input class="field" style="background:transparent" type="email" name="email" required autocomplete="email" inputmode="email" autocapitalize="none" /></label>
          <label class="row"><span style="width:110px;flex:none">{$t('auth.password')}</span><input class="field" style="background:transparent" type="password" name="password" required minlength={registering ? 12 : 1} maxlength="128" autocomplete={registering ? 'new-password' : 'current-password'} /></label>
          {#if !registering}
            <label class="row"><span style="width:110px;flex:none">{$t('auth.mfa')}</span><input class="field" style="background:transparent" name="totp_code" maxlength="32" autocomplete="one-time-code" placeholder={$t('auth.mfaHint')} /></label>
          {/if}
        </div>
        <button class="btn btn-primary btn-block" style="margin-top:20px;min-height:50px" disabled={busy}>{busy ? $t('common.wait') : registering ? $t('auth.register') : $t('auth.login')}</button>
      </form>
      {#if !registering && passkeysSupported()}
        <button class="btn btn-block" style="margin-top:10px;min-height:48px" disabled={busy} on:click={passkeyLogin}><Icon name="lock" />{$t('passkey.login')}</button>
      {/if}
      <button class="btn btn-block" style="margin-top:10px;background:transparent" on:click={() => { registering = !registering; error = ''; }}>
        {registering ? $t('auth.haveAccount') : $t('auth.firstTime')}
      </button>
      <div class="seg" style="margin:18px auto 0;max-width:200px" role="group" aria-label={$t('set.language')}>
        <button aria-pressed={$lang === 'it'} on:click={() => setLang('it')}>Italiano</button>
        <button aria-pressed={$lang === 'en'} on:click={() => setLang('en')}>English</button>
      </div>
      <p class="muted" style="margin-top:18px;text-align:center;font-size:13px">{$t('app.privacy')}</p>
    </section>
  </main>
{:else}
  <div class="shell with-side">
    <aside class="sidebar" aria-label={$t('tab.sections')}>
      <div class="brand"><img src="/favicon.svg" alt="" />{$t('app.name')}</div>
      {#each navItems as item (item.id)}
        <button class="side-item" aria-current={view === item.id ? 'page' : undefined} on:click={() => goTo(item.id)}><Icon name={item.icon} />{item.label}</button>
      {/each}
    </aside>

    <header class="nav-bar">
      <div class="nav-inner">
        <div class="brand nav-brand"><img src="/favicon.svg" alt="" />{$t('app.name')}</div>
        <span style="flex:1"></span>
        {#if view === 'expenses'}
          <button class="btn btn-primary btn-add" on:click={openNew}><Icon name="plus" />{$t('exp.new')}</button>
        {/if}
      </div>
    </header>

    <main class="content">
      <h1 class="large-title">{titles[view]}</h1>
      {#if view === 'expenses'}<p class="subtitle">{$t('home.greeting', { name: user.display_name.split(' ')[0] })}</p>{/if}
      {#if offline}<div class="alert" role="status" style="background:var(--fill)">{$t('off.banner')}</div>{/if}
      {#if waitingCount > 0}<div class="alert" role="status" style="background:var(--fill)">{syncing ? $t('off.syncing') : $t('off.pending', { n: waitingCount })}</div>{/if}
      {#if failedCount > 0}
        <div class="alert alert-err" role="alert">
          {$t('off.failed', { n: failedCount, reason: queued.find((item) => item.error)?.error ?? '' })}
          <button class="link-btn" on:click={retryFailed}>{$t('off.retry')}</button>
          <button class="link-btn" on:click={discardFailed}>{$t('off.discard')}</button>
        </div>
      {/if}
      {#if error}<div class="alert alert-err toast" role="alert">{error}</div>{/if}
      {#if notice}<div class="alert alert-ok toast" role="status">{notice}</div>{/if}

      {#if view === 'expenses'}
        <section class="card" style="margin-bottom:14px">
          <p class="muted" style="margin:0;font-size:14px">{$t('exp.shown')} · EUR</p>
          <p class="stat-big" style="margin:2px 0 0">{money(monthTotal, 'EUR', $locale)}</p>
          {#if converted}<p class="muted" style="margin:4px 0 0;font-size:13px">{$t('exp.convertedNote')}</p>{/if}
          {#if missingRates.length}<p class="muted" style="margin:4px 0 0;font-size:13px">{$t('exp.missingRates', { list: missingRates.join(', ') })}</p>{/if}
        </section>
        <form class="search" on:submit|preventDefault={loadExpenses} role="search">
          <Icon name="search" />
          <input id="expense-search" class="field" type="search" bind:value={search} placeholder={$t('exp.search')} enterkeyhint="search" />
        </form>
        <div style="display:flex;justify-content:space-between;align-items:center;margin:6px 0">
          <button class="link-btn" style="padding-left:4px" on:click={() => showFilters = !showFilters} aria-expanded={showFilters}>{$t('exp.filters')}{activeFilters ? ` (${activeFilters})` : ''}</button>
          {#if activeFilters || search}<button class="link-btn" on:click={clearFilters}>{$t('exp.reset')}</button>{/if}
        </div>
        {#if showFilters}
          <form class="card" style="display:grid;gap:12px;margin-bottom:14px" on:submit|preventDefault={loadExpenses}>
            <div class="cols-2">
              <label><span class="label">{$t('exp.from')}</span><input class="field" type="date" bind:value={expenseStart} /></label>
              <label><span class="label">{$t('exp.to')}</span><input class="field" type="date" bind:value={expenseEnd} /></label>
              <label><span class="label">{$t('exp.category')}</span><select class="field" bind:value={expenseCategory}><option value="">{$t('common.all')}</option>{#each categories as category}<option value={String(category.id)}>{category.name}</option>{/each}</select></label>
              <label><span class="label">{$t('exp.sort')}</span><select class="field" bind:value={expenseSort}><option value="newest">{$t('exp.sortNewest')}</option><option value="oldest">{$t('exp.sortOldest')}</option><option value="amount_desc">{$t('exp.sortAmount')}</option></select></label>
            </div>
            <button class="btn btn-primary">{$t('exp.apply')}</button>
          </form>
        {/if}

        <div class="group-title" style="display:flex;justify-content:space-between"><span>{$t('exp.recent')}</span><span>{$t('exp.count', { n: visibleExpenses.length })}</span></div>
        {#if visibleExpenses.length === 0}
          <div class="card muted" style="text-align:center;padding:40px 16px"><p style="margin:0;font-weight:600;color:var(--text)">{$t('exp.empty')}</p><p style="margin:6px 0 0;font-size:15px">{$t('exp.emptyHint')}</p></div>
        {:else}
          <ul class="group" style="margin:0;padding:0;list-style:none">
            {#each visibleExpenses as expense (expense.id)}
              {@const category = categories.find((c) => c.id === expense.category_id)}
              <li>
                <SwipeRow disabled={offline} duplicateLabel={$t('swipe.duplicate')} deleteLabel={$t('swipe.delete')} onDuplicate={() => duplicate(expense)} onDelete={() => removeExpense(expense)}>
                <button class="row" on:click={() => openEdit(expense)} aria-label={$t('exp.editLabel', { name: expense.merchant })}>
                  <span class="icon-tile" style={`background:${category?.color ?? '#a9b0ab'}`} aria-hidden="true">{#if category?.icon}<Icon name={category.icon} />{:else}{initial(expense.merchant)}{/if}</span>
                  <span class="row-main">
                    <span class="row-title" style="font-weight:600">{expense.merchant}</span>
                    <span class="row-sub">{expense.description || category?.name || $t('common.uncategorized')} · {new Date(`${expense.spent_on}T12:00:00`).toLocaleDateString($locale)}</span>
                    {#if expense.review_status === 'needs_review'}<span class="badge" style="margin-top:3px">{$t('common.toVerify')}</span>{/if}
                  </span>
                  <span class="row-value">{money(expense.amount, expense.currency, $locale)}</span>
                  <span class="chevron"><Icon name="chevron" /></span>
                </button>
                </SwipeRow>
              </li>
            {/each}
          </ul>
        {/if}
        {#if hasMoreExpenses}<div style="text-align:center;margin-top:14px"><button class="btn" on:click={loadMoreExpenses}>{$t('exp.more')}</button></div>{/if}
      {:else if view === 'reports'}
        <div class="seg" role="group" aria-label={$t('rep.period')} style="margin-bottom:12px">
          <button aria-pressed={period === 'today'} on:click={() => choosePeriod('today')}>{$t('rep.today')}</button>
          <button aria-pressed={period === 'week'} on:click={() => choosePeriod('week')}>{$t('rep.week')}</button>
          <button aria-pressed={period === 'month'} on:click={() => choosePeriod('month')}>{$t('rep.month')}</button>
          <button aria-pressed={period === 'year'} on:click={() => choosePeriod('year')}>{$t('rep.year')}</button>
        </div>
        <section class="card" style="margin-bottom:14px">
          <div class="cols-2" style="grid-template-columns:repeat(auto-fit,minmax(130px,1fr))">
            <label><span class="label">{$t('exp.from')}</span><input class="field" type="date" bind:value={start} on:change={loadReport} /></label>
            <label><span class="label">{$t('exp.to')}</span><input class="field" type="date" bind:value={end} on:change={loadReport} /></label>
            <label><span class="label">{$t('form.currency')}</span><select class="field" bind:value={reportCurrency} on:change={loadReport}><option>EUR</option><option>USD</option><option>GBP</option><option>CHF</option></select></label>
          </div>
        </section>
        {#if report}
          <section class="stat-grid">
            <div class="card"><p class="muted" style="margin:0;font-size:14px">{$t('rep.total')} · {report.currency}</p><p class="stat-big" style="margin:4px 0 0">{money(report.total, report.currency, $locale)}</p></div>
            <div class="card"><p class="muted" style="margin:0;font-size:14px">{$t('rep.count')}</p><p class="stat-big" style="margin:4px 0 0">{report.count}</p></div>
            <div class="card"><p class="muted" style="margin:0;font-size:14px">{$t('rep.average')}</p><p class="stat-big" style="margin:4px 0 0">{money(report.count ? Number(report.total) / report.count : 0, report.currency, $locale)}</p></div>
          </section>

          <div class="group-title">{$t('rep.byCategory')}</div>
          <div class="card">
            {#each report.by_category as item}
              <div style="margin-bottom:14px"><div style="display:flex;justify-content:space-between;gap:12px;font-size:15px;margin-bottom:5px"><span>{item.name}</span><strong>{money(item.total, report.currency, $locale)}</strong></div><div class="bar-track"><div class="bar-fill" style={`width:${Math.min(100, Number(item.total) / Math.max(Number(report.total), 1) * 100)}%`}></div></div></div>
            {/each}
            {#if report.by_category.length === 0}<p class="muted" style="margin:0;font-size:15px">{$t('common.noData')}</p>{/if}
          </div>

          <div class="group-title">{$t('rep.topMerchants')}</div>
          <div class="group">
            {#each report.top_merchants as item}<div class="row"><span class="row-main row-title">{item.name}</span><strong class="row-value">{money(item.total, report.currency, $locale)}</strong></div>{/each}
            {#if report.top_merchants.length === 0}<div class="row muted">{$t('common.noData')}</div>{/if}
          </div>

          <div class="group-title">{$t('rep.byTag')}</div>
          <div class="group">
            {#each report.by_tag as item}<div class="row"><span class="row-main row-title">{item.name}</span><strong class="row-value">{money(item.total, report.currency, $locale)}</strong></div>{/each}
            {#if report.by_tag.length === 0}<div class="row muted">{$t('rep.noTag')}</div>{/if}
          </div>

          <div class="group-title">{$t('rep.byUser')}</div>
          <div class="group">
            {#each report.by_user as item}<div class="row"><span class="row-main row-title">{item.name}</span><strong class="row-value">{money(item.total, report.currency, $locale)}</strong></div>{/each}
            {#if report.by_user.length === 0}<div class="row muted">{$t('common.noData')}</div>{/if}
          </div>

          <div class="group-title">{$t('rep.daily')}</div>
          <div class="card">
            {#if report.by_day.length}
              <div style="display:flex;height:150px;align-items:flex-end;gap:3px;overflow-x:auto;border-bottom:.5px solid var(--sep);padding-bottom:2px" role="img" aria-label={$t('rep.chartLabel')}>
                {#each report.by_day as day}<div style="display:flex;height:100%;min-width:8px;flex:1;flex-direction:column;justify-content:flex-end" title={`${day.date}: ${money(day.total, report.currency, $locale)}`}><div class="bar-fill" style={`width:100%;border-radius:4px 4px 0 0;height:${Math.max(3, Number(day.total) / peakDay * 100)}%`}></div></div>{/each}
              </div>
              <div class="muted" style="display:flex;justify-content:space-between;margin-top:6px;font-size:12px"><span>{report.by_day[0].date}</span><span>{report.by_day[report.by_day.length - 1].date}</span></div>
            {:else}<p class="muted" style="margin:0;font-size:15px">{$t('common.noData')}</p>{/if}
          </div>
        {/if}
      {:else if view === 'categories'}
        <p class="subtitle">{$t('cat.intro')}</p>
        <form class="card" style="display:grid;gap:12px" on:submit={createCategory}>
          <div style="display:flex;gap:10px;align-items:flex-end">
            <label style="flex:1;min-width:0"><span class="label">{$t('cat.new')}</span><input class="field" bind:value={categoryName} maxlength="60" placeholder={$t('cat.placeholder')} required /></label>
            <label><span class="label">{$t('cat.color')}</span><input class="field" style="width:56px;padding:4px" type="color" bind:value={categoryColor} aria-label={$t('cat.colorLabel')} /></label>
            <button class="btn btn-primary" disabled={busy} aria-label={$t('common.add')}><Icon name="plus" /></button>
          </div>
          <fieldset class="icon-picker">
            <legend class="label">{$t('cat.icon')}</legend>
            <label class="icon-opt" style="--c:{categoryColor}"><input type="radio" bind:group={categoryIcon} value="" aria-label={$t('cat.iconNone')} /><span>{initial(categoryName || 'A')}</span></label>
            {#each CATEGORY_ICONS as name}
              <label class="icon-opt" style="--c:{categoryColor}"><input type="radio" bind:group={categoryIcon} value={name} aria-label={name} /><Icon {name} /></label>
            {/each}
          </fieldset>
        </form>
        <div style="height:14px"></div>
        {#if categories.length}
          <ul class="group" style="margin:0;padding:0;list-style:none">
            {#each categories as category (category.id)}
              <li class="row" style="align-items:flex-start">
                <span class="icon-tile" style={`background:${editingCategoryId === category.id ? categoryColorDraft : category.color}`} aria-hidden="true">{#if (editingCategoryId === category.id ? categoryIconDraft : category.icon)}<Icon name={editingCategoryId === category.id ? categoryIconDraft : (category.icon ?? '')} />{:else}{initial(category.name)}{/if}</span>
                <span class="row-main">
                  {#if editingCategoryId === category.id}
                    <span style="display:flex;gap:8px;align-items:center">
                      <input class="field" style="max-width:240px;min-height:36px" bind:value={categoryNameDraft} maxlength="60" aria-label={$t('cat.renameLabel', { name: category.name })} on:keydown={(event) => { if (event.key === 'Enter') { event.preventDefault(); renameCategory(category); } if (event.key === 'Escape') editingCategoryId = null; }} />
                      <input class="field" style="width:48px;min-height:36px;padding:3px" type="color" bind:value={categoryColorDraft} aria-label={$t('cat.color')} />
                    </span>
                    <fieldset class="icon-picker" style="margin-top:8px">
                      <legend class="label">{$t('cat.icon')}</legend>
                      <label class="icon-opt" style="--c:{categoryColorDraft}"><input type="radio" bind:group={categoryIconDraft} value="" aria-label={$t('cat.iconNone')} /><span>{initial(categoryNameDraft || 'A')}</span></label>
                      {#each CATEGORY_ICONS as name}
                        <label class="icon-opt" style="--c:{categoryColorDraft}"><input type="radio" bind:group={categoryIconDraft} value={name} aria-label={name} /><Icon {name} /></label>
                      {/each}
                    </fieldset>
                  {:else}
                    <span class="row-title">{category.name}</span>
                  {/if}
                  {#each budgets.filter((item) => item.category_id === category.id) as item}
                    <span class="row-sub">{$t('budget.used', { spent: money(item.spent, 'EUR', $locale), budget: money(item.budget, 'EUR', $locale) })}</span>
                    <span class="bar-track" style="margin-top:4px"><span class="bar-fill" style={`display:block;width:${Math.min(100, item.ratio * 100)}%;${item.ratio >= 1 ? 'background:var(--danger)' : ''}`}></span></span>
                  {/each}
                  <label style="display:block;margin-top:8px">
                    <span class="label" style="margin-left:0">{$t('budget.label')}</span>
                    <input class="field" style="max-width:160px;min-height:36px" inputmode="decimal" bind:value={budgetDrafts[category.id]} placeholder={$t('budget.placeholder')} on:change={() => saveBudget(category)} />
                  </label>
                </span>
                {#if editingCategoryId === category.id}
                  <button class="btn btn-sm" on:click={() => renameCategory(category)} disabled={!categoryNameDraft.trim()}>{$t('common.save')}</button>
                  <button class="btn btn-sm" on:click={() => editingCategoryId = null}>{$t('common.cancel')}</button>
                {:else}
                  <button class="btn btn-sm" on:click={() => startRenameCategory(category)} aria-label={$t('cat.renameLabel', { name: category.name })}>{$t('common.edit')}</button>
                  <button class="btn btn-danger btn-sm" on:click={() => deleteCategory(category)} aria-label={$t('cat.deleteLabel', { name: category.name })}>{$t('common.delete')}</button>
                {/if}
              </li>
            {/each}
          </ul>
        {:else}
          <p class="card muted" style="text-align:center">{$t('cat.empty')}</p>
        {/if}

        <div class="group-title">{$t('tag.title')}</div>
        <p class="subtitle" style="margin:0 16px 10px">{$t('tag.intro')}</p>
        <form class="card" on:submit={createTag}>
          <label><span class="label">{$t('tag.new')}</span><input class="field" bind:value={tagName} maxlength="40" placeholder={$t('tag.placeholder')} required /></label>
          {#if categories.length}
            <fieldset style="border:0;padding:0;margin:12px 0 0">
              <legend class="label">{$t('tag.linked')}</legend>
              <div style="display:flex;flex-wrap:wrap;gap:8px">
                {#each categories as category}
                  <label class="chip"><input type="checkbox" bind:group={tagCategoryIds} value={category.id} />{category.name}</label>
                {/each}
              </div>
            </fieldset>
          {:else}
            <p class="muted" style="font-size:13px;margin:10px 0 0">{$t('tag.createFirst')}</p>
          {/if}
          <button class="btn btn-primary btn-block" style="margin-top:14px" disabled={busy}>{$t('tag.add')}</button>
        </form>
        {#if tags.length}
          <ul style="margin:14px 0 0;padding:0;list-style:none;display:grid;gap:12px">
            {#each tags as tag (tag.id)}
              <li class="card">
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><p style="margin:0;font-weight:600">{tag.name}</p><button class="btn btn-danger btn-sm" on:click={() => removeTag(tag)}>{$t('tag.delete')}</button></div>
                {#if categories.length}
                  <fieldset style="border:0;padding:0;margin:10px 0 0">
                    <legend class="label">{$t('tag.linkedShort')}</legend>
                    <div style="display:flex;flex-wrap:wrap;gap:8px">
                      {#each categories as category}
                        <label class="chip">
                          <input type="checkbox" checked={tagCategoryDrafts[tag.id]?.includes(category.id) ?? false} on:change={() => toggleTagCategory(tag.id, category.id)} />{category.name}
                        </label>
                      {/each}
                    </div>
                  </fieldset>
                {:else}
                  <p class="muted" style="font-size:13px;margin:8px 0 0">{$t('tag.noCategories')}</p>
                {/if}
                <button class="btn btn-sm" style="margin-top:12px" on:click={() => saveTagCategories(tag)}>{$t('tag.saveLinks')}</button>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="card muted" style="text-align:center;margin-top:14px">{$t('tag.none')}</p>
        {/if}
      {:else if view === 'family' && user.is_admin}
        <p class="subtitle">{$t('fam.intro')}</p>
        <button class="btn btn-primary btn-block" style="margin-bottom:14px" on:click={inviteFamily}><Icon name="plus" />{$t('fam.invite')}</button>
        {#if members.length}
          <ul class="group" style="margin:0;padding:0;list-style:none">
            {#each members as member (member.id)}
              <li>
                {#if editingMemberId === member.id}
                  <form class="pad" style="display:grid;gap:12px" on:submit|preventDefault={() => saveMember(member)}>
                    <label><span class="label">{$t('fam.name')}</span><input class="field" bind:value={memberDraft.display_name} maxlength="100" required /></label>
                    <label><span class="label">{$t('auth.email')}</span><input class="field" type="email" bind:value={memberDraft.email} required /></label>
                    <div style="display:flex;gap:10px"><button class="btn btn-primary" style="flex:1">{$t('common.save')}</button><button class="btn" style="flex:1" type="button" on:click={() => editingMemberId = null}>{$t('common.cancel')}</button></div>
                  </form>
                {:else}
                  <div class="row">
                    <span class="icon-tile" style="background:#9a9cc2" aria-hidden="true">{initial(member.display_name)}</span>
                    <div class="row-main">
                      <span class="row-title" style="font-weight:600">{member.display_name}{#if member.id === user.id}<span class="pill" style="margin-left:8px">{$t('common.you')}</span>{/if}</span>
                      <span class="row-sub">{member.email}{member.is_admin ? ` · ${$t('fam.admin')}` : ''}</span>
                    </div>
                    {#if member.id !== user.id}
                      <button class="btn btn-sm" on:click={() => editMember(member)}>{$t('common.edit')}</button>
                      <button class="btn btn-sm btn-danger" on:click={() => removeMember(member)}>{$t('common.remove')}</button>
                    {/if}
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
        {:else}
          <p class="card muted" style="text-align:center">{$t('fam.empty')}</p>
        {/if}
      {:else if view === 'settings'}
        <div class="group-title">{$t('set.appearance')}</div>
        <div class="group">
          <div class="row"><span class="row-main">{$t('set.theme')}</span>
            <div class="seg" style="min-width:200px" role="group" aria-label={$t('set.theme')}>
              <button aria-pressed={theme === 'system'} on:click={() => chooseTheme('system')}>{$t('set.themeSystem')}</button>
              <button aria-pressed={theme === 'light'} on:click={() => chooseTheme('light')}>{$t('set.themeLight')}</button>
              <button aria-pressed={theme === 'dark'} on:click={() => chooseTheme('dark')}>{$t('set.themeDark')}</button>
            </div>
          </div>
          <div class="row"><span class="row-main">{$t('set.language')}</span>
            <div class="seg" style="min-width:200px" role="group" aria-label={$t('set.language')}>
              <button aria-pressed={$lang === 'it'} on:click={() => setLang('it')}>Italiano</button>
              <button aria-pressed={$lang === 'en'} on:click={() => setLang('en')}>English</button>
            </div>
          </div>
        </div>

        <div class="group-title">{$t('set.account')}</div>
        <div class="group">
          <div class="row"><span class="icon-tile" style="background:var(--accent)" aria-hidden="true">{initial(user.display_name)}</span><div class="row-main"><span class="row-title" style="font-weight:600">{user.display_name}</span><span class="row-sub">{user.email}</span></div></div>
          <button class="row" on:click={openSecurity}><span class="icon-tile" style="background:#d9b08a" aria-hidden="true"><Icon name="lock" /></span><span class="row-main"><span class="row-title">{$t('set.security')}</span><span class="row-sub">{user.mfa_enabled ? $t('set.mfaOn') : $t('set.mfaOff')}</span></span><span class="chevron"><Icon name="chevron" /></span></button>
          <button class="row" on:click={logout} style="color:var(--danger)"><span class="icon-tile" style="background:var(--danger)" aria-hidden="true"><Icon name="logout" /></span><span class="row-main row-title">{$t('auth.logout')}</span></button>
        </div>

        <AccountExtras {user} notify={(text) => { notice = text; error = ''; }} fail={(text) => { error = text; notice = ''; }} onRatesChanged={() => { loadExtras(); }} />

        <div class="group-title">{$t('set.integrations')}</div>
        <div class="group">
          <button class="row" on:click={inviteTelegram}><span class="icon-tile" style="background:#7fb0cc" aria-hidden="true"><Icon name="send" /></span><span class="row-main"><span class="row-title">{$t('set.telegram')}</span><span class="row-sub">{$t('set.telegramHint')}</span></span><span class="chevron"><Icon name="chevron" /></span></button>
        </div>

        <div class="group-title">{$t('set.data')}</div>
        <div class="group">
          <a class="row" href={`/api/expenses/export/csv?${exportQuery()}`} download><span class="icon-tile" style="background:#86b498" aria-hidden="true"><Icon name="download" /></span><span class="row-main row-title">{$t('exp.exportCsv')}</span></a>
          <a class="row" href={`/api/expenses/export/json?${exportQuery()}`} download><span class="icon-tile" style="background:#8dbccb" aria-hidden="true"><Icon name="download" /></span><span class="row-main row-title">{$t('exp.exportJson')}</span></a>
        </div>
      {/if}
    </main>

    <nav class="tabbar" aria-label={$t('tab.sections')}>
      {#each navItems as item (item.id)}
        <button class="tab" aria-current={view === item.id ? 'page' : undefined} on:click={() => goTo(item.id)}><Icon name={item.icon} />{item.label}</button>
      {/each}
    </nav>
  </div>

  {#if showForm}
    <div class="scrim" role="presentation" on:click={(event) => { if (event.target === event.currentTarget) closeExpenseForm(); }}>
      <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="expense-form-title" tabindex="-1">
        <div class="sheet-head">
          <button class="link-btn" style="padding-left:0" on:click={closeExpenseForm}>{$t('common.cancel')}</button>
          <h2 id="expense-form-title">{editing ? $t('exp.edit') : $t('exp.new')}</h2>
          <button class="icon-btn" on:click={closeExpenseForm} aria-label={$t('common.close')}><Icon name="close" /></button>
        </div>
        <p class="muted" style="margin:4px 0 10px;font-size:14px">{$t('form.draftHint')}</p>
        {#if error}<p class="alert alert-err" role="alert">{error}</p>{/if}
        {#if notice}<p class="alert alert-ok" role="status">{notice}</p>{/if}
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px">
          <label class="btn file-btn"><Icon name="camera" />{$t('form.camera')}<input class="file-input" type="file" accept="image/*,application/pdf" capture="environment" on:change={upload} /></label>
          <label class="btn file-btn"><Icon name="paperclip" />{$t('form.file')}<input class="file-input" type="file" accept="image/*,application/pdf" on:change={upload} /></label>
        </div>
        <form on:submit={saveExpense} style="display:grid;gap:14px">
          <div class="card" style="display:grid;gap:12px">
            <div class="cols-2" style="grid-template-columns:1fr 1fr">
              <label><span class="label">{$t('form.amount')}</span><input class="field" style="font-size:20px;font-weight:600" type="number" inputmode="decimal" min="0.01" step="0.01" bind:value={form.amount} required placeholder="0.00" /></label>
              <label><span class="label">{$t('form.date')}</span><input class="field" type="date" bind:value={form.spent_on} required /></label>
            </div>
            <label><span class="label">{$t('form.merchant')}</span><input class="field" bind:value={form.merchant} required maxlength="160" /></label>
            <label><span class="label">{$t('form.description')}</span><input class="field" bind:value={form.description} maxlength="500" placeholder={$t('form.descriptionPlaceholder')} /><span class="muted" style="display:block;margin:4px 0 0 4px;font-size:12px">{$t('form.descriptionHint')}</span></label>
            <div class="cols-2" style="grid-template-columns:1fr 1fr">
              <label><span class="label">{$t('exp.category')}</span><select class="field" bind:value={form.category_id}><option value={null}>{$t('common.uncategorized')}</option>{#each categories as category}<option value={category.id}>{category.name}</option>{/each}</select></label>
              <div style="grid-column:1 / -1"><span class="label">{$t('form.currency')}</span>
                <div class="seg" role="group" aria-label={$t('form.currency')}>
                  {#each formCurrencies as code}<button type="button" aria-pressed={form.currency === code} on:click={() => form.currency = code}>{code}</button>{/each}
                </div>
                <input class="field" style="margin-top:6px" maxlength="3" bind:value={otherCurrency} placeholder={$t('form.otherCurrency')} aria-label={$t('form.otherCurrency')} autocapitalize="characters" on:input={() => { const code = otherCurrency.trim().toUpperCase(); if (/^[A-Z]{3}$/.test(code)) form.currency = code; }} />
                {#if formConverted}<span class="muted" style="font-size:13px">{$t('form.convertedHint', { amount: money(formConverted, 'EUR', $locale), rate: formRate ?? '' })}</span>{/if}
              </div>
            </div>
            {#if tagsForCategory(form.category_id).length}
              <fieldset style="border:0;padding:0;margin:0"><legend class="label">{$t('form.tags')}</legend><div style="display:flex;flex-wrap:wrap;gap:8px">{#each tagsForCategory(form.category_id) as tag}<label class="chip"><input type="checkbox" bind:group={form.tag_ids} value={tag.id} />{tag.name}</label>{/each}</div></fieldset>
            {/if}
          </div>

          <fieldset class="card" style="border:0;margin:0">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px"><legend style="font-weight:600;padding:0">{$t('form.items')}</legend><button class="btn btn-sm" type="button" on:click={addItem}><Icon name="plus" />{$t('common.add')}</button></div>
            <p class="muted" style="margin:0 0 10px;font-size:13px">{$t('form.itemsHint')}</p>
            {#each form.items as item, index}
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;padding-bottom:12px;border-bottom:.5px solid var(--sep)">
                <label style="grid-column:span 2"><span class="label">{$t('form.description')}</span><input class="field" bind:value={item.description} maxlength="200" /></label>
                <label><span class="label">{$t('form.quantity')}</span><input class="field" type="number" inputmode="decimal" min="0.001" step="0.001" bind:value={item.quantity} /></label>
                <label><span class="label">{$t('form.unitPrice')}</span><input class="field" type="number" inputmode="decimal" step="0.01" bind:value={item.unit_price} /></label>
                <label><span class="label">{$t('form.lineTotal')}</span><input class="field" type="number" inputmode="decimal" step="0.01" bind:value={item.total} /></label>
                <button class="btn btn-danger btn-sm" style="align-self:end" type="button" on:click={() => form.items = form.items.filter((_, itemIndex) => itemIndex !== index)}>{$t('form.removeRow')}</button>
              </div>
            {/each}
            {#if form.items.length === 0}<p class="muted" style="margin:0;font-size:13px">{$t('form.itemsEmpty')}</p>{/if}
          </fieldset>

          <div class="card" style="display:grid;gap:12px">
            <div class="cols-2" style="grid-template-columns:1fr 1fr">
              <label><span class="label">{$t('form.payment')}</span><select class="field" bind:value={form.payment_method}><option value="">{$t('common.toVerify')}</option>{#if form.payment_method && !paymentLabels[form.payment_method]}<option value={form.payment_method}>{form.payment_method}</option>{/if}{#each Object.entries(paymentLabels) as [value, key]}<option {value}>{$t(key)}</option>{/each}</select></label>
              <label><span class="label">{$t('form.tax')}</span><input class="field" type="number" inputmode="decimal" min="0" step="0.01" bind:value={form.tax_amount} /></label>
            </div>
            <div><span class="label">{$t('form.sharing')}</span>
              <div class="seg" role="group" aria-label={$t('form.sharing')}>
                <button type="button" aria-pressed={form.visibility === 'shared'} on:click={() => form.visibility = 'shared'}>{$t('form.shared')}</button>
                <button type="button" aria-pressed={form.visibility === 'personal'} on:click={() => form.visibility = 'personal'}>{$t('form.personal')}</button>
              </div>
            </div>
          </div>

          {#if form.review_status === 'needs_review'}<label class="alert alert-warn" style="display:flex;align-items:center;gap:10px;margin:0"><input type="checkbox" style="width:22px;height:22px;accent-color:var(--accent)" checked={false} on:change={markReviewed} />{$t('form.checked')}</label>{/if}
          {#if form.document_key}
            <div class="card">
              <p class="muted" style="margin:0 0 8px;font-size:13px">{$t('form.original')}</p>
              {#if form.document_key.endsWith('.pdf')}
                <a class="btn btn-sm" href={previewUrl || `/api/documents/${encodeURIComponent(form.document_key)}`} target="_blank" rel="noopener noreferrer">{$t('form.openPdf')}</a>
              {:else}
                <img style="max-height:300px;width:100%;border-radius:10px;object-fit:contain" src={previewUrl || `/api/documents/${encodeURIComponent(form.document_key)}`} alt={$t('form.docAlt')} />
              {/if}
            </div>
          {/if}
          <button class="btn btn-primary btn-block" style="min-height:50px" disabled={busy || form.review_status === 'needs_review'}>{busy ? $t('common.wait') : form.review_status === 'needs_review' ? $t('form.confirmFirst') : editing ? $t('form.saveChanges') : $t('form.saveExpense')}</button>
          {#if editing}
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
              <button class="btn" type="button" on:click={() => editing && duplicate(editing)}>{$t('exp.duplicate')}</button>
              <button class="btn btn-danger" type="button" on:click={() => editing && removeExpense(editing)}>{$t('common.delete')}</button>
            </div>
          {/if}
        </form>
      </div>
    </div>
  {/if}

  {#if showSecurity}
    <div class="scrim" role="presentation" style="z-index:40" on:click={(event) => { if (event.target === event.currentTarget) closeSecurity(); }}>
      <div class="sheet" role="dialog" aria-modal="true" aria-labelledby="security-title" tabindex="-1">
        <div class="sheet-head"><span style="width:44px"></span><h2 id="security-title">{$t('mfa.title')}</h2><button class="icon-btn" on:click={closeSecurity} aria-label={$t('common.close')}><Icon name="close" /></button></div>
        {#if error}<p class="alert alert-err" role="alert">{error}</p>{/if}
        {#if recoveryCodes.length}
          <p class="alert alert-warn">{$t('mfa.recoveryNote')}</p>
          <ul style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0;padding:0;list-style:none">{#each recoveryCodes as code}<li class="recovery-code">{code}</li>{/each}</ul>
        {:else if user?.mfa_enabled}
          <p class="alert alert-ok">{$t('mfa.active')}</p>
          <div class="card" style="display:grid;gap:10px">
            <label><span class="label">{$t('mfa.authCode')}</span><input class="field" value={mfaCode} on:input={changeMfaCode} type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" aria-describedby="mfa-code-help" /></label>
            <p id="mfa-code-help" class="muted" style="margin:0;font-size:13px">{$t('mfa.help')}</p>
            <button type="button" class="btn btn-danger" on:click={disableMfa}>{$t('mfa.disable')}</button>
          </div>
        {:else}
          <p class="muted" style="font-size:15px">{$t('mfa.scan')}</p>
          {#if mfaQr}<img style="margin:0 auto 10px;height:192px;width:192px;border-radius:12px;background:#fff;padding:6px" src={mfaQr} alt={$t('mfa.qrAlt')} /><p class="muted" style="word-break:break-all;text-align:center;font-size:12px">{$t('mfa.manualKey', { key: mfaSecret })}</p>{/if}
          <div class="card" style="display:grid;gap:10px">
            <label><span class="label">{$t('mfa.verifyCode')}</span><input class="field" value={mfaCode} on:input={changeMfaCode} type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6" aria-describedby="mfa-code-help" /></label>
            <p id="mfa-code-help" class="muted" style="margin:0;font-size:13px">{$t('mfa.help')}</p>
            <button type="button" class="btn btn-primary" on:click={enableMfa}>{$t('mfa.enable')}</button>
          </div>
        {/if}
      </div>
    </div>
  {/if}
{/if}
