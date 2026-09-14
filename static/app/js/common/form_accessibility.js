'use strict';

function linkFieldError(control, error, index) {
    if (!control || !error) return false;
    if (!error.id) error.id = `field-error-${index}`;
    control.setAttribute('aria-invalid', 'true');
    const describedBy = String(control.getAttribute('aria-describedby') || '')
        .split(/\s+/)
        .filter(Boolean);
    if (!describedBy.includes(error.id)) describedBy.push(error.id);
    control.setAttribute('aria-describedby', describedBy.join(' '));
    return true;
}

function errorControl(document, error) {
    const explicitTarget = error.getAttribute?.('data-field-error-for');
    if (explicitTarget) return document.getElementById(explicitTarget);
    const container = error.closest?.('.form-row, .form-group, [class*="col-"], fieldset, .mb-3') || error.parentElement;
    return container?.querySelector?.('input:not([type="hidden"]), select, textarea') || null;
}

function enhanceFormErrors(root, document) {
    const scope = root || document;
    const errors = Array.from(scope.querySelectorAll?.('[data-field-error], .invalid-feedback, .errorlist li, .text-danger.small') || []);
    errors.forEach((error, index) => {
        linkFieldError(errorControl(document, error), error, index + 1);
    });
    return errors.length;
}

function installFormAccessibility(document) {
    const enhance = root => enhanceFormErrors(root, document);
    document.addEventListener('DOMContentLoaded', () => {
        enhance(document);
        if (typeof MutationObserver === 'undefined' || !document.body) return;
        const observer = new MutationObserver(records => records.forEach(record => {
            Array.from(record.addedNodes || []).forEach(node => {
                if (node.nodeType === 1) enhance(node);
            });
        }));
        observer.observe(document.body, {childList: true, subtree: true});
    });
}

const api = {enhanceFormErrors, installFormAccessibility, linkFieldError};
if (typeof module !== 'undefined') module.exports = api;
if (typeof window !== 'undefined') window.AppFormAccessibility = api;
if (typeof document !== 'undefined') installFormAccessibility(document);
