// Synthetic M5-2b client. Source strings enter textContent only, never HTML.
let active = null;

export function dismissSuggestion(displayId) {
    if (!active || active.id !== displayId) return true;
    const state = active;
    active = null;
    clearTimeout(state.timer);
    for (const [target, name] of state.listeners) target.removeEventListener(name, state.track);
    state.dialog.remove();
    if (state.resolve) state.resolve({ operation: state.confirmed ? 'done' : 'close', reason_id: null, timing: null });
    return true;
}

export async function renderSuggestion(data) {
    if (active) dismissSuggestion(active.id);
    const dialog = document.createElement('dialog');
    dialog.setAttribute('aria-label', '知因合成建议');
    dialog.style.cssText = 'max-width:640px;width:90%;max-height:85vh;overflow:auto;padding:24px;border:1px solid #888;border-radius:12px;background:#fff;color:#222;';
    const add = (tag, text, parent = dialog) => {
        const node = document.createElement(tag);
        node.textContent = text;
        parent.append(node);
        return node;
    };
    add('h2', '核对原因');
    add('p', '点踩已保存。合成模型建议，未确认。原文仅供核对，未运行代码。');
    const status = add('p', '正在确认展示回执…');
    status.setAttribute('role', 'status');
    const buttons = [];
    const state = { id: data.binding.display_id, dialog, status, buttons, resolve: null, confirmed: null,
        started: performance.now(), activeMs: 0, last: null, listeners: [], timer: null, track: null };
    const choose = (operation, reasonId) => {
        if (!state.resolve || active !== state) return;
        state.track();
        const resolve = state.resolve;
        state.resolve = null;
        buttons.forEach(item => { item.node.disabled = true; });
        resolve({ operation, reason_id: reasonId, timing: { version: 'active-v1', display_id: state.id,
            session_ref: data.session_ref, active_ms: state.activeMs, elapsed_ms: performance.now() - state.started } });
    };
    const button = (label, operation, reasonId = null, parent = dialog) => {
        const node = add('button', label, parent);
        node.type = 'button';
        node.disabled = true;
        node.style.cssText = 'border:1px solid #888;border-radius:6px;padding:6px 12px;margin:6px;';
        node.onclick = () => choose(operation, reasonId);
        buttons.push({ node, operation, reasonId });
    };
    for (const card of data.cards) {
        const section = add('section', '');
        add('h3', card.label, section);
        add('p', card.template || '未定位可靠原文，仅显示类别。', section);
        for (const quote of card.quotes) {
            add('p', quote.source === 'request' ? '直接问题原文' : '目标回答原文', section);
            const pre = add('pre', quote.text, section);
            pre.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:8px;';
        }
        button('是这个问题', 'yes', card.reason_id, section);
        button('不是', 'no', card.reason_id, section);
        button('更正为这个问题', 'correct', card.reason_id, section);
    }
    button('都不是', 'none_matched');
    button('暂时跳过', 'skip');
    button('不愿说明', 'decline');
    button('常规理由菜单', 'manual');
    button('关闭', 'close');
    button('完成', 'done');
    add('p', '“都不是”仅拒绝这组建议，保留已确认原因。常规菜单中的“都不是”沿用清空人工原因的规则。');
    state.track = () => {
        const now = performance.now();
        if (state.last !== null) state.activeMs += now - state.last;
        state.last = document.hasFocus() && !document.hidden ? now : null;
    };
    for (const [target, name] of [[window, 'focus'], [window, 'blur'], [document, 'visibilitychange']]) {
        target.addEventListener(name, state.track);
        state.listeners.push([target, name]);
    }
    dialog.addEventListener('cancel', event => { event.preventDefault(); choose(state.confirmed ? 'done' : 'close', null); });
    document.body.append(dialog);
    active = state;
    dialog.showModal();
    state.track();
    state.timer = setTimeout(() => dismissSuggestion(state.id), Math.max(0, Math.min(60000, data.expires_at * 1000 - Date.now())));
    // Receipt follows DOM insertion and a rendering opportunity, never mere dispatch.
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    if (active !== state || !dialog.open || !dialog.isConnected) return { error: 'display_unavailable' };
    return { binding: data.binding };
}

export function respondSuggestion(data) {
    const state = active;
    if (!state || state.id !== data.display_id || state.resolve) return { error: 'display_unavailable' };
    state.confirmed = data.confirmed;
    state.status.textContent = data.confirmed ? '已记录你的确认。可以更正或完成。' : '请选择；没有默认确认。';
    for (const item of state.buttons) {
        item.node.hidden = data.confirmed
            ? !(item.operation === 'done' || (item.operation === 'correct' && item.reasonId !== data.confirmed))
            : ['correct', 'done'].includes(item.operation);
        item.node.disabled = item.node.hidden;
    }
    return new Promise(resolve => { state.resolve = resolve; });
}
