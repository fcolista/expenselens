<script lang="ts">
  export let duplicateLabel = 'Duplicate';
  export let deleteLabel = 'Delete';
  export let onDuplicate: () => void;
  export let onDelete: () => void;
  export let disabled = false;

  const ACTION_WIDTH = 144;
  let offset = 0;
  let startX = 0;
  let startY = 0;
  let dragging = false;
  let moved = false;
  let locked: 'x' | 'y' | null = null;
  let base = 0;

  function down(event: PointerEvent) {
    if (disabled || event.pointerType === 'mouse') return;
    startX = event.clientX;
    startY = event.clientY;
    base = offset;
    dragging = true;
    moved = false;
    locked = null;
  }

  function move(event: PointerEvent) {
    if (!dragging) return;
    const dx = event.clientX - startX;
    const dy = event.clientY - startY;
    if (!locked) {
      if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
      locked = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      if (locked === 'x') (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    }
    if (locked !== 'x') return;
    moved = true;
    offset = Math.max(-ACTION_WIDTH - 24, Math.min(0, base + dx));
  }

  function up() {
    if (!dragging) return;
    dragging = false;
    if (locked === 'x') offset = offset < -ACTION_WIDTH / 2 ? -ACTION_WIDTH : 0;
    locked = null;
  }

  function swallowClick(event: MouseEvent) {
    if (moved || offset !== 0) {
      event.preventDefault();
      event.stopPropagation();
      if (!moved) offset = 0;
      moved = false;
    }
  }

  function act(action: () => void) {
    offset = 0;
    action();
  }
</script>

<div class="swipe">
  <div class="actions" style={`width:${ACTION_WIDTH}px`} aria-hidden={offset === 0}>
    <button type="button" class="dup" tabindex={offset === 0 ? -1 : 0} on:click={() => act(onDuplicate)}>{duplicateLabel}</button>
    <button type="button" class="del" tabindex={offset === 0 ? -1 : 0} on:click={() => act(onDelete)}>{deleteLabel}</button>
  </div>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="slide" class:animate={!dragging} style={`transform:translateX(${offset}px)`}
    on:pointerdown={down} on:pointermove={move} on:pointerup={up} on:pointercancel={up}
    on:click|capture={swallowClick}
  >
    <slot />
  </div>
</div>

<style>
  .swipe { position: relative; overflow: hidden; }
  .slide { position: relative; background: var(--card); touch-action: pan-y; }
  .slide.animate { transition: transform .22s ease; }
  .actions { position: absolute; top: 0; right: 0; bottom: 0; display: flex; }
  .actions button {
    flex: 1; border: 0; color: #fff; font: inherit; font-weight: 600; font-size: 15px; cursor: pointer;
  }
  .dup { background: var(--accent); }
  .del { background: var(--danger); }
  @media (prefers-reduced-motion: reduce) { .slide.animate { transition: none; } }
</style>
