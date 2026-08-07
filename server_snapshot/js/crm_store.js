window.CRM_STORE = {
    _data: {
        cards: [],
        transactions: [],
        clients: [],
        suppliers: [],
        tags: [],
        documents: []
    },

    set(key, data) {
        if (!(key in this._data)) {
            console.warn(`CRM_STORE: unknown key "${key}"`);
            return;
        }
        this._data[key] = data;
        window.dispatchEvent(new CustomEvent('crm:state-change', { detail: { key, data } }));
    },

    get(key) {
        return this._data[key];
    },

    on(key, callback) {
        const handler = (e) => {
            if (e.detail.key === key) callback(e.detail.data);
        };
        window.addEventListener('crm:state-change', handler);
        return () => window.removeEventListener('crm:state-change', handler);
    },

    emit(event, data) {
        window.dispatchEvent(new CustomEvent(event, { detail: data }));
    },

    onEvent(event, callback) {
        const handler = (e) => callback(e.detail);
        window.addEventListener(event, handler);
        return () => window.removeEventListener(event, handler);
    }
};

window.crmEmit = (event, data) => CRM_STORE.emit(event, data);
window.crmOn = (event, callback) => CRM_STORE.onEvent(event, callback);
