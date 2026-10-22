<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api';
  import type { PasskeyInfo, RatesInfo, SessionInfo, TelegramPrefs } from './api';
  import Icon from './Icon.svelte';
  import { locale, t } from './i18n';
  import { PasskeyCancelled, passkeysSupported, registerPasskey } from './passkey';
  import type { User } from './types';

  export let user: User;
  export let notify: (message: string) => void;
  export let fail: (message: string) => void;
  export let onRatesChanged: () => void = () => {};

  let sessions: SessionInfo[] = [];
  let passkeys: PasskeyInfo[] = [];
  let prefs: TelegramPrefs = { linked: false };
  let rates: RatesInfo | null = null;
  let passkeyName = '';
  let rateCurrency = '';
  let rateValue = '';
  let busy = false;
  let pwCurrent = '';
  let pwNew = '';
  let pwConfirm = '';
  let pwCode = '';

  const fmt = (value: string | null) => (value ? new Date(value).toLocaleString($locale, { dateStyle: 'medium', timeStyle: 'short' }) : '—');
  const errorText = (e: unknown) => (e instanceof Error ? e.message : $t('common.genericError'));

  function deviceName(agent: string): string {
    if (!agent) return $t('sess.unknown');
    const os = /iPhone/.test(agent) ? 'iPhone' : /iPad/.test(agent) ? 'iPad' : /Android/.test(agent) ? 'Android'
      : /Mac OS X/.test(agent) ? 'Mac' : /Windows/.test(agent) ? 'Windows' : /Linux/.test(agent) ? 'Linux' : '';
    const browser = /Edg\//.test(agent) ? 'Edge' : /Firefox\//.test(agent) ? 'Firefox' : /Chrome\//.test(agent) ? 'Chrome'
      : /Safari\//.test(agent) ? 'Safari' : '';
    return [browser, os].filter(Boolean).join(' · ') || $t('sess.unknown');
  }

  async function load() {
    try {
      [sessions, passkeys, prefs, rates] = await Promise.all([api.sessions(), api.passkeys(), api.telegramPrefs(), api.rates()]);
    } catch (e) { fail(errorText(e)); }
  }
  onMount(load);

  async function revoke(item: SessionInfo) {
    try { await api.revokeSession(item.id); sessions = await api.sessions(); notify($t('sess.revoked')); }
    catch (e) { fail(errorText(e)); }
  }

  async function revokeOthers() {
    try { await api.revokeOtherSessions(); sessions = await api.sessions(); notify($t('sess.revokedOthers')); }
    catch (e) { fail(errorText(e)); }
  }

  async function changePassword(event: SubmitEvent) {
    event.preventDefault();
    if (pwNew.length < 12) { fail($t('pw.tooShort')); return; }
    if (pwNew !== pwConfirm) { fail($t('pw.mismatch')); return; }
    if (pwNew === pwCurrent) { fail($t('pw.same')); return; }
    busy = true;
    try {
      const { revoked } = await api.changePassword(pwCurrent, pwNew, pwCode.trim());
      pwCurrent = pwNew = pwConfirm = pwCode = '';
      sessions = await api.sessions();
      notify($t('pw.changed', { n: revoked }));
    } catch (e) { fail(errorText(e)); }
    finally { busy = false; }
  }

  async function addPasskey() {
    busy = true;
    try {
      await registerPasskey(passkeyName.trim() || deviceName(navigator.userAgent));
      passkeyName = '';
      passkeys = await api.passkeys();
      notify($t('passkey.added'));
    } catch (e) { fail(e instanceof PasskeyCancelled ? $t('passkey.cancelled') : errorText(e)); }
    finally { busy = false; }
  }

  async function removePasskey(item: PasskeyInfo) {
    if (!confirm($t('passkey.removeConfirm', { name: item.name }))) return;
    try { await api.deletePasskey(item.id); passkeys = await api.passkeys(); notify($t('passkey.removed')); }
    catch (e) { fail(errorText(e)); }
  }

  async function savePrefs(change: Partial<TelegramPrefs>) {
    try { prefs = await api.setTelegramPrefs(change); notify($t('tgp.saved')); }
    catch (e) { fail(errorText(e)); }
  }

  async function addRate(event: SubmitEvent) {
    event.preventDefault();
    const code = rateCurrency.trim().toUpperCase();
    const value = Number(rateValue.replace(',', '.'));
    if (!/^[A-Z]{3}$/.test(code) || !(value > 0)) { fail($t('rates.invalid')); return; }
    try {
      await api.setRate(code, value);
      rateCurrency = ''; rateValue = '';
      rates = await api.rates();
      onRatesChanged();
      notify($t('rates.saved'));
    } catch (e) { fail(errorText(e)); }
  }

  async function removeRate(code: string) {
    try { await api.deleteRate(code); rates = await api.rates(); onRatesChanged(); notify($t('rates.removed')); }
    catch (e) { fail(errorText(e)); }
  }

  async function refreshRates() {
    try {
      const { updated } = await api.refreshRates();
      rates = await api.rates();
      onRatesChanged();
      notify($t('rates.updated', { n: updated }));
    } catch (e) { fail(errorText(e)); }
  }

  $: others = sessions.filter((item) => !item.current).length;
</script>

<div class="group-title">{$t('pw.title')}</div>
<form class="group" on:submit={changePassword}>
  <label class="row"><span style="width:150px;flex:none">{$t('pw.current')}</span><input class="field" style="background:transparent" type="password" bind:value={pwCurrent} required maxlength="128" autocomplete="current-password" /></label>
  <label class="row"><span style="width:150px;flex:none">{$t('pw.new')}</span><input class="field" style="background:transparent" type="password" bind:value={pwNew} required minlength="12" maxlength="128" autocomplete="new-password" /></label>
  <label class="row"><span style="width:150px;flex:none">{$t('pw.confirm')}</span><input class="field" style="background:transparent" type="password" bind:value={pwConfirm} required minlength="12" maxlength="128" autocomplete="new-password" /></label>
  {#if user.mfa_enabled}
    <label class="row"><span style="width:150px;flex:none">{$t('pw.mfa')}</span><input class="field" style="background:transparent" bind:value={pwCode} required maxlength="32" autocomplete="one-time-code" /></label>
  {/if}
  <div class="row"><button class="btn btn-primary btn-sm" type="submit" disabled={busy}>{$t('pw.change')}</button></div>
</form>
<p class="muted" style="margin:6px 16px 0;font-size:13px">{$t('pw.hint')}</p>

<div class="group-title">{$t('passkey.title')}</div>
<div class="group">
  {#each passkeys as item (item.id)}
    <div class="row">
      <span class="icon-tile" style="background:#8dbccb" aria-hidden="true"><Icon name="lock" /></span>
      <span class="row-main"><span class="row-title">{item.name}</span><span class="row-sub">{item.last_used_at ? $t('passkey.lastUsed', { date: fmt(item.last_used_at) }) : $t('passkey.neverUsed')}</span></span>
      <button class="btn btn-sm btn-danger" on:click={() => removePasskey(item)}>{$t('common.remove')}</button>
    </div>
  {/each}
  {#if passkeys.length === 0}<div class="row muted">{$t('passkey.none')}</div>{/if}
  <div class="row" style="flex-wrap:wrap;gap:10px">
    {#if passkeysSupported()}
      <input class="field" style="flex:1;min-width:160px" bind:value={passkeyName} maxlength="80" placeholder={$t('passkey.name')} aria-label={$t('passkey.name')} />
      <button class="btn btn-primary btn-sm" disabled={busy} on:click={addPasskey}>{$t('passkey.add')}</button>
    {:else}
      <span class="muted">{$t('passkey.unsupported')}</span>
    {/if}
  </div>
</div>
<p class="muted" style="margin:6px 16px 0;font-size:13px">{$t('passkey.hint')}</p>

<div class="group-title">{$t('sess.title')}</div>
<div class="group">
  {#each sessions as item (item.id)}
    <div class="row">
      <span class="row-main">
        <span class="row-title" style="font-weight:600">{deviceName(item.user_agent)}{#if item.current}<span class="pill" style="margin-left:8px">{$t('sess.current')}</span>{/if}</span>
        <span class="row-sub">{$t(item.method === 'passkey' ? 'sess.passkey' : 'sess.password')} · {$t('sess.lastSeen', { date: fmt(item.last_seen_at ?? item.created_at) })}</span>
      </span>
      {#if !item.current}<button class="btn btn-sm btn-danger" on:click={() => revoke(item)}>{$t('sess.revoke')}</button>{/if}
    </div>
  {/each}
  {#if others > 0}
    <button class="row" style="color:var(--danger)" on:click={revokeOthers}><span class="row-main row-title">{$t('sess.revokeOthers')}</span></button>
  {/if}
</div>

<div class="group-title">{$t('tgp.title')}</div>
{#if prefs.linked}
  <div class="group">
    <div class="row"><span class="row-main">{$t('tgp.summary')}</span>
      <div class="seg" style="min-width:230px" role="group" aria-label={$t('tgp.summary')}>
        {#each ['off', 'weekly', 'monthly'] as option}
          <button aria-pressed={prefs.notify_period === option} on:click={() => savePrefs({ notify_period: option as 'off' | 'weekly' | 'monthly' })}>{$t(`tgp.${option}`)}</button>
        {/each}
      </div>
    </div>
    <label class="row"><span class="row-main">{$t('tgp.budget')}</span><input type="checkbox" checked={prefs.notify_budget} on:change={(event) => savePrefs({ notify_budget: event.currentTarget.checked })} /></label>
    <div class="row"><span class="row-main">{$t('tgp.language')}</span>
      <div class="seg" style="min-width:200px" role="group" aria-label={$t('tgp.language')}>
        <button aria-pressed={prefs.language === 'it'} on:click={() => savePrefs({ language: 'it' })}>Italiano</button>
        <button aria-pressed={prefs.language === 'en'} on:click={() => savePrefs({ language: 'en' })}>English</button>
      </div>
    </div>
  </div>
{:else}
  <p class="card muted" style="margin:0">{$t('tgp.notLinked')}</p>
{/if}

<div class="group-title">{$t('rates.title')}</div>
<div class="group">
  {#each rates?.rates ?? [] as item (item.currency)}
    <div class="row">
      <span class="row-main"><span class="row-title">1 {item.currency} = {Number(item.rate).toLocaleString($locale, { maximumFractionDigits: 6 })} EUR</span></span>
      {#if user.is_admin}<button class="btn btn-sm btn-danger" on:click={() => removeRate(item.currency)}>{$t('common.remove')}</button>{/if}
    </div>
  {/each}
  {#if !rates?.rates.length}<div class="row muted">{$t('rates.none')}</div>{/if}
  {#if user.is_admin}
    <form class="row" style="flex-wrap:wrap;gap:10px" on:submit={addRate}>
      <input class="field" style="width:90px" bind:value={rateCurrency} maxlength="3" placeholder={$t('rates.currency')} aria-label={$t('rates.currency')} list="used-currencies" autocapitalize="characters" />
      <datalist id="used-currencies">{#each rates?.used_currencies ?? [] as code}<option value={code}></option>{/each}</datalist>
      <input class="field" style="flex:1;min-width:110px" bind:value={rateValue} inputmode="decimal" placeholder={$t('rates.rate')} aria-label={$t('rates.rate')} />
      <button class="btn btn-primary btn-sm">{$t('rates.add')}</button>
    </form>
    {#if rates?.auto}<button class="row" on:click={refreshRates}><span class="row-main row-title">{$t('rates.refresh')}</span></button>{/if}
  {/if}
</div>
<p class="muted" style="margin:6px 16px 0;font-size:13px">{user.is_admin ? $t('rates.hint') : $t('rates.adminOnly')}</p>
