let paths = { 1: '/', 2: '/' };
let selections = { 1: null, 2: null };
let activePane = 1;
let lastTap = { pane: 0, path: '', time: 0 };

function enc(path) { return encodeURIComponent(path); }
function joinPath(base, name) { return base === '/' ? '/' + name : base.replace(/\/$/, '') + '/' + name; }

async function api(url, options) {
    const res = await fetch(url, options || {});
    if (!res.ok) {
        let msg = await res.text();
        try { msg = JSON.parse(msg).error || msg; } catch (e) {}
        throw new Error(msg || res.statusText);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.indexOf('application/json') >= 0 ? res.json() : res.text();
}

function markSelection(n) {
    const list = document.getElementById(`list${n}`);
    Array.prototype.forEach.call(list.children, row => {
        row.classList.toggle('selected', row.dataset.path === (selections[n] && selections[n].path));
    });
}

async function refreshPane(n) {
    const data = await api(`/api/list?path=${enc(paths[n])}`);
    const files = data.items || [];
    paths[n] = data.path || paths[n];
    const list = document.getElementById(`list${n}`);
    list.innerHTML = '';
    document.getElementById(`path${n}`).innerText = paths[n];

    files.forEach(f => {
        const div = document.createElement('div');
        div.dataset.path = f.path;
        div.className = `file-item ${(selections[n] && selections[n].path === f.path) ? 'selected' : ''}`;
        const icon = f.type === 'dir' ? '📁' : (f.type === 'back' ? '⬅️' : '📄');
        const size = f.type === 'file' ? ` <small>${f.size || 0}b</small>` : '';
        div.innerHTML = `<span>${icon} ${f.name}${size}</span>`;
        div.onclick = (e) => onItemClick(e, n, f);
        list.appendChild(div);
    });
}

function onItemClick(e, pane, item) {
    e.stopPropagation();
    const now = Date.now();
    const same = lastTap.pane === pane && lastTap.path === item.path && now - lastTap.time < 1200;
    setActivePane(pane);
    selections[pane] = item;
    markSelection(pane);
    lastTap = { pane, path: item.path, time: now };
    if (same) openItem(pane, item);
}

function openItem(pane, item) {
    if (item.type === 'dir' || item.type === 'back') {
        paths[pane] = item.path;
        selections[pane] = null;
        lastTap = { pane: 0, path: '', time: 0 };
        refreshPane(pane);
    } else if (item.type === 'file') {
        window.location.href = `/editor.html?path=${enc(item.path)}`;
    }
}

function setActivePane(n) {
    activePane = n;
    document.getElementById('pane1').classList.toggle('active-pane', n === 1);
    document.getElementById('pane2').classList.toggle('active-pane', n === 2);
}

async function moveItem() {
    const from = activePane;
    const to = activePane === 1 ? 2 : 1;
    const item = selections[from];
    if (!item || item.type === 'back') return;
    const dst = joinPath(paths[to], item.name);
    if (!confirm(`Переместить ${item.name} в ${paths[to]}?`)) return;
    await api(`/api/move?src=${enc(item.path)}&dst=${enc(dst)}`);
    selections[from] = null;
    await refreshAll();
}

function editSelected() {
    const item = selections[activePane];
    if (!item) return;
    openItem(activePane, item);
}

async function deleteSelected() {
    const item = selections[activePane];
    if (!item || item.type === 'back') return;
    if (!confirm(`Удалить ${item.name}?`)) return;
    await api(`/api/delete?path=${enc(item.path)}`);
    selections[activePane] = null;
    await refreshAll();
}

async function createFolder() {
    const name = prompt('Имя папки');
    if (!name) return;
    await api(`/api/mkdir?path=${enc(joinPath(paths[activePane], name))}`);
    await refreshPane(activePane);
}

async function createFile() {
    const name = prompt('Имя файла');
    if (!name) return;
    const path = joinPath(paths[activePane], name);
    await api(`/api/touch?path=${enc(path)}`);
    window.location.href = `/editor.html?path=${enc(path)}`;
}

function downloadSelected() {
    const item = selections[activePane];
    if (!item || item.type !== 'file') return;
    const a = document.createElement('a');
    a.href = `/api/read?path=${enc(item.path)}`;
    a.download = item.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
}

function chooseUpload() {
    document.getElementById('uploadInput').click();
}

async function uploadFiles(input) {
    const files = Array.prototype.slice.call(input.files || []);
    for (const file of files) {
        const dst = joinPath(paths[activePane], file.name);
        await api(`/api/save?path=${enc(dst)}`, { method: 'POST', body: file });
    }
    input.value = '';
    await refreshPane(activePane);
}

function uploadToActive() { chooseUpload(); }
async function refreshAll() { await Promise.all([refreshPane(1), refreshPane(2)]); }
window.onload = refreshAll;
