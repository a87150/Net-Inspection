(function () {
  'use strict';
  const modal = document.getElementById('domainBitLockerModal');
  if (!modal) return;
  const form = modal.querySelector('[data-bitlocker-form]');
  const status = modal.querySelector('[data-bitlocker-status]');
  const results = modal.querySelector('[data-bitlocker-results]');
  const submit = modal.querySelector('[data-bitlocker-submit]');
  let url = '';
  let generation = 0;
  let controller;
  function clear() {
    generation += 1;
    if (controller) controller.abort();
    results.replaceChildren();
    status.textContent = '';
    submit.disabled = false;
  }
  modal.addEventListener('show.bs.modal', function (event) {
    clear();
    const trigger = event.relatedTarget;
    url = trigger ? trigger.dataset.bitlockerUrl : '';
    modal.querySelector('[data-bitlocker-computer]').textContent = trigger ? trigger.dataset.computerName : '';
  });
  modal.addEventListener('hide.bs.modal', function () { clear(); url = ''; });
  window.addEventListener('pagehide', clear);
  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    if (!url || submit.disabled) return;
    clear();
    const current = generation;
    controller = new AbortController();
    submit.disabled = true;
    status.textContent = '正在读取域控恢复信息…';
    try {
      const response = await fetch(url, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        cache: 'no-store', signal: controller.signal,
        headers: { 'Accept': 'application/json' }
      });
      if (current !== generation) return;
      if (response.redirected || response.status === 403) throw new Error('登录已失效或无管理员权限，请重新登录。');
      const data = await response.json();
      if (current !== generation) return;
      if (!response.ok) throw new Error(data.message || '查询失败，请稍后重试。');
      status.textContent = data.message;
      for (const row of data.records) {
        const card = document.createElement('section');
        card.className = 'border rounded p-3 mb-2';
        for (const value of [
          '密钥 ID：' + (row.key_id || '不可读取'),
          '备份时间：' + (row.created_at || '未知'),
          '恢复密码：' + (row.password || '当前账号不可读取，或记录格式不完整')
        ]) {
          const line = document.createElement('p');
          line.className = 'text-break mb-1';
          line.textContent = value;
          card.appendChild(line);
        }
        results.appendChild(card);
      }
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') status.textContent = error.message || '查询失败，请稍后重试。';
    } finally {
      if (current === generation) submit.disabled = false;
    }
  });
}());
