document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-local-filter]').forEach((input) => {
        const selector = input.dataset.localFilter;
        input.addEventListener('input', () => {
            const keyword = input.value.trim().toLocaleLowerCase();
            document.querySelectorAll(selector).forEach((row) => {
                row.hidden = Boolean(keyword) && !row.textContent.toLocaleLowerCase().includes(keyword);
            });
        });
    });

    document.querySelectorAll('table[data-local-sort]').forEach((table) => {
        table.querySelectorAll('thead th').forEach((header, columnIndex) => {
            if (header.dataset.noSort !== undefined) return;
            header.classList.add('sortable-column');
            header.title = '点击排序';
            header.tabIndex = 0;
            const sort = () => {
                const body = table.tBodies[0];
                if (!body) return;
                const direction = header.dataset.direction === 'asc' ? 'desc' : 'asc';
                table.querySelectorAll('thead th').forEach((item) => delete item.dataset.direction);
                header.dataset.direction = direction;
                const rows = [...body.rows].filter((row) => !row.querySelector('[colspan]'));
                rows.sort((left, right) => {
                    const a = left.cells[columnIndex]?.textContent.trim() || '';
                    const b = right.cells[columnIndex]?.textContent.trim() || '';
                    const aNumber = Number(a.replace(/[% ,]/g, ''));
                    const bNumber = Number(b.replace(/[% ,]/g, ''));
                    const comparison = Number.isNaN(aNumber) || Number.isNaN(bNumber)
                        ? a.localeCompare(b, 'zh-CN', {numeric: true})
                        : aNumber - bNumber;
                    return direction === 'asc' ? comparison : -comparison;
                });
                rows.forEach((row) => body.appendChild(row));
            };
            header.addEventListener('click', sort);
            header.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') sort();
            });
        });
    });
});
