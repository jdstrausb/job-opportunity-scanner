<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '$lib/api';
  import { app } from '$lib/state/app.svelte';
  import ChipInput from '$lib/components/ChipInput.svelte';
  import { validateSearchProfile, type ProfileValidationIssue } from '$lib/profile-validator';
  import { blankProfile, duplicateProfile, numericIssues } from '$lib/profile-utils';
  import type { PreviewResponse, SearchProfileDTO } from '$lib/types';

  let profiles = $state<SearchProfileDTO[]>([]);
  let savedSig = $state('');
  let selected = $state(0);
  let loading = $state(true);
  let loadError = $state<string | null>(null);

  let saving = $state(false);
  let previewing = $state(false);
  let actionError = $state<{ detail: string; errors: string[] } | null>(null);
  let banner = $state<string | null>(null);
  let preview = $state<PreviewResponse | null>(null);
  let previewSig = $state('');

  let newContextTrigger = $state('');
  let newWeightTerm = $state('');
  let confirmDelete = $state<number | null>(null);

  const sig = $derived(JSON.stringify(profiles));
  const dirty = $derived(sig !== savedSig);
  const previewStale = $derived(preview !== null && previewSig !== sig);
  const current = $derived<SearchProfileDTO | undefined>(profiles[selected]);

  // Every profile is validated on each edit, so the sidebar can flag problems off-screen.
  const issuesByProfile = $derived<ProfileValidationIssue[][]>(
    profiles.map((p) => validateSearchProfile(p, profiles))
  );
  const numericByProfile = $derived(profiles.map((p) => numericIssues(p)));
  const blockingCount = $derived(
    issuesByProfile.reduce((n, i) => n + i.length, 0) + numericByProfile.reduce((n, i) => n + i.length, 0)
  );
  const currentIssues = $derived(issuesByProfile[selected] ?? []);
  const currentNumeric = $derived(numericByProfile[selected] ?? []);
  const canAct = $derived(profiles.length > 0 && blockingCount === 0 && !saving && !previewing);

  /** Terms flagged by the validator for a field (and optionally one keyword group). */
  function conflicts(field: ProfileValidationIssue['field'], groupIndex?: number): string[] {
    return currentIssues
      .filter((i) => i.field === field && (groupIndex === undefined || i.groupIndex === groupIndex))
      .flatMap((i) => i.conflictingTerms ?? []);
  }
  const allConflicts = $derived(currentIssues.flatMap((i) => i.conflictingTerms ?? []));

  async function load() {
    loading = true;
    try {
      const config = await api.getConfig();
      profiles = structuredClone(config.search_profiles);
      savedSig = JSON.stringify(profiles);
      selected = 0;
      loadError = null;
    } catch (e) {
      loadError = e instanceof ApiError ? e.detail : 'Could not load configuration';
    } finally {
      loading = false;
    }
  }
  onMount(load);

  function fail(e: unknown, fallback: string) {
    actionError =
      e instanceof ApiError ? { detail: e.detail, errors: e.errors } : { detail: fallback, errors: [] };
  }

  async function runPreview() {
    if (!canAct) return;
    previewing = true;
    actionError = null;
    try {
      const body = structuredClone($state.snapshot(profiles));
      preview = await api.previewConfig({ search_profiles: body });
      previewSig = JSON.stringify(body);
    } catch (e) {
      fail(e, 'Preview failed');
    } finally {
      previewing = false;
    }
  }

  async function save() {
    if (!canAct) return;
    saving = true;
    actionError = null;
    banner = null;
    try {
      const res = await api.updateConfig({ search_profiles: structuredClone($state.snapshot(profiles)) });
      profiles = structuredClone(res.search_profiles);
      savedSig = JSON.stringify(profiles);
      selected = Math.min(selected, Math.max(0, profiles.length - 1));
      const n = res.reevaluated_jobs_count;
      banner = `Saved and hot-reloaded. ${n} stored job${n === 1 ? '' : 's'} re-evaluated.`;
      app.showToast(banner);
      app.reloadFeed();
    } catch (e) {
      fail(e, 'Save failed');
    } finally {
      saving = false;
    }
  }

  // -- list actions ---------------------------------------------------------
  function addProfile() {
    profiles = [...profiles, blankProfile(profiles)];
    selected = profiles.length - 1;
  }
  function duplicate(i: number) {
    profiles = [...profiles.slice(0, i + 1), duplicateProfile(profiles[i], profiles), ...profiles.slice(i + 1)];
    selected = i + 1;
  }
  function remove(i: number) {
    profiles = profiles.filter((_, idx) => idx !== i);
    selected = Math.min(selected, Math.max(0, profiles.length - 1));
    confirmDelete = null;
  }
  function move(i: number, delta: -1 | 1) {
    const j = i + delta;
    if (j < 0 || j >= profiles.length) return;
    const next = [...profiles];
    [next[i], next[j]] = [next[j], next[i]];
    profiles = next;
    selected = j;
  }

  // -- keyword groups -------------------------------------------------------
  function addGroup() {
    if (current) current.keyword_groups = [...current.keyword_groups, []];
  }
  function removeGroup(i: number) {
    if (current) current.keyword_groups = current.keyword_groups.filter((_, idx) => idx !== i);
  }

  // -- contextual terms -----------------------------------------------------
  function addContext() {
    const key = newContextTrigger.trim().toLowerCase();
    if (!current || !key || key in current.contextual_terms) return;
    current.contextual_terms = { ...current.contextual_terms, [key]: [] };
    newContextTrigger = '';
  }
  function removeContext(key: string) {
    if (!current) return;
    const { [key]: _gone, ...rest } = current.contextual_terms;
    current.contextual_terms = rest;
  }

  // -- affinity weights -----------------------------------------------------
  function addWeight() {
    const key = newWeightTerm.trim().toLowerCase();
    if (!current || !key || key in current.affinity_weights) return;
    current.affinity_weights = { ...current.affinity_weights, [key]: 1 };
    newWeightTerm = '';
  }
  function removeWeight(key: string) {
    if (!current) return;
    const { [key]: _gone, ...rest } = current.affinity_weights;
    current.affinity_weights = rest;
  }
</script>

<div class="h-full overflow-y-auto">
  <div class="mx-auto max-w-6xl p-5">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold">Search Profiles</h2>
        <p class="text-sm text-slate-600">
          Each profile is a track with its own keyword gates and Affinity Score weights.
        </p>
      </div>
      <div class="flex items-center gap-2">
        {#if dirty}<span class="text-xs text-amber-700">Unsaved changes</span>{/if}
        <button
          type="button"
          class="rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!canAct}
          onclick={runPreview}
        >
          {previewing ? 'Previewing…' : 'Dry-Run Preview against Stored Jobs'}
        </button>
        <button
          type="button"
          class="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!canAct || !dirty}
          onclick={save}
        >
          {saving ? 'Saving…' : 'Save & Hot-Reload Profiles'}
        </button>
      </div>
    </div>

    {#if blockingCount > 0}
      <p class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-800" role="status">
        {blockingCount} issue{blockingCount === 1 ? '' : 's'} to fix before you can preview or save.
      </p>
    {/if}
    {#if banner}
      <p class="mb-3 rounded bg-emerald-50 px-3 py-2 text-sm text-emerald-800" role="status">{banner}</p>
    {/if}
    {#if actionError}
      <div class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
        <p class="font-medium">{actionError.detail}</p>
        {#if actionError.errors.length}
          <ul class="mt-1 list-inside list-disc">
            {#each actionError.errors as err (err)}<li>{err}</li>{/each}
          </ul>
        {/if}
      </div>
    {/if}

    {#if loading}
      <p class="py-10 text-center text-sm text-slate-500">Loading profiles…</p>
    {:else if loadError}
      <p class="py-10 text-center text-sm text-red-700" role="alert">{loadError}</p>
    {:else}
      <div class="grid gap-5 lg:grid-cols-[18rem_1fr]">
        <!-- Track list -->
        <section aria-label="Profiles">
          <ul class="space-y-1.5">
            {#each profiles as p, i (i)}
              {@const bad = issuesByProfile[i].length + numericByProfile[i].length > 0}
              <li class="rounded border {i === selected ? 'border-sky-500 bg-sky-50' : 'border-slate-200'}">
                <div class="flex items-center gap-2 px-2 py-1.5">
                  <input
                    type="checkbox"
                    bind:checked={p.enabled}
                    aria-label="Enable {p.name || 'profile'}"
                    class="rounded border-slate-300"
                  />
                  <button
                    type="button"
                    class="min-w-0 flex-1 truncate text-left text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {p.enabled ? 'font-medium' : 'text-slate-400 line-through'}"
                    aria-current={i === selected ? 'true' : undefined}
                    onclick={() => (selected = i)}
                  >
                    {p.name || '(unnamed)'}
                  </button>
                  {#if bad}<span class="text-red-600" title="Has issues" aria-label="has issues">●</span>{/if}
                </div>
                {#if i === selected}
                  <div class="flex flex-wrap gap-1 border-t border-sky-100 px-2 py-1.5 text-xs">
                    <button type="button" class="rounded border border-slate-300 bg-white px-1.5 py-0.5 hover:bg-slate-50 disabled:opacity-40" disabled={i === 0} aria-label="Move up" onclick={() => move(i, -1)}>↑</button>
                    <button type="button" class="rounded border border-slate-300 bg-white px-1.5 py-0.5 hover:bg-slate-50 disabled:opacity-40" disabled={i === profiles.length - 1} aria-label="Move down" onclick={() => move(i, 1)}>↓</button>
                    <button type="button" class="rounded border border-slate-300 bg-white px-2 py-0.5 hover:bg-slate-50" onclick={() => duplicate(i)}>Duplicate</button>
                    {#if confirmDelete === i}
                      <button type="button" class="rounded bg-red-600 px-2 py-0.5 text-white" onclick={() => remove(i)}>Confirm delete</button>
                      <button type="button" class="rounded border border-slate-300 bg-white px-2 py-0.5" onclick={() => (confirmDelete = null)}>Cancel</button>
                    {:else}
                      <button type="button" class="rounded border border-red-300 bg-white px-2 py-0.5 text-red-700 hover:bg-red-50" onclick={() => (confirmDelete = i)}>Delete</button>
                    {/if}
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
          <button
            type="button"
            class="mt-2 w-full rounded border border-dashed border-slate-400 px-3 py-1.5 text-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
            onclick={addProfile}
          >
            + Add Profile
          </button>
          {#if profiles.length === 0}
            <p class="mt-3 text-sm text-slate-500">No profiles yet. Add one to start matching jobs.</p>
          {/if}
        </section>

        <!-- Editor -->
        {#if current}
          <section class="space-y-5" aria-label="Edit {current.name}">
            {#if currentIssues.length + currentNumeric.length > 0}
              <ul class="space-y-1 rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800" aria-label="Validation issues">
                {#each currentIssues as issue, i (i)}<li>{issue.message}</li>{/each}
                {#each currentNumeric as msg (msg)}<li>{msg}</li>{/each}
              </ul>
            {/if}

            <label class="block">
              <span class="text-sm font-medium">Profile name</span>
              <input
                type="text"
                bind:value={current.name}
                class="mt-1 w-full max-w-sm rounded border px-2.5 py-1.5 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 {currentIssues.some((i) => i.field === 'name') ? 'border-red-400' : 'border-slate-300'}"
              />
            </label>

            <div>
              <h3 class="text-sm font-medium">Required terms <span class="font-normal text-slate-500">(all must match)</span></h3>
              <div class="mt-1">
                <ChipInput bind:values={current.required_terms} label="Required terms" lowercase invalid={conflicts('required_terms')} placeholder="e.g. python" />
              </div>
            </div>

            <div>
              <div class="flex items-center justify-between">
                <h3 class="text-sm font-medium">Keyword groups <span class="font-normal text-slate-500">(at least one term from each group)</span></h3>
                <button type="button" class="rounded border border-slate-300 px-2 py-0.5 text-xs hover:bg-slate-50" onclick={addGroup}>+ Add group</button>
              </div>
              <div class="mt-1 space-y-2">
                {#each current.keyword_groups as _group, gi (gi)}
                  <div class="rounded border border-slate-200 bg-slate-50 p-2">
                    <div class="mb-1 flex items-center justify-between text-xs text-slate-500">
                      <span>Group {gi + 1}: any of</span>
                      <button type="button" class="text-red-700 hover:underline" onclick={() => removeGroup(gi)}>Remove group</button>
                    </div>
                    <ChipInput bind:values={current.keyword_groups[gi]} label="Keyword group {gi + 1} terms" lowercase invalid={conflicts('keyword_groups', gi)} placeholder="Add synonyms" />
                  </div>
                {/each}
                {#if current.keyword_groups.length === 0}<p class="text-xs text-slate-500">No groups.</p>{/if}
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium">Exclude terms <span class="font-normal text-slate-500">(reject if any match)</span></h3>
              <div class="mt-1">
                <ChipInput bind:values={current.exclude_terms} label="Exclude terms" lowercase invalid={allConflicts} placeholder="e.g. clearance" />
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium">Boundary terms <span class="font-normal text-slate-500">(whole-word match only)</span></h3>
              <div class="mt-1">
                <ChipInput bind:values={current.boundary_terms} label="Boundary terms" lowercase placeholder="e.g. rails" />
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium">Contextual terms <span class="font-normal text-slate-500">(trigger counts only if a context term is present)</span></h3>
              <div class="mt-1 space-y-2">
                {#each Object.keys(current.contextual_terms) as key (key)}
                  <div class="grid grid-cols-[8rem_1fr_auto] items-start gap-2">
                    <span class="pt-1.5 font-mono text-sm">{key} →</span>
                    <ChipInput bind:values={current.contextual_terms[key]} label="Context terms for {key}" lowercase placeholder="e.g. ruby" />
                    <button type="button" class="pt-1.5 text-xs text-red-700 hover:underline" aria-label="Remove trigger {key}" onclick={() => removeContext(key)}>Remove</button>
                  </div>
                {/each}
                <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); addContext(); }}>
                  <input type="text" bind:value={newContextTrigger} aria-label="New trigger term" placeholder="Trigger term" class="w-40 rounded border border-slate-300 px-2 py-1 text-sm" />
                  <button type="submit" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">+ Add trigger</button>
                </form>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-medium">Affinity weights</h3>
              <div class="mt-1 space-y-1.5">
                {#each Object.keys(current.affinity_weights) as key (key)}
                  <div class="grid grid-cols-[8rem_1fr_5rem_auto] items-center gap-2 text-sm">
                    <span class="truncate font-mono {allConflicts.includes(key) ? 'text-red-700' : ''}" title={key}>{key}</span>
                    <input type="range" min="0.1" max="10" step="0.1" bind:value={current.affinity_weights[key]} aria-label="Weight slider for {key}" />
                    <input type="number" min="0.1" max="10" step="0.1" bind:value={current.affinity_weights[key]} aria-label="Weight for {key}" class="rounded border border-slate-300 px-1.5 py-0.5" />
                    <button type="button" class="text-xs text-red-700 hover:underline" aria-label="Remove weight {key}" onclick={() => removeWeight(key)}>Remove</button>
                  </div>
                {/each}
                <form class="flex gap-2" onsubmit={(e) => { e.preventDefault(); addWeight(); }}>
                  <input type="text" bind:value={newWeightTerm} aria-label="New weighted term" placeholder="Term" class="w-40 rounded border border-slate-300 px-2 py-1 text-sm" />
                  <button type="submit" class="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50">+ Add weight</button>
                </form>
              </div>
            </div>

            <div class="flex flex-wrap gap-6">
              <label class="block text-sm">
                <span class="font-medium">Title boost multiplier</span>
                <input type="number" min="1" max="10" step="0.1" bind:value={current.title_boost_multiplier} class="mt-1 block w-28 rounded border border-slate-300 px-2 py-1" />
              </label>
              <label class="block text-sm">
                <span class="font-medium">Minimum affinity score</span>
                <input type="number" min="0" step="0.5" bind:value={current.min_affinity_score} class="mt-1 block w-28 rounded border border-slate-300 px-2 py-1" />
              </label>
            </div>
          </section>
        {/if}
      </div>

      <!-- Zero-Network Config Preview -->
      {#if preview}
        <section class="mt-8 rounded-lg border border-slate-200 p-4" aria-label="Preview results">
          <div class="flex items-center justify-between">
            <h3 class="text-sm font-semibold">Zero-Network Config Preview</h3>
            {#if previewStale}<span class="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">Outdated — profiles changed since this run</span>{/if}
          </div>
          <dl class="mt-3 flex flex-wrap gap-x-8 gap-y-2 text-sm">
            <div><dt class="text-xs text-slate-500">Evaluated</dt><dd class="text-lg font-semibold tabular-nums">{preview.total_evaluated}</dd></div>
            <div><dt class="text-xs text-slate-500">Matched</dt><dd class="text-lg font-semibold tabular-nums">{preview.total_matched}</dd></div>
            <div>
              <dt class="text-xs text-slate-500">Change vs. saved</dt>
              <dd class="text-lg font-semibold tabular-nums"><span class="text-emerald-700">+{preview.newly_matched_count}</span> / <span class="text-red-700">−{preview.dropped_match_count}</span></dd>
            </div>
          </dl>
          <ul class="mt-3 flex flex-wrap gap-2 text-xs">
            {#each Object.entries(preview.profile_counts) as [name, count] (name)}
              <li class="rounded-full border border-slate-300 px-2.5 py-0.5">{name}: <strong class="tabular-nums">{count}</strong></li>
            {/each}
          </ul>
          <h4 class="mt-4 text-xs font-semibold uppercase tracking-wide text-slate-500">Top matches</h4>
          {#if preview.sample_matches.length === 0}
            <p class="mt-1 text-sm text-slate-500">No stored jobs match these profiles.</p>
          {:else}
            <ul class="mt-1 divide-y divide-slate-100">
              {#each preview.sample_matches.slice(0, 20) as m (m.job_key)}
                <li class="py-2">
                  <div class="flex items-start justify-between gap-2">
                    <div class="min-w-0">
                      <p class="truncate text-sm font-medium">{m.title}</p>
                      <p class="truncate text-xs text-slate-600">{m.company}{m.location ? ` · ${m.location}` : ''}</p>
                    </div>
                    <span class="shrink-0 rounded bg-emerald-600 px-1.5 py-0.5 text-xs font-semibold tabular-nums text-white">{m.affinity_score.toFixed(1)}</span>
                  </div>
                  <p class="mt-1 flex flex-wrap gap-1 text-xs">
                    {#if m.primary_profile}<span class="rounded-full border border-emerald-300 px-2 text-emerald-800">{m.primary_profile}</span>{/if}
                    {#each Object.entries(m.score_breakdown) as [term, pts] (term)}
                      <span class="rounded bg-slate-100 px-1.5 font-mono">{term} +{pts}</span>
                    {/each}
                  </p>
                </li>
              {/each}
            </ul>
          {/if}
        </section>
      {/if}
    {/if}
  </div>
</div>
