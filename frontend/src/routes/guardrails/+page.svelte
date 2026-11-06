<script lang="ts">
  import { onMount } from 'svelte';
  import { api, ApiError } from '$lib/api';
  import { app } from '$lib/state/app.svelte';
  import ChipInput from '$lib/components/ChipInput.svelte';
  import { PRESET_INDUSTRY_PACKS, applyIndustryPack } from '$lib/guardrail-packs';
  import type { FilteringRulesDTO } from '$lib/types';

  type ListKey = {
    [K in keyof FilteringRulesDTO]: FilteringRulesDTO[K] extends string[] ? K : never;
  }[keyof FilteringRulesDTO];

  let filtering = $state<FilteringRulesDTO | null>(null);
  let savedSig = $state('');
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let actionError = $state<{ detail: string; errors: string[] } | null>(null);
  let packNote = $state<string | null>(null);

  const dirty = $derived(filtering !== null && JSON.stringify(filtering) !== savedSig);

  /** Inline validation mirroring `FilteringRules` in app/config/models.py. */
  const issues = $derived.by(() => {
    const f = filtering;
    const out: Record<string, string> = {};
    if (!f) return out;
    const int = (v: unknown) => Number.isInteger(v);
    if (!int(f.max_job_age_days) || f.max_job_age_days < 1 || f.max_job_age_days > 730) {
      out.max_job_age_days = 'Must be a whole number from 1 to 730.';
    }
    if (f.require_remote && f.remote_synonyms.length === 0) {
      out.remote_synonyms = 'Add at least one synonym while “Require remote” is on.';
    }
    if (!int(f.max_years_experience) || f.max_years_experience < 0 || f.max_years_experience > 20) {
      out.max_years_experience = 'Must be a whole number from 0 to 20.';
    }
    if (!int(f.hard_block_years) || f.hard_block_years < 0 || f.hard_block_years > 25) {
      out.hard_block_years = 'Must be a whole number from 0 to 25.';
    } else if (f.hard_block_years < f.max_years_experience) {
      out.hard_block_years = 'Must be at least the maximum years of experience.';
    }
    return out;
  });
  const issueCount = $derived(Object.keys(issues).length);

  onMount(async () => {
    try {
      filtering = structuredClone((await api.getConfig()).filtering);
      savedSig = JSON.stringify(filtering);
    } catch (e) {
      loadError = e instanceof ApiError ? e.detail : 'Could not load configuration';
    }
  });

  function applyPack(id: string) {
    if (!filtering) return;
    const pack = PRESET_INDUSTRY_PACKS.find((p) => p.id === id);
    if (!pack) return;
    const before = filtering.blocked_domains.length + filtering.blocked_companies.length;
    const next = applyIndustryPack($state.snapshot(filtering), id);
    const added = next.blocked_domains.length + next.blocked_companies.length - before;
    const enabledNow = !next.enable_domain_filter;
    next.enable_domain_filter = true;
    filtering = next;
    packNote =
      (added === 0 ? `“${pack.label}” was already fully applied.` : `Added ${added} term${added === 1 ? '' : 's'} from “${pack.label}”.`) +
      (enabledNow ? ' Domain filter was off, so it is now enabled.' : '');
  }

  async function save() {
    if (!filtering || issueCount > 0 || saving) return;
    saving = true;
    actionError = null;
    try {
      const res = await api.updateConfig({ filtering: structuredClone($state.snapshot(filtering)) });
      filtering = structuredClone(res.filtering);
      savedSig = JSON.stringify(filtering);
      const n = res.reevaluated_jobs_count;
      app.showToast(`Guardrails saved and hot-reloaded${n ? ` (${n} jobs re-evaluated)` : ''}.`);
    } catch (e) {
      actionError =
        e instanceof ApiError ? { detail: e.detail, errors: e.errors } : { detail: 'Save failed', errors: [] };
    } finally {
      saving = false;
    }
  }
</script>

{#snippet chipField(f: FilteringRulesDTO, key: ListKey, label: string, hint?: string, issue?: string)}
  <div>
    <label for="chips-{key}" class="text-sm font-medium">{label}{#if hint} <span class="font-normal text-slate-500">{hint}</span>{/if}</label>
    <div class="mt-1">
      <ChipInput id="chips-{key}" bind:values={f[key]} {label} lowercase />
    </div>
    {#if issue}<p class="mt-1 text-xs text-red-700">{issue}</p>{/if}
  </div>
{/snippet}

<div class="h-full overflow-y-auto">
  <div class="mx-auto max-w-4xl p-5">
    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold">Pre-Persistence Guardrails</h2>
        <p class="text-sm text-slate-600">Jobs rejected here are never stored, matched or alerted.</p>
      </div>
      <div class="flex items-center gap-2">
        {#if dirty}<span class="text-xs text-amber-700">Unsaved changes</span>{/if}
        <button
          type="button"
          class="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!dirty || issueCount > 0 || saving}
          onclick={save}
        >
          {saving ? 'Saving…' : 'Save Guardrails'}
        </button>
      </div>
    </div>

    {#if actionError}
      <div class="mb-3 rounded bg-red-50 px-3 py-2 text-sm text-red-800" role="alert">
        <p class="font-medium">{actionError.detail}</p>
        {#if actionError.errors.length}
          <ul class="mt-1 list-inside list-disc">{#each actionError.errors as err (err)}<li>{err}</li>{/each}</ul>
        {/if}
      </div>
    {/if}

    {#if loadError}
      <p class="py-10 text-center text-sm text-red-700" role="alert">{loadError}</p>
    {:else if !filtering}
      <p class="py-10 text-center text-sm text-slate-500">Loading guardrails…</p>
    {:else}
      <section class="mb-5 rounded-lg border border-slate-200 p-4" aria-label="Preset Industry Block Packs">
        <h3 class="text-sm font-semibold">Preset Industry Block Packs</h3>
        <p class="text-xs text-slate-500">One click merges curated terms into the blocked lists, skipping duplicates.</p>
        <div class="mt-2 flex flex-wrap gap-2">
          {#each PRESET_INDUSTRY_PACKS as pack (pack.id)}
            <button
              type="button"
              class="rounded-full border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
              title={pack.description}
              onclick={() => applyPack(pack.id)}
            >
              {pack.label}
            </button>
          {/each}
        </div>
        {#if packNote}<p class="mt-2 text-sm text-emerald-800" role="status">{packNote}</p>{/if}
      </section>

      <div class="space-y-3">
        <details class="rounded-lg border border-slate-200" open>
          <summary class="cursor-pointer px-4 py-3 text-sm font-semibold">Age filter</summary>
          <div class="space-y-3 border-t border-slate-100 p-4">
            <label class="block text-sm">
              <span class="font-medium">Maximum job age (days)</span>
              <input type="number" min="1" max="730" bind:value={filtering.max_job_age_days} class="mt-1 block w-28 rounded border px-2 py-1 {issues.max_job_age_days ? 'border-red-400' : 'border-slate-300'}" />
              {#if issues.max_job_age_days}<span class="text-xs text-red-700">{issues.max_job_age_days}</span>{/if}
            </label>
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.use_updated_at_fallback} class="rounded" /> Use updated date when posted date is missing</label>
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.treat_recent_update_as_fresh} class="rounded" /> Treat recently updated jobs as fresh</label>
          </div>
        </details>

        <details class="rounded-lg border border-slate-200" open>
          <summary class="cursor-pointer px-4 py-3 text-sm font-semibold">Location filter</summary>
          <div class="space-y-3 border-t border-slate-100 p-4">
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.require_remote} class="rounded" /> Require remote</label>
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.require_remote_structured} class="rounded" /> Require structured remote signal (no description fallback)</label>
            {@render chipField(filtering, 'remote_synonyms', 'Remote synonyms', undefined, issues.remote_synonyms)}
            {@render chipField(filtering, 'acceptable_countries', 'Acceptable countries')}
            {@render chipField(filtering, 'remote_allowed_regions', 'Allowed remote regions')}
            {@render chipField(filtering, 'remote_blocked_regions', 'Blocked remote regions')}
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.allow_remote_canada_if_us_allowed} class="rounded" /> Allow Canada remote if US applicants are allowed</label>
            {@render chipField(filtering, 'us_eligibility_phrases', 'US eligibility phrases')}
            {@render chipField(filtering, 'allow_hybrid_cities', 'Hybrid-friendly cities')}
            {@render chipField(filtering, 'remote_description_keywords', 'Remote description keywords', '(only used without structured signals)')}
          </div>
        </details>

        <details class="rounded-lg border border-slate-200" open>
          <summary class="cursor-pointer px-4 py-3 text-sm font-semibold">Role filter</summary>
          <div class="space-y-3 border-t border-slate-100 p-4">
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.enable_seniority_filter} class="rounded" /> Enable seniority filter</label>
            {@render chipField(filtering, 'seniority_block_terms', 'Seniority block terms')}
            {@render chipField(filtering, 'seniority_allow_terms', 'Seniority allow terms', '(override blocking)')}
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.enable_experience_filter} class="rounded" /> Enable experience filter</label>
            <div class="flex flex-wrap gap-6">
              <label class="block text-sm">
                <span class="font-medium">Max years of experience</span>
                <input type="number" min="0" max="20" bind:value={filtering.max_years_experience} class="mt-1 block w-28 rounded border px-2 py-1 {issues.max_years_experience ? 'border-red-400' : 'border-slate-300'}" />
                {#if issues.max_years_experience}<span class="text-xs text-red-700">{issues.max_years_experience}</span>{/if}
              </label>
              <label class="block text-sm">
                <span class="font-medium">Hard block at (years)</span>
                <input type="number" min="0" max="25" bind:value={filtering.hard_block_years} class="mt-1 block w-28 rounded border px-2 py-1 {issues.hard_block_years ? 'border-red-400' : 'border-slate-300'}" />
                {#if issues.hard_block_years}<span class="block max-w-[14rem] text-xs text-red-700">{issues.hard_block_years}</span>{/if}
              </label>
            </div>
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.enable_role_filter} class="rounded" /> Enable role filter</label>
            {@render chipField(filtering, 'role_allow_terms', 'Role allow terms')}
            {@render chipField(filtering, 'role_block_terms', 'Role block terms')}
          </div>
        </details>

        <details class="rounded-lg border border-slate-200" open>
          <summary class="cursor-pointer px-4 py-3 text-sm font-semibold">Domain filter</summary>
          <div class="space-y-3 border-t border-slate-100 p-4">
            <label class="flex items-center gap-2 text-sm"><input type="checkbox" bind:checked={filtering.enable_domain_filter} class="rounded" /> Enable domain filter</label>
            {@render chipField(filtering, 'blocked_companies', 'Blocked companies')}
            {@render chipField(filtering, 'blocked_domains', 'Blocked domains / industries')}
            {@render chipField(filtering, 'domain_allow_terms', 'Domain allow terms', '(override blocked domains)')}
          </div>
        </details>
      </div>
    {/if}
  </div>
</div>
