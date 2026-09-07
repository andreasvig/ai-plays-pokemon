<script>
  // The last DISPATCH failure, from /api/queue's `last_error`.
  //
  // ONE component for both queue surfaces (QueueBar on Home, QueuePanel) rather
  // than the same strip written twice: the two would drift, and the interesting
  // behaviour is not the markup but the dismissal rule below.
  //
  // Why it exists: `executor.drain_loop` catches every dispatch failure so one
  // poisoned item cannot freeze the serial queue. The cost was that the item
  // simply VANISHED — the caller saw its 201, the card flashed active, the queue
  // went back to idle, and the only trace was a traceback on the app's stdout.
  // The route has carried this since the queue existed and nothing read it
  // (finding #5b).
  import Icon from './Icon.svelte'
  import { queueErrorSubject } from '../lib/queue.js'
  let { error = null } = $props()
  // Dismissal is keyed on the failure's TIMESTAMP, not a boolean: the server
  // clears `last_error` the moment a run actually starts, but until then every
  // poll re-serves the same object, so a boolean would un-dismiss on the next
  // ping. A NEW failure carries a new `at` and re-shows on its own.
  let dismissedAt = $state(null)
  const show = $derived(!!error && error.at !== dismissedAt)
  const subject = $derived(queueErrorSubject(error))
</script>

{#if show}
  <div class="qerr" role="alert" data-testid="queue-last-error">
    <div class="qetext">
      <b>Last dispatch failed:</b> {error.error}
      <div class="qemeta">
        {#if subject}<span class="mono">{subject}</span>{/if}
        {#if error.at}<span class="faint"> · {error.at}</span>{/if}
      </div>
    </div>
    <button class="qex" onclick={() => dismissedAt = error.at} aria-label="Dismiss">
      <Icon name="close" size={12} />
    </button>
  </div>
{/if}

<style>
  .qerr {
    display: flex; align-items: flex-start; gap: 8px;
    border: 1px solid var(--red-rule); background: var(--red-soft);
    border-radius: var(--radius-sm); padding: 8px 10px;
    font-size: 11.5px; line-height: 1.45; color: var(--red);
  }
  .qetext { flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .qemeta { margin-top: 2px; font-size: 10.5px; }
  .qex {
    flex: none; width: 18px; height: 18px; display: grid; place-items: center;
    padding: 0; border: none; background: none; color: var(--red);
    border-radius: var(--radius-sm);
  }
  .qex:hover { background: var(--surface); }
</style>
