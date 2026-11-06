<script lang="ts">
  import '../app.css';
  import { page } from '$app/state';
  import ScanControlBar from '$lib/components/ScanControlBar.svelte';
  import DismissalsDrawer from '$lib/components/DismissalsDrawer.svelte';
  import UndoToast from '$lib/components/UndoToast.svelte';
  import { app } from '$lib/state/app.svelte';

  let { children } = $props();

  const TABS = [
    { href: '/', label: 'Triage' },
    { href: '/profiles', label: 'Search Profiles' },
    { href: '/guardrails', label: 'Guardrails' },
    { href: '/sources', label: 'Sources' }
  ];

  const isActive = (href: string) =>
    href === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(href);
</script>

<div class="flex h-screen flex-col bg-white text-slate-900">
  <header class="border-b border-slate-200">
    <div class="flex items-center gap-6 px-4">
      <h1 class="py-3 text-sm font-semibold tracking-tight">Job Opportunity Scanner</h1>
      <nav aria-label="Workspaces" class="flex gap-1">
        {#each TABS as tab (tab.href)}
          <a
            href={tab.href}
            class="border-b-2 px-3 py-3 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sky-400 {isActive(tab.href)
              ? 'border-sky-600 font-medium text-sky-700'
              : 'border-transparent text-slate-600 hover:text-slate-900'}"
            aria-current={isActive(tab.href) ? 'page' : undefined}
          >
            {tab.label}
          </a>
        {/each}
      </nav>
      <button
        type="button"
        class="ml-auto rounded border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-400"
        aria-haspopup="dialog"
        onclick={() => (app.dismissalsDrawerOpen = true)}
      >
        Dismissals
      </button>
    </div>
    <ScanControlBar />
  </header>

  <main class="min-h-0 flex-1">
    {@render children()}
  </main>
</div>

<DismissalsDrawer
  open={app.dismissalsDrawerOpen}
  onclose={() => (app.dismissalsDrawerOpen = false)}
  onrestored={() => app.reloadFeed()}
/>

{#if app.toast}
  {#key app.toast.id}
    <UndoToast message={app.toast.message} onundo={app.toast.onundo} onclose={() => app.dismissToast()} />
  {/key}
{/if}
