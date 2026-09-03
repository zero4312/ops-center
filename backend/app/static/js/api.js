/* ops-center 前端 API 封装（axios） */
(function (global) {
    'use strict';

    const TOKEN_KEY = 'ops_center_token';

    const http = axios.create({
        baseURL: '/api',
        timeout: 60000,
        headers: { 'Content-Type': 'application/json' }
    });

    // 请求拦截：附带 JWT
    http.interceptors.request.use(cfg => {
        const t = localStorage.getItem(TOKEN_KEY);
        if (t) cfg.headers.Authorization = 'Bearer ' + t;
        return cfg;
    });

    // 响应拦截：统一错误提示 + 401 跳转登录
    http.interceptors.response.use(
        resp => resp.data,
        err => {
            const status = err.response && err.response.status;
            const detail = (err.response && err.response.data && err.response.data.detail) || err.message || '请求失败';

            if (status === 401 && !String(err.config.url || '').includes('/auth/login')) {
                localStorage.removeItem(TOKEN_KEY);
                global.location.reload();
                return Promise.reject(new Error(detail));
            }
            if (global.ElMessage) global.ElMessage.error(detail);
            return Promise.reject(new Error(detail));
        }
    );

    const api = {
        // ---- 认证 ----
        login: (username, password) => http.post('/auth/login', { username, password }),
        me: () => http.get('/auth/me'),
        changePassword: (old_password, new_password) =>
            http.post('/auth/change-password', { old_password, new_password }),

        // ---- 云账号 ----
        listAccounts: () => http.get('/accounts'),
        createAccount: d => http.post('/accounts', d),
        updateAccount: (id, d) => http.put('/accounts/' + id, d),
        deleteAccount: id => http.delete('/accounts/' + id),
        testAccount: id => http.post('/accounts/' + id + '/test'),
        syncAccount: id => http.post('/accounts/' + id + '/sync'),
        syncAll: () => http.post('/accounts/sync-all'),

        // ---- 应用 ----
        listApps: () => http.get('/apps'),
        createApp: d => http.post('/apps', d),
        updateApp: (id, d) => http.put('/apps/' + id, d),
        deleteApp: id => http.delete('/apps/' + id),
        bindResources: (id, ids) => http.post('/apps/' + id + '/bind', { resource_ids: ids }),
        unbindResources: (id, ids) => http.post('/apps/' + id + '/unbind', { resource_ids: ids }),
        recomputeApps: () => http.post('/apps/recompute'),

        // ---- 资源 ----
        listResources: p => http.get('/resources', { params: p }),
        summary: p => http.get('/resources/summary', { params: p }),
        environments: () => http.get('/resources/environments'),
        refreshResource: id => http.post('/resources/' + id + '/refresh'),
        refreshStatus: ids => http.post('/resources/refresh-status', { ids: ids || [] }),
        updateResource: (id, d) => http.put('/resources/' + id, d),

        // ---- 开关机 ----
        execute: d => http.post('/operations/execute', d),
        listTasks: p => http.get('/operations', { params: p }),
        runningTasks: () => http.get('/operations/running'),
        getTask: id => http.get('/operations/' + id),

        // ---- 定时策略 ----
        listPolicies: () => http.get('/schedules'),
        createPolicy: d => http.post('/schedules', d),
        updatePolicy: (id, d) => http.put('/schedules/' + id, d),
        deletePolicy: id => http.delete('/schedules/' + id),
        togglePolicy: id => http.post('/schedules/' + id + '/toggle'),
        runPolicy: id => http.post('/schedules/' + id + '/run'),
        scheduleLogs: () => http.get('/schedules/logs'),

        // ---- 用户与审计 ----
        listUsers: () => http.get('/users'),
        createUser: d => http.post('/users', d),
        updateUser: (id, d) => http.put('/users/' + id, d),
        deleteUser: id => http.delete('/users/' + id),
        resetPassword: (id, p) => http.post('/users/' + id + '/reset-password', { new_password: p }),
        auditLogs: p => http.get('/users/audit-logs', { params: p })
    };

    global.api = api;
    global.TOKEN_KEY = TOKEN_KEY;
})(window);
