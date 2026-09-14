function createPeopleTaskController({document, window, fetchImpl = fetch, pollIntervalMs = 3000}) {
    const root = document.querySelector('[data-people-task-notifications]');
    if (!root) return {start() {}, stop() {}, async poll() {}};
    const runningList = root.querySelector('[data-people-running-list]');
    const modalElement = root.querySelector('#peopleTaskCompletionModal');
    const messageElement = root.querySelector('[data-people-completion-message]');
    const jumpElement = root.querySelector('[data-people-completion-jump]');
    const closeElements = root.querySelectorAll
        ? Array.from(root.querySelectorAll('[data-people-completion-close]'))
        : [root.querySelector('[data-people-completion-close]')].filter(Boolean);
    const shownTerminal = new Set();
    let timer = null;
    let currentTerminal = null;

    async function postAcknowledgement(task, kind) {
        const response = await fetchImpl(task.ack_url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                'X-CSRFToken': root.dataset.csrfToken,
            },
            body: `kind=${encodeURIComponent(kind)}`,
        });
        if (!response.ok) throw new Error('acknowledgement failed');
    }

    async function dismissRunning(task) {
        await postAcknowledgement(task, 'running');
        await poll();
    }

    async function acknowledgeTerminal(task = currentTerminal) {
        if (!task) return;
        await postAcknowledgement(task, 'terminal');
        currentTerminal = null;
    }

    function renderRunning(tasks) {
        const notices = tasks.filter(task => task.show_running).map(task => {
            const alert = document.createElement('div');
            alert.className = 'alert alert-info d-flex flex-wrap align-items-center justify-content-between gap-2';
            alert.textContent = task.message;
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'btn btn-sm btn-outline-primary';
            button.textContent = '知道了，本次不再显示';
            button.addEventListener('click', () => dismissRunning(task));
            alert.append(button);
            return alert;
        });
        runningList.replaceChildren(...notices);
    }

    function showTerminal(tasks) {
        const task = tasks.find(item => item.show_terminal && !shownTerminal.has(item.id));
        if (!task) return;
        shownTerminal.add(task.id);
        currentTerminal = task;
        messageElement.textContent = task.message;
        jumpElement.href = task.jump_url;
        const workflow = document.querySelector('#importModal.show, #peoplePreviewModal.show');
        if (workflow) {
            const notice = document.createElement('div');
            notice.className = 'alert alert-info';
            notice.textContent = task.message + ' ';
            const link = document.createElement('a');
            link.href = task.jump_url;
            link.textContent = '查看结果 / 继续下一步';
            link.addEventListener('click', () => acknowledgeTerminal(task));
            notice.append(link);
            workflow.querySelector('.modal-body').prepend(notice);
            return;
        }
        window.bootstrap?.Modal.getOrCreateInstance(modalElement).show();
    }

    async function poll() {
        if (timer !== null) {
            window.clearTimeout(timer);
            timer = null;
        }
        try {
            const response = await fetchImpl(root.dataset.statusUrl, {
                credentials: 'same-origin', headers: {'Accept': 'application/json'},
            });
            if (!response.ok) throw new Error('status failed');
            const payload = await response.json();
            const tasks = Array.isArray(payload.tasks) ? payload.tasks : [];
            renderRunning(tasks);
            showTerminal(tasks);
            if (tasks.some(task => task.status === 'queued' || task.status === 'running')) {
                timer = window.setTimeout(poll, pollIntervalMs);
            }
        } catch (_error) {
            timer = null;
        }
    }

    function start() { poll(); }
    function stop() {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
    }

    closeElements.forEach(element => {
        element.addEventListener('click', () => acknowledgeTerminal());
    });
    async function followTerminalResult(event) {
        event.preventDefault();
        const task = currentTerminal;
        if (!task) return;
        const workflow = document.querySelector('#importModal.show, #peoplePreviewModal.show');
        try {
            await acknowledgeTerminal(task);
        } catch (_error) {
            messageElement.textContent = '结果确认失败，请重试后再查看。';
            return;
        }
        if (!workflow) window.location.assign(task.jump_url);
    }
    jumpElement?.addEventListener('click', followTerminalResult);
    return {start, stop, poll, dismissRunning, acknowledgeTerminal};
}

if (typeof module !== 'undefined') module.exports = {createPeopleTaskController};

if (typeof document !== 'undefined') {
    document.addEventListener('DOMContentLoaded', () => {
        const controller = createPeopleTaskController({document, window});
        controller.start();
        document.addEventListener('people:operation-submitted', () => controller.poll());
    });
}
