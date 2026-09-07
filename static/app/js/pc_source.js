document.querySelectorAll('[data-pc-source-form]').forEach((form) => {
  const kind = form.querySelector('[name="source_type"]');
  const timeMode = form.querySelector('[name="file_time_mode"]');
  const update = () => {
    const smb = kind.value === 'smb';
    for (const name of ['domain', 'share_name']) {
      const group = form.querySelector('[data-source-field="' + name + '"]');
      group.hidden = !smb;
      group.querySelectorAll('input').forEach((input) => { input.disabled = !smb; });
    }
    for (const name of ['ftp_passive', 'ftp_use_tls']) {
      form.querySelector('[data-source-field="' + name + '"]').hidden = smb;
    }
    const recent = timeMode.value === 'recent_days';
    form.querySelector('[data-source-field="recent_days"]').hidden = !recent;
    for (const name of ['range_start_date', 'range_end_date']) {
      form.querySelector('[data-source-field="' + name + '"]').hidden = recent;
    }
  };
  kind.addEventListener('change', () => {
    const port = form.querySelector('[name="port"]');
    if (['21', '445', ''].includes(port.value)) port.value = kind.value === 'smb' ? '445' : '21';
    update();
  });
  timeMode.addEventListener('change', update);
  update();
});
document.querySelectorAll('[data-pc-profile-switch]').forEach((select) => {
  select.addEventListener('change', () => {
    const url = new URL(window.location.href);
    url.searchParams.set('task_profile', select.value);
    url.searchParams.set('task_modal', 'profile');
    window.location.assign(url.toString());
  });
});
