<script>
  // Contact (Andreas 2026-09-16). Two asks sit under the links and are the
  // reason the page exists: someone to help with the token bill, and models
  // worth putting on the board.
  //
  // Every link is in LINKS — one list, so adding a channel is one entry and the
  // page has no hand-placed anchors to fall out of sync.
  import { LINKS, CONTACT_EMAIL } from '../lib/contact.js'
</script>

<section class="contact">
  <h2>Contact</h2>
  <p class="intro faint">PokeBench is built and run by Andreas Vig Astrup.</p>

  <ul class="links">
    {#if CONTACT_EMAIL}
      <li>
        <span class="what">Email</span>
        <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>
      </li>
    {/if}
    {#each LINKS as l (l.key)}
      <li>
        <span class="what">{l.label}</span>
        <a href={l.href} target="_blank" rel="noreferrer noopener">{l.text}</a>
      </li>
    {/each}
  </ul>

  <div class="asks">
    <article>
      <h3>Tokens are expensive</h3>
      <p>Every run on this board is paid for out of pocket, and a single full-ladder run can cost
        several dollars — a whole sweep across models runs into the hundreds. If you can help with
        that, in credits, an API key, or a provider arrangement, please get in touch. More budget
        is directly more models, more reasoning levels and more repeats per model.</p>
    </article>
    <article>
      <h3>Want a model on the board?</h3>
      <p>If there is a model you would like to see tested — yours or anyone's — reach out. Anything
        reachable through OpenRouter can be added, and it plays the same frozen config, the same
        ROM and the same starting save as every other row.</p>
    </article>
  </div>
</section>

<style>
  .contact { max-width: 680px; margin: 0 auto; padding: 48px 24px; }
  @media (max-width: 720px) { .contact { padding: 24px 12px 40px; } .links li { grid-template-columns: 78px minmax(0, 1fr); gap: 10px; } }
  .contact h2 { font-size: 26px; font-weight: 780; letter-spacing: -.02em; margin: 0 0 6px; }
  .intro { font-size: 14px; margin: 0 0 26px; }

  .links { list-style: none; margin: 0 0 32px; padding: 0; display: flex; flex-direction: column; }
  .links li { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 12px; align-items: baseline;
    padding: 9px 0; border-top: 1px solid var(--border); }
  .links li:last-child { border-bottom: 1px solid var(--border); }
  .what { font-size: 11px; font-weight: 700; letter-spacing: .05em; text-transform: uppercase; color: var(--faint); }
  .links a { font-size: 14px; color: var(--accent); text-decoration: none; word-break: break-word; }
  .links a:hover { text-decoration: underline; }

  .asks { display: flex; flex-direction: column; gap: 22px; }
  .asks h3 { font-size: 16px; font-weight: 740; margin: 0 0 6px; }
  .asks p { font-size: 14.5px; line-height: 1.65; color: var(--muted); margin: 0; }
</style>
