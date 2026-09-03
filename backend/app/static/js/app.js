/* ops-center 主应用：登录、全局布局、应用切换、路由 */
(function (global) {
    'use strict';

    const { createApp, ref, computed, onMounted, onUnmounted } = Vue;
    const C = global.OCComponents || {};

    // view key（小写，用于 URL）→ 组件名（components.js 导出，首字母大写）
    const VIEW_COMPONENT = {
        overview: 'Overview', resources: 'Resources',
        tasks: 'Operations', schedules: 'Schedules',
        accounts: 'Accounts', users: 'Users', audit: 'Audit'
    };

    const ROLE_LABELS = { admin: '管理员', operator: '运维', readonly: '只读' };
    const ROLE_TAG = { admin: 'danger', operator: 'warning', readonly: 'info' };

    const app = createApp({
        setup() {
            // ---------- 状态 ----------
            const token = ref(localStorage.getItem(global.TOKEN_KEY) || '');
            const isLogin = computed(() => !!token.value);
            const me = ref({ username: '', role: 'readonly', full_name: '' });
            const view = ref('overview');
            const apps = ref([]);
            const envs = ref([]);
            const currentAppId = ref(null);
            const currentEnv = ref('');
            const runningCount = ref(0);
            const syncing = ref(false);

            const loginForm = ref({ username: 'admin', password: '' });
            const loginLoading = ref(false);

            const pwdDialog = ref(false);
            const pwdForm = ref({ old: '', nw: '' });

            // 侧边栏菜单
            const menus = [
                {
                    title: '资源视图',
                    items: [
                        { key: 'overview', label: '概览', icon: 'Odometer' },
                        { key: 'resources', label: '资源清单', icon: 'Grid' }
                    ]
                },
                {
                    title: '运维操作',
                    items: [
                        { key: 'tasks', label: '任务中心', icon: 'List' },
                        { key: 'schedules', label: '定时开关机', icon: 'Timer' }
                    ]
                },
                {
                    title: '系统管理',
                    items: [
                        { key: 'accounts', label: '云账号', icon: 'Link' },
                        { key: 'users', label: '用户与权限', icon: 'User' },
                        { key: 'audit', label: '审计日志', icon: 'Document' }
                    ]
                }
            ];

            const viewComponent = computed(() => C[VIEW_COMPONENT[view.value]] || C.Overview);

            // ---------- 方法 ----------
            function login() {
                loginLoading.value = true;
                api.login(loginForm.value.username, loginForm.value.password)
                    .then(r => {
                        token.value = r.token;
                        localStorage.setItem(global.TOKEN_KEY, r.token);
                        me.value = r.user;
                        boot();
                    })
                    .finally(() => { loginLoading.value = false; });
            }

            function doLogin() {
                if (!loginForm.value.username || !loginForm.value.password) {
                    ElementPlus.ElMessage.warning('请输入用户名和密码');
                    return;
                }
                login();
            }

            function logout() {
                localStorage.removeItem(global.TOKEN_KEY);
                token.value = '';
                me.value = { username: '', role: 'readonly' };
                apps.value = [];
                view.value = 'overview';
            }

            function boot() {
                api.me().then(r => { me.value = r; }).catch(() => logout());
                loadApps();
                loadEnvs();
                pollRunning();
            }

            function loadApps() {
                api.listApps().then(r => { apps.value = r.items; });
            }

            function loadEnvs() {
                api.environments().then(r => { envs.value = r.items; });
            }

            function syncAll() {
                syncing.value = true;
                api.syncAll().then(r => {
                    ElementPlus.ElMessage.success(`同步完成：成功 ${r.succeed}/${r.total} 个账号`);
                    loadApps();
                }).finally(() => { syncing.value = false; });
            }

            // 视图 key -> URL 路径映射（SPA 前端路由，服务端已做回退到 index.html）
            const PATH_MAP = {
                overview: 'overview', resources: 'resources',
                tasks: 'tasks', schedules: 'schedules',
                accounts: 'accounts', users: 'users', audit: 'audit'
            };
            function resolveViewFromPath() {
                const seg = (window.location.pathname.split('/').filter(Boolean).pop() || '').toLowerCase();
                return PATH_MAP[seg] || 'overview';
            }
            function go(key) {
                view.value = key;
                const target = '/' + key;
                if (window.location.pathname !== target) {
                    window.history.pushState({}, '', target);
                }
            }
            window.addEventListener('popstate', () => { view.value = resolveViewFromPath(); });

            function onAppChange() { /* 子组件 watch appId 自动刷新 */ }
            function onEnvChange() { /* 子组件 watch env 自动刷新 */ }

            function onUserCommand(cmd) {
                if (cmd === 'logout') logout();
                else if (cmd === 'password') { pwdForm.value = { old: '', nw: '' }; pwdDialog.value = true; }
            }

            function doChangePwd() {
                if (!pwdForm.value.nw || pwdForm.value.nw.length < 8) {
                    ElementPlus.ElMessage.warning('新密码至少 8 位');
                    return;
                }
                api.changePassword(pwdForm.value.old, pwdForm.value.nw).then(() => {
                    ElementPlus.ElMessage.success('密码已修改');
                    pwdDialog.value = false;
                });
            }

            // 轮询运行中任务
            let timer = null;
            function pollRunning() {
                api.runningTasks().then(r => { runningCount.value = r.count; }).catch(() => {});
                if (timer) clearTimeout(timer);
                timer = setTimeout(pollRunning, 3000);
            }

            onMounted(() => {
                view.value = resolveViewFromPath();
                if (token.value) boot();
            });

            onUnmounted(() => { if (timer) clearTimeout(timer); });

            return {
                isLogin, me, view, apps, envs, currentAppId, currentEnv, runningCount, syncing,
                loginForm, loginLoading, pwdDialog, pwdForm, menus, viewComponent,
                doLogin, logout, loadApps, syncAll, go, onAppChange, onEnvChange,
                onUserCommand, doChangePwd, pollRunning,
                roleLabel: r => ROLE_LABELS[r] || r,
                roleTagType: r => ROLE_TAG[r] || 'info'
            };
        }
    });

    // 注册 Element Plus 与图标
    app.use(ElementPlus, { locale: ElementPlusLocaleZhCn });
    if (global.ElementPlusIconsVue) {
        for (const [key, comp] of Object.entries(ElementPlusIconsVue)) {
            app.component(key, comp);
        }
    }

    app.mount('#app');
})(window);
