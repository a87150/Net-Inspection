function initSourceForm(form) {
  const kind = form.querySelector('[name="source_type"]');
  const timeMode = form.querySelector('[name="file_time_mode"]');
  const authMode = form.querySelector('[name="smb_auth_mode"]');
  if (!kind || !timeMode) return;
  const show = (name, visible) => {
    const group = form.querySelector('[data-source-field="' + name + '"]');
    if (!group) return;
    group.hidden = !visible;
    group.querySelectorAll('input, select, textarea').forEach((input) => {
      input.disabled = !visible;
    });
  };
  const update = () => {
    const smb = kind.value === 'smb';
    for (const name of ['shared_path', 'share_name', 'smb_auth_mode']) show(name, smb);
    const manual = !smb || authMode?.value === 'credentials';
    for (const name of ['username', 'password']) show(name, manual);
    show('domain', smb && manual);
    for (const name of ['host', 'ftp_directory', 'ftp_passive', 'ftp_use_tls']) show(name, !smb);
    const recent = timeMode.value === 'recent_days';
    show('recent_days', recent);
    for (const name of ['range_start_date', 'range_end_date']) show(name, !recent);
  };
  kind.addEventListener('change', () => {
    const port = form.querySelector('[name="port"]');
    if (port && ['21', '445', ''].includes(port.value)) port.value = kind.value === 'smb' ? '445' : '21';
    update();
  });
  timeMode.addEventListener('change', update);
  authMode?.addEventListener('change', update);
  form.addEventListener('modal-draft-restored', update);
  form.addEventListener('invalid', (event) => {
    const details = event.target.closest('[data-source-advanced]');
    if (details) details.open = true;
  }, true);
  update();
}
if (typeof module !== 'undefined') module.exports = {initSourceForm};
const boundSourceControls = new WeakSet();
function bindSourceForms(root) {
root.querySelectorAll('[data-pc-source-form]').forEach(form => {
  if (boundSourceControls.has(form)) return;
  boundSourceControls.add(form);
  initSourceForm(form);
});
root.querySelectorAll('[data-pc-profile-switch]').forEach((select) => {
  if (boundSourceControls.has(select)) return;
  boundSourceControls.add(select);
  select.addEventListener('change', () => {
    const url = new URL(window.location.href);
    url.searchParams.set('task_profile', select.value);
    url.searchParams.set('task_modal', 'profile');
    window.AppModalTransport?.navigate(select.closest('.modal'), url.toString());
  });
});
}
if (typeof window !== 'undefined') window.AppPCSource = {bind: bindSourceForms};
bindSourceForms(document);
