<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '$lib/api';
  import { app } from '$lib/state/app.svelte';
  import { relativeTime } from '$lib/format';
  import RegistryManagerPanel from '$lib/components/RegistryManagerPanel.svelte';
  import SerpQueryBuilderModal from '$lib/components/SerpQueryBuilderModal.svelte';
  import type {
    ConfigResponse,
    HealthStatus,
    SerpSourceDraft,
    SourceConfigDTO,
    SourcesResponse
  } from '$lib/types';

  type HealthFilter = 'all' | 'error' | 'healthy' | 'unscanned';
  const FILTERS: Array<{ value: HealthFilter; label: string }> = [
    { value: 'all', label: 'All' },
    { value: 'error', label: 'Failing' },
    { value: 'healthy', label: 'Healthy' },
    { value: 'unscanned', label: 'Unscanned' }
  ];
  const SHOW_LIMIT = 100;

  let health = $state<SourcesResponse | null>(null);
  let config = $state<ConfigResponse | null>(null);
  let loadError = $state<string | null>(null);
  let actionError = $state<string | null>(null);
  let busy = $state(false);

  let expanded = $state<string | null>(null);
  let filters = $state<Record<string, HealthFilter>>({});
  let showAllFor = $state<Record<string, boolean>>({});

  let modalOpen = $state(false);
  let editing = $state<SerpSourceDraft | undefined>();
  let editingIdentifier = $state<string | null>(null);

  let newType = $state<'greenhouse' | 'lever' | 'ashby' | 'rss'>('greenhouse');
  let newIdentifier = $state('');
  let newName = $state('');
  let addError = $state<string | null>(null);
  let confirmRemove = $state<string | null>(null);

  const serpQueries = $derived(
    (config?.sources ?? []).filter((s) => s.type === 'serpapi_google').map((s) => s.identifier)
  );

  async function refresh() {
    try {
      [health, config] = await Promise.all([api.getSources(), api.getConfig()]);
      loadError = null;
    } catch (e) {
      loadError = e instanceof ApiError ? e.detail : 'Could not load sources';
    }
  }
  onMount(refresh);

  const key = (type: string, identifier: string) => `${type}:${identifier}`;

  const STATUS_STYLE: Record<HealthStatus, string> = {
    healthy: 'bg-emerald-100 text-emerald-800',
    error: 'bg-red-100 text-red-800',
    unscanned: 'bg-slate-100 text-slate-600',
    disabled: 'bg-slate-100 text-slate-400'
  };
  const STATUS_LABEL: Record<HealthStatus, string> = {
    healthy: 'Healthy',
    error: 'Failing',
    unscanned: 'Unscanned',
    disabled: 'Disabled'
  };

  /** Persist a full replacement of the (raw, wildcard-preserving) sources list. */
  async function saveSources(next: SourceConfigDTO[], message: string): Promise<boolean> {
    busy = true;
    actionError = null;
    try {
      await api.updateConfig({ sources: next });
      app.showToast(message);
      await refresh();
      return true;
    } catch (e) {
      actionError = e instanceof ApiError ? [e.detail, ...e.errors].join(' — ') : 'Save failed';
      return false;
    } finally {
      busy = false;
    }
  }

  function setEnabled(type: string, identifier: string, enabled: boolean) {
    if (!config) return;
    const next = config.sources.map((s) => (s.type === type && s.identifier === identifier ? { ...s, enabled } : s));
    saveSources(next, `Source ${enabled ? 'enabled' : 'disabled'}.`);
  }

  function removeSource(type: string, identifier: string) {
    if (!config) return;
    confirmRemove = null;
    saveSources(
      config.sources.filter((s) => !(s.type === type && s.identifier === identifier)),
      'Source removed.'
    );
  }

  async function addAtsSource() {
    if (!config) return;
    const identifier = newIdentifier.trim();
    addError = null;
    if (!identifier) {
      addError = newType === 'rss' ? 'Enter the feed URL.' : 'Enter the company slug.';
      return;
    }
    if (config.sources.some((s) => s.type === newType && s.identifier.toLowerCase() === identifier.toLowerCase())) {
      addError = `A ${newType} source “${identifier}” already exists.`;
      return;
    }
    const entry: SourceConfigDTO = { name: newName.trim() || identifier, type: newType, identifier, enabled: true, options: {} };
    if (await saveSources([...config.sources, entry], 'Source added.')) {
      newIdentifier = '';
      newName = '';
    }
  }

  function openNewSerp() {
    editing = undefined;
    editingIdentifier = null;
    modalOpen = true;
  }
  function openEditSerp(s: SourceConfigDTO) {
    editing = { name: s.name, query: s.identifier, num_results: Number(s.options.num_results ?? 20) };
    editingIdentifier = s.identifier;
    modalOpen = true;
  }

  async function saveSerp(draft: SerpSourceDraft) {
    if (!config) return;
    let next: SourceConfigDTO[];
    if (editingIdentifier !== null) {
      next = config.sources.map((s) =>
        s.type === 'serpapi_google' && s.identifier === editingIdentifier
          ? { ...s, name: draft.name, identifier: draft.query, options: { ...s.options, num_results: draft.num_results } }
          : s
      );
    } else {
      next = [
        ...config.sources,
        { name: draft.name, type: 'serpapi_google', identifier: draft.query, enabled: true, options: { num_results: draft.num_results } }
      ];
    }
    if (await saveSources(next, editingIdentifier !== null ? 'Source updated.' : 'Source added.')) modalOpen = false;
  }

  function sourceFor(type: string, identifier: string): SourceConfigDTO | undefined {
    return config?.sources.find((s) => s.type === type && s.identifier === identifier);
  }
</script>

<div class="h-full overflow-y-auto">
  <div class="mx-auto max-w-5xl space-y-8 p-5">
    <div>
      <h2 class="text-lg font-semibold">Sources</h2>
      <p class="text-sm text-slate-600">Health of every ATS board the scanner polls.</p>
    </div>

    {#if actionError}<p class="rounded bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">{actionError}</p>{/if}

    {#if loadError}
      <p class="py-10 text-center text-sm text-red-700" role="alert">{loadError}</p>
    {:else if !health || !config}
      <p class="py-10 text-center text-sm text-slate-500">Loading sources…</p>
    {:else}
      <!-- Wildcard Source Rollups -->
      <section aria-label="Wildcard Source Rollups">
        <h3 class="mb-2 text-sm font-semibold">Wildcard Source Rollups</h3>
        {#if health.wildcard_rollups.length === 0}
          <p class="text-sm text-slate-500">No wildcard sources are configured.</p>
        {/if}
        <div class="space-y-3">
          {#each health.wildcard_rollups as r (r.type)}
            {@const id = key(r.type, '*')}
            {@const f = filters[id] ?? 'all'}
            {@const rows = r.companies.filter((c) => f === 'all' || c.health_status === f)}
            <article class="rounded-lg border border-slate-200">
              <div class="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                <button
                  type="button"
                  class="flex min-w-0 flex-1 items-center gap-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
                  aria-expanded={expanded === id}
                  aria-controls="rollup-{r.type}"
                  onclick={() => (expanded = expanded === id ? null : id)}
                >
                  <span aria-hidden="true">{expanded === id ? '▾' : '▸'}</span>
                  <span>
                    <span class="font-medium">{r.name}</span>
                    <span class="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs capitalize text-slate-700">{r.type}</span>
                  </span>
                </button>
                <dl class="flex gap-4 text-xs">
                  <div><dt class="text-slate-500">Enabled</dt><dd class="font-semibold tabular-nums">{r.total_enabled}<span class="font-normal text-slate-400"> / {r.total_companies}</span></dd></div>
                  <div><dt class="text-slate-500">Healthy</dt><dd class="font-semibold tabular-nums text-emerald-700">{r.healthy_count}</dd></div>
                  <div><dt class="text-slate-500">Failing</dt><dd class="font-semibold tabular-nums {r.error_count ? 'text-red-700' : ''}">{r.error_count}</dd></div>
                  <div><dt class="text-slate-500">Unscanned</dt><dd class="font-semibold tabular-nums">{r.unscanned_count}</dd></div>
                </dl>
                <label class="flex items-center gap-1.5 text-xs">
                  <input type="checkbox" checked={r.enabled} disabled={busy} class="rounded" onchange={(e) => setEnabled(r.type, '*', e.currentTarget.checked)} />
                  Enabled
                </label>
              </div>
              <p class="px-4 pb-2 text-xs text-slate-500">
                {r.last_success_at ? `Last success ${relativeTime(r.last_success_at)}` : 'No successful scan yet'}{r.last_error_at ? ` · last error ${relativeTime(r.last_error_at)}` : ''}
              </p>

              {#if expanded === id}
                <div id="rollup-{r.type}" class="border-t border-slate-100 px-4 py-3">
                  <div class="mb-2 flex gap-1" role="group" aria-label="Filter companies by health">
                    {#each FILTERS as opt (opt.value)}
                      <button
                        type="button"
                        class="rounded-full border px-3 py-0.5 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {f === opt.value ? 'border-slate-800 bg-slate-800 text-white' : 'border-slate-300 hover:bg-slate-50'}"
                        aria-pressed={f === opt.value}
                        onclick={() => { filters[id] = opt.value; showAllFor[id] = false; }}
                      >
                        {opt.label}
                      </button>
                    {/each}
                  </div>
                  {#if rows.length === 0}
                    <p class="py-3 text-sm text-slate-500">No companies in this state.</p>
                  {:else}
                    <div class="max-h-96 overflow-y-auto">
                      <table class="w-full text-left text-xs">
                        <thead class="sticky top-0 bg-white text-slate-500">
                          <tr><th class="py-1 pr-2 font-medium">Company</th><th class="pr-2 font-medium">Status</th><th class="pr-2 font-medium">Last success</th><th class="pr-2 font-medium">Last error</th></tr>
                        </thead>
                        <tbody class="divide-y divide-slate-100">
                          {#each showAllFor[id] ? rows : rows.slice(0, SHOW_LIMIT) as c (c.identifier)}
                            <tr class="align-top">
                              <td class="py-1.5 pr-2"><span class="font-medium">{c.name}</span> <code class="text-slate-500">{c.identifier}</code></td>
                              <td class="pr-2"><span class="rounded px-1.5 py-0.5 {STATUS_STYLE[c.health_status]}">{STATUS_LABEL[c.health_status]}</span></td>
                              <td class="pr-2 text-slate-600">{relativeTime(c.last_success_at) || '—'}</td>
                              <td class="pr-2 text-slate-600">
                                {relativeTime(c.last_error_at) || '—'}
                                {#if c.error_message}<span class="block max-w-xs break-words text-red-700">{c.error_message}</span>{/if}
                              </td>
                            </tr>
                          {/each}
                        </tbody>
                      </table>
                      {#if !showAllFor[id] && rows.length > SHOW_LIMIT}
                        <button type="button" class="w-full py-2 text-xs text-sky-700 hover:underline" onclick={() => (showAllFor[id] = true)}>Show all {rows.length}</button>
                      {/if}
                    </div>
                  {/if}
                </div>
              {/if}
            </article>
          {/each}
        </div>
      </section>

      <!-- Explicit sources -->
      <section aria-label="Explicit sources">
        <div class="mb-2 flex items-center justify-between">
          <h3 class="text-sm font-semibold">Explicit Sources</h3>
          <button type="button" class="rounded border border-slate-300 px-2.5 py-1 text-xs hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400" onclick={openNewSerp}>+ Google discovery source</button>
        </div>
        {#if health.explicit_sources.length === 0}
          <p class="text-sm text-slate-500">No explicit sources.</p>
        {:else}
          <ul class="divide-y divide-slate-100 rounded-lg border border-slate-200">
            {#each health.explicit_sources as s (key(s.type, s.identifier))}
              {@const cfg = sourceFor(s.type, s.identifier)}
              <li class="flex flex-wrap items-start gap-3 px-4 py-2.5 text-sm">
                <input type="checkbox" checked={s.enabled} disabled={busy} aria-label="Enable {s.name}" class="mt-1 rounded" onchange={(e) => setEnabled(s.type, s.identifier, e.currentTarget.checked)} />
                <div class="min-w-0 flex-1">
                  <p class="flex flex-wrap items-center gap-2">
                    <span class="font-medium {s.enabled ? '' : 'text-slate-400'}">{s.name}</span>
                    <span class="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">{s.type}</span>
                    <span class="rounded px-1.5 py-0.5 text-xs {STATUS_STYLE[s.health_status]}">{STATUS_LABEL[s.health_status]}</span>
                  </p>
                  <p class="mt-0.5 truncate font-mono text-xs text-slate-500" title={s.identifier}>{s.identifier}</p>
                  {#if s.error_message}<p class="mt-0.5 break-words text-xs text-red-700">{s.error_message}</p>{/if}
                  <p class="mt-0.5 text-xs text-slate-500">
                    {s.last_success_at ? `Last success ${relativeTime(s.last_success_at)}` : 'Never scanned successfully'}{s.last_error_at ? ` · last error ${relativeTime(s.last_error_at)}` : ''}
                  </p>
                </div>
                <div class="flex gap-1 text-xs">
                  {#if s.type === 'serpapi_google' && cfg}
                    <button type="button" class="rounded border border-slate-300 px-2 py-0.5 hover:bg-slate-50" onclick={() => openEditSerp(cfg)}>Edit query</button>
                  {/if}
                  {#if confirmRemove === key(s.type, s.identifier)}
                    <button type="button" class="rounded bg-red-600 px-2 py-0.5 text-white" onclick={() => removeSource(s.type, s.identifier)}>Confirm</button>
                    <button type="button" class="rounded border border-slate-300 px-2 py-0.5" onclick={() => (confirmRemove = null)}>Cancel</button>
                  {:else}
                    <button type="button" class="rounded border border-red-300 px-2 py-0.5 text-red-700 hover:bg-red-50" onclick={() => (confirmRemove = key(s.type, s.identifier))}>Remove</button>
                  {/if}
                </div>
              </li>
            {/each}
          </ul>
        {/if}

        <form class="mt-3 flex flex-wrap items-end gap-2 text-sm" onsubmit={(e) => { e.preventDefault(); addAtsSource(); }}>
          <label class="block">
            <span class="text-xs text-slate-600">Type</span>
            <select bind:value={newType} class="mt-0.5 block rounded border border-slate-300 px-2 py-1">
              <option value="greenhouse">greenhouse</option>
              <option value="lever">lever</option>
              <option value="ashby">ashby</option>
              <option value="rss">rss</option>
            </select>
          </label>
          <label class="block">
            <span class="text-xs text-slate-600">{newType === 'rss' ? 'Feed URL' : 'Company slug'}</span>
            <input type="text" bind:value={newIdentifier} class="mt-0.5 block w-52 rounded border border-slate-300 px-2 py-1" />
          </label>
          <label class="block">
            <span class="text-xs text-slate-600">Name</span>
            <input type="text" bind:value={newName} placeholder="optional" class="mt-0.5 block w-44 rounded border border-slate-300 px-2 py-1" />
          </label>
          <button type="submit" class="rounded border border-slate-300 px-3 py-1 hover:bg-slate-50 disabled:opacity-50" disabled={busy}>+ Add source</button>
        </form>
        {#if addError}<p class="mt-1 text-xs text-red-700" role="alert">{addError}</p>{/if}
      </section>

      <RegistryManagerPanel onchange={refresh} />
    {/if}
  </div>
</div>

<SerpQueryBuilderModal
  open={modalOpen}
  initial={editing}
  serpapiConfigured={config?.env_status.serpapi_configured ?? false}
  takenQueries={serpQueries}
  onsave={saveSerp}
  onclose={() => (modalOpen = false)}
/>
