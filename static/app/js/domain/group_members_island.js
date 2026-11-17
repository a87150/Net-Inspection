/** Vue island for the group member modal: selection state only, table stays server-rendered.
 *
 * Loaded both on the standalone member page and on the group list, because the modal is
 * fetched and injected there. Mounting is idempotent so injected markup gets picked up on
 * every app:modal-updated without double-binding the previous DOM.
 */
import { createApp } from '../../../vendor/vue/vue.esm-browser.prod.js';
import { nextSelection, submitDisabled, summaryLabel } from './member_selection.mjs';

function mountIsland(root) {
  if (!root || root.__vue_app__) return;

  const candidates = () => Array.from(root.querySelectorAll('input[data-member-target]:not(:disabled)'));

  createApp({
    delimiters: ['[[', ']]'],
    data() {
      return {
        // server-rendered checked state survives a failed POST; read it before Vue owns the inputs
        selected: Array.from(root.querySelectorAll('input[data-member-target]:checked'))
          .map(input => input.value),
        total: Number(root.dataset.candidateCount || 0),
        locked: root.dataset.memberLocked === 'true',
      };
    },
    computed: {
      summary() {
        return summaryLabel(this.selected.length, this.total);
      },
      submitDisabled() {
        return submitDisabled(this.selected.length, this.locked);
      },
    },
    methods: {
      select(action) {
        this.selected = nextSelection(this.selected, candidates().map(input => input.value), action);
      },
    },
  }).mount(root);
}

function scan(scope) {
  (scope || document).querySelectorAll('#domain-members-island').forEach(mountIsland);
}

scan();
document.addEventListener('app:modal-updated', event => scan(event.detail?.modal || document));
