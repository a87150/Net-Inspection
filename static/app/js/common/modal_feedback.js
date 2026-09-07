'use strict';

const MODAL_DRAFT_PREFIX = 'net-modal-form:';

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
        && !['password', 'file', 'submit', 'button', 'reset', 'image'].includes(type);
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
    const fields = Array.from(form.elements || []).filter(canRetainField);
    const used = new Set();

    fields.forEach(field => {
        const type = String(field.type || '').toLowerCase();
        let index = draft.findIndex((entry, candidateIndex) => (
            !used.has(candidateIndex)
            && entry.name === field.name
            && (type !== 'radio' && type !== 'checkbox' || entry.value === String(field.value ?? ''))
        ));
        if (index < 0) {
            index = draft.findIndex((entry, candidateIndex) => (
                !used.has(candidateIndex) && entry.name === field.name
            ));
        }
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
            field.value = entry.value;
        }
    });
    return true;
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
        moveFlashMessagesToModal(document, modal);

        const storage = availableStorage(document);
        const forms = Array.from(document.querySelectorAll('.modal form'));
        if (!storage) return;

        forms.forEach(form => {
            const key = formDraftKey(form);
            if (typeof form.addEventListener === 'function') {
                form.addEventListener('submit', () => {
                    try {
                        storage.setItem(key, JSON.stringify(captureFormDraft(form)));
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
                if (belongsToOpenModal && saved) restoreFormDraft(form, JSON.parse(saved));
                if (belongsToOpenModal || !modal) storage.removeItem(key);
            } catch (_error) {
                storage.removeItem(key);
            }
        });
    });
}

if (typeof module !== 'undefined') {
    module.exports = {
        captureFormDraft,
        installModalFeedback,
        moveFlashMessagesToModal,
        restoreFormDraft,
    };
}
if (typeof document !== 'undefined') installModalFeedback(document);
