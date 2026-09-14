'use strict';

const MODAL_DRAFT_PREFIX = 'net-modal-form:';
const DRAFT_IDENTITY_FIELDS = new Set([
    'profile_id', 'channel_id', 'source_id', 'policy_id', 'batch_id',
    'provider', 'scope', 'channel_type', 'device_type',
]);

function isDraftIdentity(field) {
    return DRAFT_IDENTITY_FIELDS.has(field.name)
        && (String(field.type).toLowerCase() === 'hidden' || field.name.endsWith('_id'));
}

function moveFlashMessagesToModal(document, modal) {
    if (!modal) return false;
    const body = modal.querySelector('.modal-body');
    const messages = Array.from(document.querySelectorAll('[data-flash-message]'));
    if (!body || !messages.length) return false;

    let stack = body.querySelector('[data-modal-feedback]');
    if (!stack) {
        stack = document.createElement('div');
        stack.className = 'modal-feedback-stack';
        stack.setAttribute('data-modal-feedback', '');
        stack.setAttribute('role', 'region');
        stack.setAttribute('aria-live', 'polite');
        body.prepend(stack);
    }
    messages.forEach(message => stack.append(message));
    return true;
}

function canRetainField(field) {
    const type = String(field.type || '').toLowerCase();
    return Boolean(field.name)
        && !field.disabled
        && field.name !== 'csrfmiddlewaretoken'
        && !isDraftIdentity(field)
        // IDs and routing tokens belong to the freshly rendered server form.
        // A create draft may contain an empty ID even after saving succeeds.
        && !['hidden', 'password', 'file', 'submit', 'button', 'reset', 'image'].includes(type);
}

function captureFormDraft(form) {
    return Array.from(form.elements || [])
        .filter(canRetainField)
        .map(field => {
            const type = String(field.type || '').toLowerCase();
            const entry = {
                name: field.name,
                type,
                value: String(field.value ?? ''),
                checked: Boolean(field.checked),
            };
            if (type === 'select-multiple') {
                entry.values = Array.from(field.options || [])
                    .filter(option => option.selected)
                    .map(option => String(option.value));
            }
            return entry;
        });
}

function restoreFormDraft(form, draft) {
    if (!Array.isArray(draft)) return false;
    const used = new Set();
    const restore = field => {
        const type = String(field.type || '').toLowerCase();
        const index = draft.findIndex((entry, candidateIndex) => (
            !used.has(candidateIndex)
            && entry && entry.type === type
            && entry.name === field.name
            && (type !== 'radio' && type !== 'checkbox' || entry.value === String(field.value ?? ''))
        ));
        if (index < 0) return;

        const entry = draft[index];
        used.add(index);
        if (type === 'radio' || type === 'checkbox') {
            field.checked = Boolean(entry.checked);
        } else if (type === 'select-multiple') {
            const selected = new Set(entry.values || []);
            Array.from(field.options || []).forEach(option => {
                option.selected = selected.has(String(option.value));
            });
        } else {
            if (type === 'select-one' && field.options
                && !Array.from(field.options).some(option => String(option.value) === entry.value)) return;
            field.value = entry.value;
        }
    };
    const signal = () => {
        const EventClass = form.ownerDocument?.defaultView?.Event;
        if (EventClass && form.dispatchEvent) form.dispatchEvent(new EventClass('modal-draft-restored'));
    };
    // Restore controlling selections first, then enable their dependent fields.
    // Never dispatch change: profile selectors can navigate away on change.
    let restoredCount;
    do {
        restoredCount = used.size;
        Array.from(form.elements || []).filter(canRetainField)
            .filter(field => String(field.type).toLowerCase().startsWith('select-')).forEach(restore);
        signal();
        // A protocol change can enable another controlling select (SMB auth).
        // Each entry is consumed once, so chained controls always terminate.
    } while (used.size > restoredCount);
    Array.from(form.elements || []).filter(canRetainField)
        .filter(field => !String(field.type).toLowerCase().startsWith('select-')).forEach(restore);
    signal();
    return true;
}

function showModalFeedback(document, modal, text, kind = 'danger') {
    const body = modal?.querySelector('.modal-body');
    if (!body) return null;
    body.querySelector('[data-modal-feedback]')?.remove?.();
    const message = document.createElement('div');
    message.className = `alert alert-${kind}`;
    message.setAttribute('data-modal-feedback', '');
    message.setAttribute('role', 'status');
    message.textContent = text;
    body.prepend(message);
    return message;
}

function formDraftScope(form) {
    const identities = Array.from(form.elements || [])
        .filter(isDraftIdentity)
        .map(field => [field.name, String(field.value ?? '')]);
    return JSON.stringify([
        form.ownerDocument?.location?.pathname || '',
        form.getAttribute?.('action') || '',
        identities,
    ]);
}

function formDraftKey(form) {
    const modal = typeof form.closest === 'function' ? form.closest('.modal') : null;
    const action = typeof form.getAttribute === 'function' ? form.getAttribute('action') : '';
    return MODAL_DRAFT_PREFIX + [modal?.id || 'modal', form.id || action || 'form'].join(':');
}

function availableStorage(document) {
    try {
        return document.defaultView?.sessionStorage || null;
    } catch (_error) {
        return null;
    }
}

function installModalFeedback(document) {
    document.addEventListener('DOMContentLoaded', () => {
        const modal = document.querySelector('.modal[data-auto-open="true"]');
        const savedSuccessfully = Array.from(document.querySelectorAll('[data-flash-message]'))
            .some(message => message.classList?.contains('alert-success'));
        moveFlashMessagesToModal(document, modal);

        const storage = availableStorage(document);
        const forms = Array.from(document.querySelectorAll('.modal form'));
        if (!storage) return;

        forms.forEach(form => {
            const key = formDraftKey(form);
            const scope = formDraftScope(form);
            if (typeof form.addEventListener === 'function') {
                form.addEventListener('submit', () => {
                    // In-dialog transport keeps drafts in memory; do not leave
                    // stale pre-save identities for a later full-page visit.
                    if (document.defaultView?.AppModalTransport) return;
                    try {
                        storage.setItem(key, JSON.stringify({version: 2, scope, fields: captureFormDraft(form)}));
                    } catch (_error) {
                        // Form submission must never depend on browser storage.
                    }
                });
            }

            const belongsToOpenModal = modal && (
                typeof modal.contains === 'function' ? modal.contains(form) : form.closest?.('.modal') === modal
            );
            try {
                const saved = storage.getItem(key);
                if (belongsToOpenModal && saved && !savedSuccessfully) {
                    const draft = JSON.parse(saved);
                    if (draft?.version === 2 && draft.scope === scope) restoreFormDraft(form, draft.fields);
                }
                if (belongsToOpenModal || !modal) storage.removeItem(key);
            } catch (_error) {
                storage.removeItem(key);
            }
        });
    });
}

const modalFeedbackApi = {
    captureFormDraft,
    installModalFeedback,
    moveFlashMessagesToModal,
    restoreFormDraft,
    showModalFeedback,
};
if (typeof module !== 'undefined') module.exports = modalFeedbackApi;
if (typeof window !== 'undefined') window.AppModalFeedback = modalFeedbackApi;
if (typeof document !== 'undefined') installModalFeedback(document);
