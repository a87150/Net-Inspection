const fs = require('node:fs');
const path = require('node:path');

const BASE = path.resolve(__dirname, '../..');
const APP_TEMPLATE_ROOTS = [
    'index', 'people', 'domain', 'alerts', 'topology', 'ipam',
    'pc', 'server', 'weakcurrent',
];

function templatePath(relative) {
    for (const app of APP_TEMPLATE_ROOTS) {
        const candidate = path.join(BASE, app, 'templates', relative);
        if (fs.existsSync(candidate)) return candidate;
    }
    throw new Error('找不到模板：' + relative);
}

module.exports = {templatePath};
