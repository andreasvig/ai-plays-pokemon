<script>
  import Self from './JsonTree.svelte'
  let { data, k = null, depth = 0 } = $props()
  const isObj = (v) => v !== null && typeof v === 'object'
  const entries = $derived(isObj(data) ? (Array.isArray(data) ? data.map((v, i) => [i, v]) : Object.entries(data)) : [])
  const isArr = $derived(Array.isArray(data))
</script>

<div class="row" style={`padding-left:${depth ? 12 : 0}px`}>
  {#if isObj(data)}
    {#if k !== null}<span class="key">{k}</span><span class="punc">: {isArr ? '[' : '{'}</span>{/if}
    <div class="children">
      {#each entries as [ck, cv]}
        <Self data={cv} k={isArr ? null : ck} depth={depth + 1} />
      {/each}
    </div>
    {#if k !== null}<span class="punc">{isArr ? ']' : '}'}</span>{/if}
  {:else}
    {#if k !== null}<span class="key">{k}</span><span class="punc">: </span>{/if}
    {#if typeof data === 'string'}<span class="str">"{data}"</span>
    {:else if typeof data === 'number'}<span class="num">{data}</span>
    {:else if typeof data === 'boolean'}<span class="bool">{data}</span>
    {:else}<span class="null">null</span>{/if}
  {/if}
</div>

<style>
  .row { font-family: var(--mono); font-size: 11.5px; line-height: 1.7; }
  .children { display: flex; flex-direction: column; }
  .key { color: #0e7490; }
  .punc { color: var(--faint); }
  .str { color: #15803d; }
  .num { color: #b45309; }
  .bool { color: #7c3aed; }
  .null { color: var(--faint); }
</style>
