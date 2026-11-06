<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { api, ApiError } from '$lib/api';
  import { app } from '$lib/state/app.svelte';
  import { plainText, relativeTime } from '$lib/format';
  import DismissSplitButton from '$lib/components/DismissSplitButton.svelte';
  import WhyMatchedCard from '$lib/components/WhyMatchedCard.svelte';
  import type {
    DismissalScope,
    JobCounts,
    JobQueryFilters,
    JobTriageItem,
    TriageStatus
  } from '$lib/types';

  const PAGE_SIZE = 50;
  const STATUSES: TriageStatus[] = ['new', 'saved', 'applied'];

  // -- filter state ---------------------------------------------------------
  let matchMode = $state<'matched' | 'all' | 'unmatched'>('matched');
  let userStatus = $state<TriageStatus | ''>('');
  let alertStatus = $state<'alerted' | 'pending_alert' | ''>('');
  let remoteOnly = $state(false);
  let profile = $state('');
  let sourceType = $state('');
  let sourceInput = $state('');
  let sourceIdentifier = $state('');
  let queryInput = $state('');
  let q = $state('');
  let sortBy = $state<'affinity_score' | 'posted_at' | 'first_seen_at'>('affinity_score');
  let sortOrder = $state<'desc' | 'asc'>('desc');
  let profileNames = $state<string[]>([]);

  // -- feed state -----------------------------------------------------------
  let jobs = $state<JobTriageItem[]>([]);
  let total = $state(0);
  let counts = $state<JobCounts>({ matched: 0, saved: 0, applied: 0, pending_alert: 0 });
  let loading = $state(true);
  let loadingMore = $state(false);
  let error = $state<string | null>(null);
  let selectedKey = $state<string | null>(null);
  let detail = $state<JobTriageItem | null>(null);
  let detailLoading = $state(false);
  let feedToken = 0;
  let detailToken = 0;

  const selected = $derived(jobs.find((j) => j.job_key === selectedKey) ?? null);
  const description = $derived(plainText(detail?.description));
  const hasMore = $derived(jobs.length < total);

  const STATUS_TABS: Array<{ value: TriageStatus | ''; label: string; count?: () => number }> = [
    { value: '', label: 'All', count: () => counts.matched },
    { value: 'new', label: 'New' },
    { value: 'saved', label: 'Saved', count: () => counts.saved },
    { value: 'applied', label: 'Applied', count: () => counts.applied }
  ];
  const MATCH_MODES: Array<{ value: typeof matchMode; label: string }> = [
    { value: 'matched', label: 'Matched Only' },
    { value: 'all', label: 'All Stored Jobs' },
    { value: 'unmatched', label: 'Unmatched' }
  ];

  onMount(async () => {
    try {
      profileNames = (await api.getConfig()).search_profiles.map((p) => p.name);
    } catch {
      // The dropdown just stays empty; the feed itself does not depend on config.
    }
  });

  // Debounce free-text search.
  $effect(() => {
    const text = queryInput;
    const timer = setTimeout(() => (q = text.trim()), 300);
    return () => clearTimeout(timer);
  });

  // Debounce the free-text source identifier filter.
  $effect(() => {
    const text = sourceInput;
    const timer = setTimeout(() => (sourceIdentifier = text.trim()), 300);
    return () => clearTimeout(timer);
  });

  function currentFilters(): JobQueryFilters {
    return {
      match_mode: matchMode,
      user_status: userStatus || undefined,
      alert_status: alertStatus || undefined,
      is_remote: remoteOnly ? true : undefined,
      profile: profile || undefined,
      source_type: sourceType || undefined,
      source_identifier: sourceIdentifier || undefined,
      q: q || undefined,
      sort_by: sortBy,
      sort_order: sortOrder
    };
  }

  async function load(filters: JobQueryFilters, append = false, limit = PAGE_SIZE) {
    const token = ++feedToken;
    if (append) loadingMore = true;
    else loading = true;
    try {
      const res = await api.listJobs({
        ...filters,
        limit,
        offset: append ? jobs.length : 0
      });
      if (token !== feedToken) return; // a newer request superseded this one
      jobs = append ? [...jobs, ...res.items] : res.items;
      total = res.total;
      counts = res.counts;
      error = null;
      if (!append && !jobs.some((j) => j.job_key === selectedKey)) {
        const desktop = window.matchMedia('(min-width: 1024px)').matches;
        selectedKey = desktop ? (jobs[0]?.job_key ?? null) : null;
      }
    } catch (e) {
      if (token !== feedToken) return;
      error = e instanceof ApiError ? e.detail : 'Could not load jobs';
    } finally {
      if (token === feedToken) {
        loading = false;
        loadingMore = false;
      }
    }
  }

  // Refetch whenever a filter changes or another component bumps feedVersion.
  $effect(() => {
    app.feedVersion;
    const filters = currentFilters();
    untrack(() => load(filters));
  });

  // Fetch the full record (with description) for the selected job.
  $effect(() => {
    const key = selectedKey;
    app.feedVersion;
    if (!key) {
      detail = null;
      return;
    }
    const token = ++detailToken;
    detailLoading = true;
    api
      .getJob(key)
      .then((item) => {
        if (token === detailToken) detail = item;
      })
      .catch(() => {
        if (token === detailToken) detail = null;
      })
      .finally(() => {
        if (token === detailToken) detailLoading = false;
      });
  });

  async function refreshCounts() {
    try {
      counts = (await api.listJobs({ match_mode: 'matched', limit: 1 })).counts;
    } catch {
      // counts are cosmetic; keep the old ones
    }
  }

  async function setStatus(job: JobTriageItem, next: TriageStatus) {
    if (job.user_status === next) return;
    const previous = job.user_status;
    patchJob(job.job_key, { user_status: next });
    try {
      await api.updateJobStatus(job.job_key, next);
      refreshCounts();
    } catch (e) {
      patchJob(job.job_key, { user_status: previous });
      error = e instanceof ApiError ? e.detail : 'Could not update status';
    }
  }

  function patchJob(key: string, patch: Partial<JobTriageItem>) {
    jobs = jobs.map((j) => (j.job_key === key ? { ...j, ...patch } : j));
    if (detail?.job_key === key) detail = { ...detail, ...patch };
  }

  // -- reactive dismissal ---------------------------------------------------
  function isHidden(job: JobTriageItem, scope: DismissalScope, key: string): boolean {
    const k = key.trim().toLowerCase();
    if (scope === 'job') return job.job_key.trim().toLowerCase() === k;
    if (scope === 'company') return job.company.trim().toLowerCase() === k;
    return job.source_identifier.trim().toLowerCase() === k;
  }

  async function dismiss(request: { scope: DismissalScope; item_key: string; reason: string | null }) {
    const { scope, item_key, reason } = request;
    // Optimistic: drop matching rows now; roll back if the API rejects.
    const snapshot = { jobs, total, selectedKey };
    // Undo re-fetches at least as many rows as were loaded, so "Load more" pages survive.
    const restoreLimit = Math.min(200, Math.max(PAGE_SIZE, jobs.length));
    const removed = jobs.filter((j) => isHidden(j, scope, item_key)).length;
    const index = jobs.findIndex((j) => j.job_key === selectedKey);
    jobs = jobs.filter((j) => !isHidden(j, scope, item_key));
    total = Math.max(0, total - removed);
    if (selectedKey && !jobs.some((j) => j.job_key === selectedKey)) {
      selectedKey = jobs[Math.min(Math.max(index, 0), jobs.length - 1)]?.job_key ?? null;
    }
    try {
      const res = await api.createDismissal({ scope, item_key, reason });
      const n = res.hidden_job_count;
      app.showToast(
        `Dismissed ${scope} ${item_key} (${n} job${n === 1 ? '' : 's'} hidden)`,
        async () => {
          try {
            await api.deleteDismissal(scope, item_key);
          } catch (e) {
            error = e instanceof ApiError ? e.detail : 'Could not undo dismissal';
          }
          await load(currentFilters(), false, restoreLimit);
        }
      );
      refreshCounts();
    } catch (e) {
      jobs = snapshot.jobs;
      total = snapshot.total;
      selectedKey = snapshot.selectedKey;
      error = e instanceof ApiError ? e.detail : 'Could not dismiss';
    }
  }

  // -- keyboard shortcuts: j/k move, n/s/a set Triage Status, d dismiss job ---
  function onkeydown(event: KeyboardEvent) {
    if (event.metaKey || event.ctrlKey || event.altKey || app.dismissalsDrawerOpen) return;
    const el = event.target as HTMLElement | null;
    if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
    const i = jobs.findIndex((j) => j.job_key === selectedKey);
    switch (event.key) {
      case 'j':
        if (jobs.length) selectedKey = jobs[Math.min(jobs.length - 1, i + 1)].job_key;
        break;
      case 'k':
        if (jobs.length) selectedKey = jobs[Math.max(0, i - 1)].job_key;
        break;
      case 'n':
      case 's':
      case 'a':
        if (selected) setStatus(selected, { n: 'new', s: 'saved', a: 'applied' }[event.key] as TriageStatus);
        break;
      case 'd':
        if (selected) dismiss({ scope: 'job', item_key: selected.job_key, reason: null });
        break;
      default:
        return;
    }
    event.preventDefault();
  }

  // -- presentation helpers -------------------------------------------------
  const STATUS_STYLE: Record<TriageStatus, string> = {
    new: 'border-slate-300 text-slate-700',
    saved: 'border-sky-500 bg-sky-100 text-sky-800',
    applied: 'border-emerald-500 bg-emerald-100 text-emerald-800'
  };

  function alertBadge(job: JobTriageItem): { label: string; cls: string; title?: string } {
    if (job.alert_status === 'alerted') {
      return {
        label: 'Alerted',
        cls: 'bg-emerald-100 text-emerald-800',
        title: job.alert_sent_at ? `Sent ${new Date(job.alert_sent_at).toLocaleString()}` : undefined
      };
    }
    if (job.alert_status === 'pending_alert') {
      return { label: 'Pending Alert', cls: 'bg-amber-100 text-amber-800', title: 'Will be included in the next digest' };
    }
    return { label: 'None', cls: 'bg-slate-100 text-slate-600' };
  }
</script>

<svelte:window {onkeydown} />

<div class="flex h-full flex-col">
  <!-- Filter & sort bar -->
  <section class="space-y-2 border-b border-slate-200 bg-white px-4 py-2" aria-label="Filters">
    <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div class="flex" role="group" aria-label="Match mode">
        {#each MATCH_MODES as m, i (m.value)}
          <button
            type="button"
            class="border px-3 py-1 text-xs focus:outline-none focus-visible:z-10 focus-visible:ring-2 focus-visible:ring-sky-400 {i === 0 ? 'rounded-l' : '-ml-px'} {i === MATCH_MODES.length - 1 ? 'rounded-r' : ''} {matchMode === m.value ? 'border-sky-600 bg-sky-600 text-white' : 'border-slate-300 hover:bg-slate-50'}"
            aria-pressed={matchMode === m.value}
            onclick={() => (matchMode = m.value)}
          >
            {m.label}
          </button>
        {/each}
      </div>

      <div class="flex gap-1" role="group" aria-label="Triage status">
        {#each STATUS_TABS as tab (tab.value)}
          <button
            type="button"
            class="rounded-full border px-3 py-1 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {userStatus === tab.value ? 'border-slate-800 bg-slate-800 text-white' : 'border-slate-300 hover:bg-slate-50'}"
            aria-pressed={userStatus === tab.value}
            onclick={() => (userStatus = tab.value)}
          >
            {tab.label}{#if tab.count} <span class="tabular-nums opacity-75">{tab.count()}</span>{/if}
          </button>
        {/each}
      </div>

      <label class="flex items-center gap-1.5 text-xs">
        <input type="checkbox" bind:checked={remoteOnly} class="rounded border-slate-300" />
        Remote only
      </label>
    </div>

    <div class="flex flex-wrap items-center gap-2 text-xs">
      <label class="flex items-center gap-1">
        <span class="text-slate-500">Alert</span>
        <select bind:value={alertStatus} class="rounded border border-slate-300 px-2 py-1">
          <option value="">All</option>
          <option value="alerted">Alerted</option>
          <option value="pending_alert">Pending Alert ({counts.pending_alert})</option>
        </select>
      </label>
      <label class="flex items-center gap-1">
        <span class="text-slate-500">Profile</span>
        <select bind:value={profile} class="rounded border border-slate-300 px-2 py-1">
          <option value="">All profiles</option>
          {#each profileNames as name (name)}<option value={name}>{name}</option>{/each}
        </select>
      </label>
      <label class="flex items-center gap-1">
        <span class="text-slate-500">Source type</span>
        <select bind:value={sourceType} class="rounded border border-slate-300 px-2 py-1">
          <option value="">All</option>
          {#each ['greenhouse', 'lever', 'ashby', 'rss', 'serpapi_google'] as t (t)}<option value={t}>{t}</option>{/each}
        </select>
      </label>
      <label class="flex items-center gap-1">
        <span class="text-slate-500">Source</span>
        <input
          type="text"
          bind:value={sourceInput}
          placeholder="identifier"
          class="w-28 rounded border border-slate-300 px-2 py-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
        />
      </label>
      <label class="flex items-center gap-1">
        <span class="text-slate-500">Sort</span>
        <select bind:value={sortBy} class="rounded border border-slate-300 px-2 py-1">
          <option value="affinity_score">Affinity Score</option>
          <option value="posted_at">Posted Date</option>
          <option value="first_seen_at">First Seen</option>
        </select>
      </label>
      <button
        type="button"
        class="rounded border border-slate-300 px-2 py-1 hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
        aria-label="Sort order: {sortOrder === 'desc' ? 'descending' : 'ascending'}. Click to toggle."
        onclick={() => (sortOrder = sortOrder === 'desc' ? 'asc' : 'desc')}
      >
        {sortOrder === 'desc' ? '↓ Desc' : '↑ Asc'}
      </button>
      <label class="ml-auto flex-1 sm:max-w-xs">
        <span class="sr-only">Search title, company or description</span>
        <input
          type="search"
          bind:value={queryInput}
          placeholder="Search title, company, description…"
          class="w-full rounded border border-slate-300 px-2.5 py-1 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
        />
      </label>
    </div>
  </section>

  {#if error}
    <p class="bg-red-50 px-4 py-2 text-sm text-red-800" role="alert">
      {error}
      <button type="button" class="ml-2 underline" onclick={() => (error = null)}>Dismiss</button>
    </p>
  {/if}

  <div class="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[26rem_1fr]">
    <!-- Master list -->
    <section class="min-h-0 overflow-y-auto border-r border-slate-200 {selectedKey ? 'hidden lg:block' : ''}" aria-label="Jobs">
      <p class="sticky top-0 z-10 border-b border-slate-100 bg-white/95 px-4 py-1.5 text-xs text-slate-500" aria-live="polite">
        {#if loading}Loading…{:else}{total} job{total === 1 ? '' : 's'}{/if}
      </p>

      {#if !loading && jobs.length === 0}
        <p class="px-4 py-8 text-center text-sm text-slate-500">
          No jobs match these filters.{#if matchMode === 'matched'} Try “All Stored Jobs”.{/if}
        </p>
      {/if}

      <ul class="divide-y divide-slate-100">
        {#each jobs as job (job.job_key)}
          {@const badge = alertBadge(job)}
          <li class={job.job_key === selectedKey ? 'bg-sky-50' : ''}>
            <button
              type="button"
              class="block w-full px-4 pt-3 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400"
              aria-current={job.job_key === selectedKey ? 'true' : undefined}
              onclick={() => (selectedKey = job.job_key)}
            >
              <span class="flex items-start justify-between gap-2">
                <span class="font-medium leading-snug">{job.title}</span>
                <span class="shrink-0 rounded bg-emerald-600 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-white" title="Affinity score">
                  {job.affinity_score.toFixed(1)}
                </span>
              </span>
              <span class="mt-0.5 block text-sm text-slate-600">{job.company}</span>
              <span class="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
                {#if job.is_remote}<span class="rounded bg-violet-100 px-1.5 py-0.5 text-violet-800">Remote</span>{/if}
                {#if job.location_country}<span class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700">{job.location_country}</span>{/if}
                {#if job.secondary_locations.length > 0}
                  <span class="rounded bg-slate-100 px-1.5 py-0.5 text-slate-700" title={job.secondary_locations.join(', ')}>
                    +{job.secondary_locations.length} location{job.secondary_locations.length === 1 ? '' : 's'}
                  </span>
                {/if}
                {#if job.primary_profile}
                  <span class="rounded-full border border-emerald-300 px-2 py-0.5 text-emerald-800">{job.primary_profile}</span>
                {/if}
                <span class="rounded px-1.5 py-0.5 {badge.cls}" title={badge.title}>{badge.label}</span>
                <span class="text-slate-500" title="Posted / first seen">
                  {job.posted_at ? `Posted ${relativeTime(job.posted_at)}` : `Seen ${relativeTime(job.first_seen_at)}`}
                </span>
              </span>
            </button>
            <div class="flex items-center gap-1 px-4 pb-3 pt-2" role="group" aria-label="Triage status for {job.title}">
              {#each STATUSES as s (s)}
                <button
                  type="button"
                  class="rounded-full border px-2.5 py-0.5 text-xs capitalize focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {job.user_status === s ? STATUS_STYLE[s] : 'border-slate-200 text-slate-500 hover:bg-slate-50'}"
                  aria-pressed={job.user_status === s}
                  onclick={() => setStatus(job, s)}
                >
                  {s}
                </button>
              {/each}
              <span class="ml-auto">
                <DismissSplitButton {job} compact ondismiss={dismiss} />
              </span>
            </div>
          </li>
        {/each}
      </ul>

      {#if hasMore}
        <div class="p-4 text-center">
          <button
            type="button"
            class="rounded border border-slate-300 px-4 py-1.5 text-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:opacity-50"
            disabled={loadingMore}
            onclick={() => load(currentFilters(), true)}
          >
            {loadingMore ? 'Loading…' : `Load more (${total - jobs.length} left)`}
          </button>
        </div>
      {/if}
    </section>

    <!-- Detail pane -->
    <section class="min-h-0 overflow-y-auto p-5 {selectedKey ? '' : 'hidden lg:block'}" aria-label="Job detail">
      {#if !selected}
        <p class="py-16 text-center text-sm text-slate-500">
          Select a job to see details. <span class="hidden sm:inline">Shortcuts: j/k move · n/s/a status · d dismiss.</span>
        </p>
      {:else}
        {@const view = detail ?? selected}
        <button
          type="button"
          class="mb-3 text-sm text-sky-700 underline lg:hidden"
          onclick={() => (selectedKey = null)}
        >
          ← Back to list
        </button>

        <header class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="text-xl font-semibold leading-tight">{view.title}</h2>
            <p class="mt-1 text-slate-700">
              {view.company}
              {#if view.location}<span class="text-slate-500"> · {view.location}</span>{/if}
            </p>
            <p class="mt-1 text-xs text-slate-500">
              {view.posted_at ? `Posted ${relativeTime(view.posted_at)} · ` : ''}First seen {relativeTime(view.first_seen_at)}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <a
              href={view.url}
              target="_blank"
              rel="noopener noreferrer"
              class="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-1"
            >
              Open Original Posting ↗
            </a>
            {#key view.job_key}
              <DismissSplitButton job={view} ondismiss={dismiss} />
            {/key}
          </div>
        </header>

        <div class="mt-3 flex gap-1" role="group" aria-label="Triage status">
          {#each STATUSES as s (s)}
            <button
              type="button"
              class="rounded-full border px-3 py-1 text-xs capitalize focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {view.user_status === s ? STATUS_STYLE[s] : 'border-slate-200 text-slate-500 hover:bg-slate-50'}"
              aria-pressed={view.user_status === s}
              onclick={() => setStatus(selected, s)}
            >
              {s}
            </button>
          {/each}
        </div>

        <div class="mt-5">
          <WhyMatchedCard job={view} />
        </div>

        <article class="mt-6" aria-label="Job description">
          <h3 class="mb-2 text-sm font-semibold text-slate-500">Description</h3>
          {#if detailLoading && !detail}
            <p class="text-sm text-slate-500">Loading description…</p>
          {:else if description}
            <div class="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">{description}</div>
          {:else}
            <p class="text-sm text-slate-500">No description available.</p>
          {/if}
        </article>
      {/if}
    </section>
  </div>
</div>
