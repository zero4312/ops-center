/* ops-center 前端视图组件（无构建，Vue 3 global build） */
(function (global) {
    'use strict';

    // ---------- 工具函数 ----------
    function fmtTime(s) {
        if (!s) return '-';
        let str = String(s);
        // 后端返回 naive UTC 时间，补 Z 后按 UTC 解析，再转本地展示
        if (!/Z|[+-]\d{2}:\d{2}$/.test(str)) str = str.replace(' ', 'T') + 'Z';
        const d = new Date(str);
        if (isNaN(d.getTime())) return s;
        const p = n => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }

    function fmtCron(cron) {
        if (!cron) return '-';
        const parts = cron.trim().split(/\s+/);
        if (parts.length !== 5) return cron;
        const [m, h, d, mo, w] = parts;
        const weekMap = { '0': '周日', '1': '周一', '2': '周二', '3': '周三', '4': '周四', '5': '周五', '6': '周六', '7': '周日' };
        let when = '';
        if (w !== '*' && !w.includes(',')) when = weekMap[w] || '';
        else if (d !== '*' && mo === '*' && w === '*') when = `每月 ${d} 日`;
        else if (d === '*' && w === '*') when = '每天';
        else if (w.includes('-')) when = '工作日';
        return `${h}:${m} ${when}`.trim();
    }

    const powerStateMeta = {
        running: { type: 'success', text: '运行中' },
        stopped: { type: 'danger', text: '已停止' },
        other: { type: 'info', text: '其他' }
    };
    const taskStatusMeta = {
        pending: { type: 'info', text: '待执行' },
        running: { type: 'warning', text: '执行中' },
        success: { type: 'success', text: '成功' },
        partial: { type: 'warning', text: '部分成功' },
        failed: { type: 'danger', text: '失败' },
        cancelled: { type: 'info', text: '已取消' }
    };
    const itemStatusMeta = {
        pending: { type: 'info', text: '待执行' },
        running: { type: 'warning', text: '执行中' },
        success: { type: 'success', text: '成功' },
        failed: { type: 'danger', text: '失败' },
        skipped: { type: 'info', text: '跳过' }
    };

    // ======================================================================
    // 概览
    // ======================================================================
    const Overview = {
        props: ['appId', 'env'],
        template: `
        <div>
            <h2 class="page-title">{{ appId ? '当前应用概览' : '全局概览' }}</h2>
            <el-row :gutter="14">
                <el-col :span="6">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-primary">{{ summary.total }}</div>
                        <div class="stat-label">资源总数</div>
                    </div>
                </el-col>
                <el-col :span="6">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-success">{{ runningTotal }}</div>
                        <div class="stat-label">运行中</div>
                    </div>
                </el-col>
                <el-col :span="6">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-danger">{{ stoppedTotal }}</div>
                        <div class="stat-label">已停止</div>
                    </div>
                </el-col>
                <el-col :span="6">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-warning">{{ summary.stop_saving_count }}</div>
                        <div class="stat-label">停机可省资源</div>
                    </div>
                </el-col>
            </el-row>

            <div class="oc-card" style="padding:14px 16px">
                <h3 style="margin:0 0 12px;font-size:15px">
                    运行中资源
                    <el-tag size="small" type="success" style="margin-left:8px">{{ runningRows.length }}</el-tag>
                </h3>
                <el-table :data="runningRows" v-loading="runningLoading" border size="small" stripe
                          max-height="420" empty-text="当前没有运行中的资源">
                    <el-table-column label="云账号" prop="account_name" min-width="150" show-overflow-tooltip />
                    <el-table-column label="实例名称" min-width="240" show-overflow-tooltip>
                        <template #default="{ row }">{{ row.resource_name || row.resource_id }}</template>
                    </el-table-column>
                    <el-table-column label="运行中" width="100">
                        <template #default>
                            <span><span class="dot dot-running"></span>运行中</span>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <el-row :gutter="14" style="margin-top:4px">
                <el-col :span="8">
                    <div class="oc-card">
                        <h3 style="margin:0 0 12px;font-size:15px">按资源类型</h3>
                        <div v-for="(v, k) in summary.by_type" :key="k" style="margin-bottom:10px">
                            <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:4px">
                                <span>{{ k }}</span>
                                <span class="text-muted">运行 {{ v.running }} / 停止 {{ v.stopped }} / 共 {{ v.total }}</span>
                            </div>
                            <el-progress :percentage="pct(v.running, v.total)" :stroke-width="14"
                                         :color="v.total ? '#2563eb' : '#e5e7eb'" />
                        </div>
                        <el-empty v-if="!Object.keys(summary.by_type || {}).length" description="暂无资源" :image-size="60" />
                    </div>
                </el-col>
                <el-col :span="8">
                    <div class="oc-card">
                        <h3 style="margin:0 0 12px;font-size:15px">应用资源 TOP10</h3>
                        <div v-for="a in topApps" :key="a.id" style="display:flex;align-items:center;margin-bottom:9px">
                            <span style="width:150px;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="a.name">{{ a.name }}</span>
                            <el-progress :percentage="pct(a.stats.total, maxAppTotal)" :stroke-width="10"
                                         style="flex:1" :show-text="false" color="#67c23a" />
                            <span style="width:40px;text-align:right;font-size:13px;color:#909399">{{ a.stats.total }}</span>
                        </div>
                        <el-empty v-if="!topApps.length" description="暂无应用" :image-size="60" />
                    </div>
                </el-col>
                <el-col :span="8">
                    <div class="oc-card">
                        <h3 style="margin:0 0 12px;font-size:15px">最近任务</h3>
                        <div v-for="t in recentTasks" :key="t.id" style="display:flex;align-items:center;margin-bottom:9px;font-size:13px">
                            <el-tag size="small" :type="(taskStatusMeta[t.status]||{}).type">{{ t.action_label }}</el-tag>
                            <span style="margin-left:8px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ t.app_name || ('#'+t.id) }}</span>
                            <span class="text-muted">{{ fmtTime(t.created_at) }}</span>
                        </div>
                        <el-empty v-if="!recentTasks.length" description="暂无任务" :image-size="60" />
                    </div>
                </el-col>
            </el-row>
        </div>`,
        data() {
            return {
                summary: { by_type: {}, stop_saving_count: 0, total: 0 },
                apps: [], tasks: [], taskStatusMeta,
                runningRows: [], runningLoading: false
            };
        },
        computed: {
            runningTotal() { return this.sumTotals('running'); },
            stoppedTotal() { return this.sumTotals('stopped'); },
            topApps() { return this.apps.slice(0, 10); },
            maxAppTotal() { return Math.max(1, ...this.apps.map(a => a.stats.total)); },
            recentTasks() { return this.tasks.slice(0, 8); }
        },
        methods: {
            fmtTime,
            pct(n, total) { return total ? Math.round(n / total * 100) : 0; },
            sumTotals(k) {
                return Object.values(this.summary.by_type || {}).reduce((s, v) => s + (v[k] || 0), 0);
            },
            load() {
                api.summary({ app_id: this.appId }).then(r => { this.summary = r; });
                api.listApps().then(r => { this.apps = r.items.filter(a => a.stats.total > 0); });
                api.listTasks({ limit: 20 }).then(r => { this.tasks = r.items; });
                this.runningLoading = true;
                api.listResources({ app_id: this.appId, power_state: 'running', page: 1, page_size: 500 })
                    .then(r => { this.runningRows = r.items; })
                    .finally(() => { this.runningLoading = false; });
            }
        },
        watch: {
            appId() { this.load(); }
        },
        mounted() { this.load(); }
    };

    // ======================================================================
    // 资源清单
    // ======================================================================
    const Resources = {
        props: ['appId', 'env', 'me'],
        emits: ['go', 'task-created'],
        template: `
        <div>
            <h2 class="page-title">资源清单</h2>
            <div class="oc-toolbar">
                <el-select v-model="q.resource_type" clearable placeholder="资源类型" style="width:120px" @change="load(1)">
                    <el-option label="ECS" value="ECS" />
                    <el-option label="RDS" value="RDS" />
                </el-select>
                <el-select v-model="q.power_state" clearable placeholder="状态" style="width:120px" @change="load(1)">
                    <el-option label="运行中" value="running" />
                    <el-option label="已停止" value="stopped" />
                    <el-option label="其他" value="other" />
                </el-select>
                <el-select v-model="q.account_id" clearable placeholder="云账号" style="width:150px" @change="load(1)">
                    <el-option v-for="a in accounts" :key="a.id" :label="a.name" :value="a.id" />
                </el-select>
                <el-input v-model="q.keyword" placeholder="实例名 / ID / IP 搜索" clearable style="width:200px"
                          @keyup.enter="load(1)" @clear="load(1)" />
                <el-button :icon="Search" @click="load(1)">查询</el-button>
                <div class="oc-spacer"></div>
                <el-button :icon="Refresh" @click="refreshStatus" :loading="refreshing">刷新状态</el-button>
                <el-button type="success" :icon="VideoPlay" :disabled="!selected.length" @click="operate('start')">
                    开机 ({{ selected.length }})
                </el-button>
                <el-button type="danger" :icon="VideoPause" :disabled="!selected.length" @click="operate('stop')">
                    节省关机 ({{ selected.length }})
                </el-button>
                <el-button type="primary" plain icon="Connection" :disabled="!selected.length" @click="openBatchBind">
                    关联应用 ({{ selected.length }})
                </el-button>
                <el-dropdown v-if="appId" split-button type="primary" @command="operateApp" @click="operateApp('stop')">
                    本应用节省关机
                    <template #dropdown>
                        <el-dropdown-menu>
                            <el-dropdown-item command="stop">应用整体节省关机</el-dropdown-item>
                            <el-dropdown-item command="start">应用整体开机</el-dropdown-item>
                        </el-dropdown-menu>
                    </template>
                </el-dropdown>
            </div>

            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" @selection-change="s => selected = s" border size="small" stripe>
                    <el-table-column type="selection" width="42" />
                    <el-table-column label="实例名称" prop="resource_name" min-width="200" show-overflow-tooltip>
                        <template #default="{ row }">
                            <span :title="row.resource_name">{{ row.resource_name || row.resource_id }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="类型" width="70">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.resource_type === 'ECS' ? 'primary' : 'success'">{{ row.resource_type }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="云账号" prop="account_name" width="150" show-overflow-tooltip />
                    <el-table-column label="应用" width="140">
                        <template #default="{ row }">
                            <span :style="{ color: row.app_id ? '#303133' : '#c0c4cc' }">
                                {{ row.app_name }}
                                <el-tag v-if="row.is_manual" size="small" effect="plain" style="margin-left:2px">手工</el-tag>
                            </span>
                        </template>
                    </el-table-column>
                    <el-table-column label="状态" width="90">
                        <template #default="{ row }">
                            <span><span class="dot" :class="'dot-' + row.power_state"></span>
                            {{ (powerStateMeta[row.power_state] || {}).text || row.status }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="环境" prop="env" width="70" />
                    <el-table-column label="CPU(核)" width="80">
                        <template #default="{ row }">{{ row.cpu != null ? row.cpu : '-' }}</template>
                    </el-table-column>
                    <el-table-column label="内存(GB)" width="80">
                        <template #default="{ row }">{{ row.memory_gb != null ? row.memory_gb : '-' }}</template>
                    </el-table-column>
                    <el-table-column label="计费" width="90">
                        <template #default="{ row }">{{ row.charge_label || '-' }}</template>
                    </el-table-column>
                    <el-table-column label="内网IP" prop="private_ip" width="130" show-overflow-tooltip />
                    <el-table-column label="停机可省" width="95">
                        <template #default="{ row }">
                            <el-tooltip :content="row.stop_saving_reason || ''" :disabled="!row.stop_saving_reason" placement="top">
                                <el-tag v-if="row.stop_saving === true" size="small" type="warning">可省</el-tag>
                                <el-tag v-else-if="row.stop_saving === false" size="small" type="info">不省</el-tag>
                                <span v-else class="text-muted">未知</span>
                            </el-tooltip>
                        </template>
                    </el-table-column>
                    <el-table-column label="纳管" width="80">
                        <template #default="{ row }">
                            <el-switch :model-value="row.managed" size="small"
                                       :disabled="me.role === 'readonly'"
                                       @change="v => toggleManaged(row, v)" />
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="180" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="success" size="small" @click="operateOne('start', row)">开机</el-button>
                            <el-button link type="danger" size="small" @click="operateOne('stop', row)">节省关机</el-button>
                            <el-button link type="primary" size="small" @click="openBind(row)">归属</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <div style="display:flex;justify-content:flex-end;align-items:center">
                <span class="text-muted" style="margin-right:12px">共 {{ total }} 条</span>
                <el-pagination layout="prev, pager, next" :total="total" :page-size="q.page_size"
                               :current-page="q.page" @current-change="p => { q.page = p; load(); }" />
            </div>

            <!-- 归属绑定对话框（单个 / 批量，支持新建应用） -->
            <el-dialog v-model="bindDialog" :title="bindMode === 'batch' ? '批量关联应用' : '资源归属应用'" width="500px">
                <p style="margin:0 0 12px;font-size:13px;color:#606266">
                    <template v-if="bindMode === 'batch'">
                        已选 <b>{{ selected.length }}</b> 个资源：
                        <span class="text-muted" style="font-size:12px">{{ selected.slice(0, 3).map(r => r.resource_name).join('、') }}{{ selected.length > 3 ? ' 等' : '' }}</span>
                    </template>
                    <template v-else>
                        资源：<b>{{ bindRow.resource_name }}</b>（{{ bindRow.resource_id }}）<br>
                        <span style="color:#909399">当前归属：{{ bindRow.app_name }}{{ bindRow.is_manual ? '（手工）' : '' }}</span>
                    </template>
                </p>

                <el-form label-width="80px">
                    <el-form-item label="目标应用">
                        <el-select v-model="bindAppId" filterable clearable
                                   :disabled="bindCreateNew"
                                   placeholder="选择应用（清空=恢复自动解析）" style="width:100%">
                            <el-option v-for="a in apps" :key="a.id" :label="a.name" :value="a.id" />
                        </el-select>
                    </el-form-item>
                    <el-form-item>
                        <el-checkbox v-model="bindCreateNew">新建应用并关联</el-checkbox>
                    </el-form-item>
                    <template v-if="bindCreateNew">
                        <el-form-item label="应用名称" required>
                            <el-input v-model="bindNewApp.name" placeholder="如 APC-DEMO" maxlength="128" />
                        </el-form-item>
                        <el-form-item label="应用编码" required>
                            <el-input v-model="bindNewApp.code" placeholder="如 DEMO（唯一）" maxlength="128" />
                        </el-form-item>
                        <el-form-item label="负责人">
                            <el-input v-model="bindNewApp.owner" placeholder="选填" maxlength="64" />
                        </el-form-item>
                    </template>
                </el-form>

                <el-alert v-if="!bindCreateNew" type="info" :closable="false" show-icon
                          title="手工关联优先于按实例名自动解析；清空选择可恢复自动解析。" />

                <template #footer>
                    <el-button @click="bindDialog = false">取消</el-button>
                    <el-button type="primary" :loading="bindLoading" @click="doBind">确定</el-button>
                </template>
            </el-dialog>
        </div>`,
        data() {
            return {
                rows: [], total: 0, loading: false, refreshing: false,
                accounts: [], apps: [], selected: [],
                q: { resource_type: '', power_state: '', account_id: '', keyword: '', page: 1, page_size: 50 },
                bindDialog: false, bindMode: 'row', bindRow: {}, bindAppId: null,
                bindCreateNew: false, bindLoading: false,
                bindNewApp: { name: '', code: '', owner: '' },
                powerStateMeta
            };
        },
        methods: {
            fmtTime,
            load(page) {
                if (page) this.q.page = page;
                this.loading = true;
                api.listResources({
                    app_id: this.appId,
                    resource_type: this.q.resource_type || undefined,
                    power_state: this.q.power_state || undefined,
                    account_id: this.q.account_id || undefined,
                    keyword: this.q.keyword || undefined,
                    page: this.q.page, page_size: this.q.page_size
                }).then(r => {
                    this.rows = r.items; this.total = r.total;
                }).finally(() => { this.loading = false; });
            },
            loadAccounts() {
                api.listAccounts().then(r => { this.accounts = r.items; });
                api.listApps().then(r => { this.apps = r.items; });
            },
            refreshStatus() {
                this.refreshing = true;
                api.refreshStatus(this.selected.map(r => r.id)).then(r => {
                    this.$message.success(`刷新完成：成功 ${r.succeed}，失败 ${r.failed}`);
                    this.load();
                }).finally(() => { this.refreshing = false; });
            },
            toggleManaged(row, v) {
                api.updateResource(row.id, { managed: v }).then(() => {
                    row.managed = v;
                    this.$message.success(v ? '已纳入开关机范围' : '已排除出开关机范围');
                });
            },
            openBind(row) {
                this.bindMode = 'row';
                this.bindRow = row;
                this.bindAppId = row.app_id;
                this.bindCreateNew = false;
                this.bindNewApp = { name: '', code: '', owner: '' };
                this.bindDialog = true;
            },
            openBatchBind() {
                if (!this.selected.length) return;
                this.bindMode = 'batch';
                this.bindAppId = null;
                this.bindCreateNew = false;
                this.bindNewApp = { name: '', code: '', owner: '' };
                this.bindDialog = true;
            },
            doBind() {
                const ids = this.bindMode === 'batch'
                    ? this.selected.map(r => r.id) : [this.bindRow.id];
                if (!ids.length) return;
                this.bindLoading = true;

                let p;
                if (this.bindCreateNew) {
                    const { name, code, owner } = this.bindNewApp;
                    if (!name || !code) {
                        this.$message.warning('请填写新应用的名称与编码');
                        this.bindLoading = false;
                        return;
                    }
                    p = api.createApp({ name, code, owner }).then(r => (
                        api.batchAssign({ resource_ids: ids, app_id: r.id })
                    ));
                } else {
                    p = api.batchAssign({ resource_ids: ids, app_id: this.bindAppId || null });
                }

                p.then(r => {
                    this.$message.success(r.message || '已更新归属');
                    this.bindDialog = false;
                    this.selected = [];
                    this.load();
                    this.$emit('refresh-apps');
                }).finally(() => { this.bindLoading = false; });
            },
            confirmOperate(action, label) {
                return new Promise(resolve => {
                    this.$confirm(`确定对「${label}」执行【${action === 'start' ? '开机' : '节省关机'}】吗？节省关机将回收计算资源以降低费用，开机可能因库存不足失败。`, '操作确认', {
                        type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消'
                    }).then(() => resolve(true)).catch(() => resolve(false));
                });
            },
            async operate(action) {
                if (!this.selected.length) return;
                const label = `${this.selected.length} 个资源`;
                if (!(await this.confirmOperate(action, label))) return;
                api.execute({ action, resource_ids: this.selected.map(r => r.id) }).then(r => {
                    this.$message.success(r.message);
                    this.selected = [];
                    this.$emit('task-created');
                    this.$emit('go', 'tasks');
                });
            },
            async operateOne(action, row) {
                if (!(await this.confirmOperate(action, row.resource_name))) return;
                api.execute({ action, resource_ids: [row.id] }).then(r => {
                    this.$message.success(r.message);
                    this.$emit('task-created');
                });
            },
            async operateApp(action) {
                if (!this.appId) return;
                const label = this.appName();
                if (!(await this.confirmOperate(action, '本应用 ' + label))) return;
                api.execute({ action, app_id: this.appId }).then(r => {
                    this.$message.success(r.message);
                    this.$emit('task-created');
                    this.$emit('go', 'tasks');
                });
            },
            appName() {
                const a = this.apps.find(x => x.id === this.appId);
                return a ? a.name : '全部资源';
            }
        },
        watch: {
            appId() { this.load(1); },
            env() { this.load(1); }
        },
        mounted() {
            this.loadAccounts();
            this.load();
        }
    };

    // ======================================================================
    // 任务中心
    // ======================================================================
    const Operations = {
        props: ['appId'],
        emits: ['go'],
        template: `
        <div>
            <h2 class="page-title">任务中心</h2>
            <div class="oc-toolbar">
                <el-select v-model="q.status" clearable placeholder="状态" style="width:140px" @change="load">
                    <el-option v-for="(m, k) in taskStatusMeta" :key="k" :label="m.text" :value="k" />
                </el-select>
                <el-select v-model="q.action" clearable placeholder="动作" style="width:110px" @change="load">
                    <el-option label="开机" value="start" />
                    <el-option label="节省关机" value="stop" />
                </el-select>
                <el-button :icon="Refresh" @click="load">刷新</el-button>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="ID" prop="id" width="70" />
                    <el-table-column label="动作" width="90">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.action === 'start' ? 'success' : 'danger'">{{ row.action_label }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="目标" width="160">
                        <template #default="{ row }">{{ row.app_name || row.scope }}</template>
                    </el-table-column>
                    <el-table-column label="触发" width="90">
                        <template #default="{ row }">
                            {{ row.trigger === 'schedule' ? '定时' : '手动' }}
                        </template>
                    </el-table-column>
                    <el-table-column label="操作人" prop="operator" width="100" />
                    <el-table-column label="状态" width="110">
                        <template #default="{ row }">
                            <el-tag size="small" :type="(taskStatusMeta[row.status] || {}).type">
                                <el-icon v-if="row.status === 'running'" class="is-loading" style="margin-right:3px"><Loading /></el-icon>
                                {{ (taskStatusMeta[row.status] || {}).text || row.status }}
                            </el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="进度" width="180">
                        <template #default="{ row }">
                            <el-progress :percentage="progress(row)" :stroke-width="12"
                                         :status="row.status === 'failed' ? 'exception' : (row.status === 'success' ? 'success' : undefined)" />
                        </template>
                    </el-table-column>
                    <el-table-column label="成功/失败/跳过" width="130">
                        <template #default="{ row }">
                            <span class="text-success">{{ row.succeed }}</span> /
                            <span class="text-danger">{{ row.failed }}</span> /
                            <span class="text-muted">{{ row.skipped }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="创建时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column label="操作" width="80" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <el-drawer v-model="detailVisible" :title="'任务 #' + (detail.id || '') + ' 明细'" size="60%">
                <div v-if="detail.id">
                    <el-descriptions :column="3" border size="small" style="margin-bottom:16px">
                        <el-descriptions-item label="动作">{{ detail.action_label }}</el-descriptions-item>
                        <el-descriptions-item label="状态">
                            <el-tag size="small" :type="(taskStatusMeta[detail.status] || {}).type">{{ (taskStatusMeta[detail.status] || {}).text }}</el-tag>
                        </el-descriptions-item>
                        <el-descriptions-item label="操作人">{{ detail.operator }}</el-descriptions-item>
                        <el-descriptions-item label="总数">{{ detail.total }}</el-descriptions-item>
                        <el-descriptions-item label="成功">{{ detail.succeed }}</el-descriptions-item>
                        <el-descriptions-item label="失败">{{ detail.failed }}</el-descriptions-item>
                    </el-descriptions>
                    <el-table :data="detail.items || []" border size="small" stripe>
                        <el-table-column label="资源" prop="resource_name" min-width="180" show-overflow-tooltip />
                        <el-table-column label="类型" prop="resource_type" width="70" />
                        <el-table-column label="账号" prop="account_name" width="120" show-overflow-tooltip />
                        <el-table-column label="状态" width="90">
                            <template #default="{ row }">
                                <el-tag size="small" :type="(itemStatusMeta[row.status] || {}).type">{{ (itemStatusMeta[row.status] || {}).text }}</el-tag>
                            </template>
                        </el-table-column>
                        <el-table-column label="结果" prop="message" min-width="220" show-overflow-tooltip />
                    </el-table>
                </div>
            </el-drawer>
        </div>`,
        data() {
            return {
                rows: [], loading: false, taskStatusMeta, itemStatusMeta,
                q: { status: '', action: '' },
                detailVisible: false, detail: {}
            };
        },
        methods: {
            fmtTime,
            progress(row) {
                if (row.status === 'pending') return 0;
                if (row.status === 'running') {
                    const done = (row.succeed || 0) + (row.failed || 0) + (row.skipped || 0);
                    return row.total ? Math.round(done / row.total * 100) : 0;
                }
                return 100;
            },
            load() {
                this.loading = true;
                api.listTasks({ status: this.q.status || undefined, action: this.q.action || undefined, limit: 100 })
                    .then(r => { this.rows = r.items; })
                    .finally(() => { this.loading = false; });
            },
            openDetail(row) {
                api.getTask(row.id).then(r => { this.detail = r; this.detailVisible = true; });
            }
        },
        mounted() { this.load(); }
    };

    // ======================================================================
    // 定时策略
    // ======================================================================
    const Schedules = {
        props: ['appId', 'me'],
        emits: ['task-created'],
        template: `
        <div>
            <h2 class="page-title">定时开关机</h2>
            <div class="oc-toolbar">
                <span class="text-muted" style="font-size:13px">cron 为 5 段式（分 时 日 月 周），时区 Asia/Shanghai</span>
                <div class="oc-spacer"></div>
                <el-button type="primary" :icon="Plus" @click="openCreate" :disabled="me.role === 'readonly'">新建策略</el-button>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="策略名称" prop="name" min-width="160" show-overflow-tooltip />
                    <el-table-column label="动作" width="90">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.action === 'start' ? 'success' : 'danger'">{{ row.action_label }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="目标" prop="target_desc" width="160" show-overflow-tooltip />
                    <el-table-column label="执行时间" width="160">
                        <template #default="{ row }">
                            <span class="mono">{{ row.cron_expr }}</span>
                            <div class="text-muted" style="font-size:12px">{{ fmtCron(row.cron_expr) }}</div>
                        </template>
                    </el-table-column>
                    <el-table-column label="下次执行" width="170">
                        <template #default="{ row }">{{ fmtTime(row.next_run_at) }}</template>
                    </el-table-column>
                    <el-table-column label="上次执行" width="170">
                        <template #default="{ row }">
                            <div>{{ fmtTime(row.last_run_at) }}</div>
                            <div v-if="row.last_status" style="font-size:12px">
                                <el-tag size="small" :type="(taskStatusMeta[row.last_status] || {}).type">{{ (taskStatusMeta[row.last_status] || {}).text }}</el-tag>
                            </div>
                        </template>
                    </el-table-column>
                    <el-table-column label="启用" width="80">
                        <template #default="{ row }">
                            <el-switch :model-value="row.enabled" size="small" :disabled="me.role === 'readonly'"
                                       @change="v => toggle(row, v)" />
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="220" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="primary" size="small" @click="runNow(row)">立即执行</el-button>
                            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <div class="oc-card">
                <h3 style="margin:0 0 12px;font-size:15px">最近执行记录</h3>
                <el-table :data="logs" border size="small" stripe>
                    <el-table-column label="策略" prop="policy_name" min-width="150" show-overflow-tooltip />
                    <el-table-column label="触发时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.fired_at) }}</template>
                    </el-table-column>
                    <el-table-column label="状态" width="100">
                        <template #default="{ row }">
                            <el-tag size="small" :type="(taskStatusMeta[row.status] || {}).type">{{ (taskStatusMeta[row.status] || {}).text || row.status }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="成功/失败/跳过" width="130">
                        <template #default="{ row }">
                            <span class="text-success">{{ row.succeed }}</span> /
                            <span class="text-danger">{{ row.failed }}</span> /
                            <span class="text-muted">{{ row.skipped }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="备注" prop="message" min-width="200" show-overflow-tooltip />
                </el-table>
            </div>

            <el-dialog v-model="dialog" :title="form.id ? '编辑策略' : '新建策略'" width="520px">
                <el-form :model="form" label-width="110px">
                    <el-form-item label="策略名称"><el-input v-model="form.name" placeholder="如：每日 09:00 开机" /></el-form-item>
                    <el-form-item label="动作">
                        <el-radio-group v-model="form.action">
                            <el-radio-button value="start">开机</el-radio-button>
                            <el-radio-button value="stop">节省关机</el-radio-button>
                        </el-radio-group>
                    </el-form-item>
                    <el-form-item label="作用范围">
                        <el-radio-group v-model="form.scope">
                            <el-radio-button value="app">按应用</el-radio-button>
                            <el-radio-button value="resource">指定资源</el-radio-button>
                        </el-radio-group>
                    </el-form-item>
                    <el-form-item v-if="form.scope === 'app'" label="目标应用">
                        <el-select v-model="form.target_app_id" filterable placeholder="选择应用" style="width:100%">
                            <el-option v-for="a in apps" :key="a.id" :label="a.name + '（' + a.stats.total + '）'" :value="a.id" />
                        </el-select>
                    </el-form-item>
                    <el-form-item v-else label="目标资源">
                        <el-select v-model="form.target_resource_ids" multiple filterable placeholder="选择资源（可多选）" style="width:100%" collapse-tags>
                            <el-option v-for="r in resourceOptions" :key="r.id" :label="r.resource_name" :value="r.id" />
                        </el-select>
                    </el-form-item>
                    <el-form-item label="cron 表达式">
                        <el-input v-model="form.cron_expr" placeholder="0 9 * * 1-5">
                            <template #append>Asia/Shanghai</template>
                        </el-input>
                        <div class="text-muted" style="font-size:12px;margin-top:4px">当前解析：{{ fmtCron(form.cron_expr) }}</div>
                    </el-form-item>
                    <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
                </el-form>
                <template #footer>
                    <el-button @click="dialog = false">取消</el-button>
                    <el-button type="primary" @click="save">保存</el-button>
                </template>
            </el-dialog>
        </div>`,
        data() {
            return {
                rows: [], logs: [], apps: [], resourceOptions: [], loading: false,
                taskStatusMeta, dialog: false,
                form: this.blankForm()
            };
        },
        methods: {
            fmtTime, fmtCron,
            blankForm() {
                return { id: null, name: '', action: 'start', scope: 'app', target_app_id: null, target_resource_ids: [], cron_expr: '0 9 * * *', remark: '' };
            },
            load() {
                this.loading = true;
                api.listPolicies().then(r => { this.rows = r.items; }).finally(() => { this.loading = false; });
                api.scheduleLogs().then(r => { this.logs = r.items; });
            },
            loadOptions() {
                api.listApps().then(r => { this.apps = r.items; });
                api.listResources({ page_size: 500 }).then(r => { this.resourceOptions = r.items; });
            },
            openCreate() {
                this.form = this.blankForm();
                this.dialog = true;
            },
            openEdit(row) {
                this.form = {
                    id: row.id, name: row.name, action: row.action, scope: row.scope,
                    target_app_id: row.target_app_id, target_resource_ids: row.target_resource_ids || [],
                    cron_expr: row.cron_expr, remark: row.remark
                };
                this.dialog = true;
            },
            save() {
                const payload = {
                    name: this.form.name, action: this.form.action, scope: this.form.scope,
                    cron_expr: this.form.cron_expr, remark: this.form.remark
                };
                if (this.form.scope === 'app') payload.target_app_id = this.form.target_app_id;
                else payload.target_resource_ids = this.form.target_resource_ids;
                const p = this.form.id
                    ? api.updatePolicy(this.form.id, payload)
                    : api.createPolicy(payload);
                p.then(() => {
                    this.$message.success('已保存');
                    this.dialog = false;
                    this.load();
                });
            },
            toggle(row, v) {
                api.togglePolicy(row.id).then(() => { this.load(); });
            },
            runNow(row) {
                this.$confirm(`立即执行策略「${row.name}」吗？`, '提示', { type: 'warning' }).then(() => {
                    return api.runPolicy(row.id);
                }).then(r => {
                    this.$message.success(r.message);
                    this.$emit('task-created');
                    this.load();
                }).catch(() => {});
            },
            remove(row) {
                this.$confirm(`删除策略「${row.name}」？`, '警告', { type: 'warning' }).then(() => {
                    return api.deletePolicy(row.id);
                }).then(() => {
                    this.$message.success('已删除');
                    this.load();
                }).catch(() => {});
            }
        },
        mounted() {
            this.load();
            this.loadOptions();
        }
    };

    // ======================================================================
    // 云账号
    // ======================================================================
    const Accounts = {
        props: ['me'],
        emits: ['refresh-apps'],
        template: `
        <div>
            <h2 class="page-title">云账号管理</h2>
            <div class="oc-toolbar">
                <span class="text-muted" style="font-size:13px">通过接入不同账号的 AK 扩展纳管范围；请使用 RAM 子用户最小权限 AK，勿用主账号 AK</span>
                <div class="oc-spacer"></div>
                <el-button type="primary" :icon="Plus" @click="openCreate" :disabled="me.role !== 'admin'">新增账号</el-button>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="名称" prop="name" min-width="150" show-overflow-tooltip />
                    <el-table-column label="厂商" width="90">
                        <template #default="{ row }"><el-tag size="small" :type="row.provider === 'alibaba' ? 'primary' : 'success'">{{ row.provider_label }}</el-tag></template>
                    </el-table-column>
                    <el-table-column label="地域" prop="region" width="130" />
                    <el-table-column label="AccessKey" prop="access_key_id" width="180" show-overflow-tooltip />
                    <el-table-column label="资源数" width="120">
                        <template #default="{ row }">
                            <span>ECS {{ (row.resource_counts || {}).ECS || 0 }} / RDS {{ (row.resource_counts || {}).RDS || 0 }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="连接检查" width="140">
                        <template #default="{ row }">
                            <el-tooltip :content="row.last_check_msg || ''" placement="top" :disabled="!row.last_check_msg">
                                <el-tag v-if="row.last_check_ok === true" size="small" type="success">正常</el-tag>
                                <el-tag v-else-if="row.last_check_ok === false" size="small" type="danger">失败</el-tag>
                                <span v-else class="text-muted">未检测</span>
                            </el-tooltip>
                        </template>
                    </el-table-column>
                    <el-table-column label="最近同步" width="170">
                        <template #default="{ row }">{{ fmtTime(row.last_sync_at) }}</template>
                    </el-table-column>
                    <el-table-column label="启用" width="80">
                        <template #default="{ row }">
                            <el-switch :model-value="row.enabled" size="small" :disabled="me.role !== 'admin'"
                                       @change="v => toggleAccount(row, v)" />
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="230" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="primary" size="small" @click="test(row)" :loading="testingId === row.id">测试</el-button>
                            <el-button link type="success" size="small" @click="sync(row)" :loading="syncingId === row.id">同步</el-button>
                            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                            <el-button link type="danger" size="small" @click="remove(row)">删除</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <el-dialog v-model="dialog" :title="form.id ? '编辑账号' : '新增云账号'" width="560px">
                <el-form :model="form" label-width="110px">
                    <el-form-item label="账号名称"><el-input v-model="form.name" placeholder="如：阿里云-生产A" /></el-form-item>
                    <el-form-item label="云厂商">
                        <el-select v-model="form.provider" style="width:100%" :disabled="!!form.id">
                            <el-option v-for="p in providers" :key="p.value" :label="p.label" :value="p.value" />
                        </el-select>
                    </el-form-item>
                    <el-form-item label="地域"><el-input v-model="form.region" placeholder="如：cn-shanghai / cn-beijing" /></el-form-item>
                    <el-form-item label="AccessKeyId"><el-input v-model="form.access_key_id" placeholder="AK" /></el-form-item>
                    <el-form-item label="AccessKeySecret">
                        <el-input v-model="form.access_key_secret" type="password" show-password
                                  :placeholder="form.id ? '留空表示不修改' : 'SK'" />
                    </el-form-item>
                    <el-form-item label="限定 VPC">
                        <el-input v-model="vpcText" placeholder="多个 VPC 用逗号分隔，留空表示不限" />
                    </el-form-item>
                    <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
                </el-form>
                <template #footer>
                    <el-button @click="dialog = false">取消</el-button>
                    <el-button type="primary" @click="save" :loading="saving">保存</el-button>
                </template>
            </el-dialog>
        </div>`,
        data() {
            return {
                rows: [], providers: [], loading: false, saving: false,
                testingId: null, syncingId: null, dialog: false, vpcText: '',
                form: this.blankForm()
            };
        },
        methods: {
            fmtTime,
            blankForm() {
                return { id: null, name: '', provider: 'alibaba', region: 'cn-shanghai', access_key_id: '', access_key_secret: '', remark: '' };
            },
            load() {
                this.loading = true;
                api.listAccounts().then(r => { this.rows = r.items; this.providers = r.providers; })
                    .finally(() => { this.loading = false; });
            },
            openCreate() {
                this.form = this.blankForm();
                this.vpcText = '';
                this.dialog = true;
            },
            openEdit(row) {
                this.form = {
                    id: row.id, name: row.name, provider: row.provider, region: row.region,
                    access_key_id: row.access_key_id, access_key_secret: '', remark: row.remark
                };
                this.vpcText = (row.vpc_ids || []).join(',');
                this.dialog = true;
            },
            save() {
                const payload = {
                    name: this.form.name, provider: this.form.provider, region: this.form.region,
                    access_key_id: this.form.access_key_id,
                    vpc_ids: this.vpcText.split(',').map(s => s.trim()).filter(Boolean),
                    remark: this.form.remark
                };
                if (this.form.access_key_secret) payload.access_key_secret = this.form.access_key_secret;
                this.saving = true;
                const p = this.form.id ? api.updateAccount(this.form.id, payload) : api.createAccount(payload);
                p.then(() => {
                    this.$message.success('已保存，建议立即测试连接');
                    this.dialog = false;
                    this.load();
                }).finally(() => { this.saving = false; });
            },
            test(row) {
                this.testingId = row.id;
                api.testAccount(row.id).then(r => {
                    if (r.ok) this.$message.success(r.message);
                    else this.$message.error(r.message);
                    this.load();
                }).finally(() => { this.testingId = null; });
            },
            sync(row) {
                this.syncingId = row.id;
                api.syncAccount(row.id).then(r => {
                    this.$message.success(r.message || '同步完成');
                    this.load();
                    this.$emit('refresh-apps');
                }).finally(() => { this.syncingId = null; });
            },
            toggleAccount(row, v) {
                api.updateAccount(row.id, { enabled: v }).then(() => { this.load(); });
            },
            remove(row) {
                this.$confirm(`删除云账号「${row.name}」及其下所有资源记录？`, '警告', { type: 'warning' }).then(() => {
                    return api.deleteAccount(row.id);
                }).then(() => {
                    this.$message.success('已删除');
                    this.load();
                    this.$emit('refresh-apps');
                }).catch(() => {});
            }
        },
        mounted() { this.load(); }
    };

    // ======================================================================
    // 用户管理
    // ======================================================================
    const Users = {
        props: ['me'],
        template: `
        <div>
            <h2 class="page-title">用户与权限</h2>
            <div class="oc-toolbar">
                <span class="text-muted" style="font-size:13px">角色：管理员（全部权限）/ 运维（开关机与策略）/ 只读（仅查看）</span>
                <div class="oc-spacer"></div>
                <el-button type="primary" :icon="Plus" @click="openCreate" :disabled="me.role !== 'admin'">新增用户</el-button>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="用户名" prop="username" width="150" />
                    <el-table-column label="姓名" prop="full_name" width="140" />
                    <el-table-column label="角色" width="110">
                        <template #default="{ row }">
                            <el-tag size="small" :type="roleTag(row.role)">{{ row.role_label }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="邮箱" prop="email" min-width="160" show-overflow-tooltip />
                    <el-table-column label="状态" width="90">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="最后登录" width="170">
                        <template #default="{ row }">{{ fmtTime(row.last_login_at) }}</template>
                    </el-table-column>
                    <el-table-column label="操作" width="200" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
                            <el-button link type="warning" size="small" @click="resetPwd(row)">重置密码</el-button>
                            <el-button link type="danger" size="small" @click="remove(row)" :disabled="row.username === me.username">删除</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <el-dialog v-model="dialog" :title="form.id ? '编辑用户' : '新增用户'" width="480px">
                <el-form :model="form" label-width="90px">
                    <el-form-item label="用户名"><el-input v-model="form.username" :disabled="!!form.id" /></el-form-item>
                    <el-form-item v-if="!form.id" label="密码"><el-input v-model="form.password" type="password" show-password placeholder="至少 8 位" /></el-form-item>
                    <el-form-item label="姓名"><el-input v-model="form.full_name" /></el-form-item>
                    <el-form-item label="角色">
                        <el-select v-model="form.role" style="width:100%">
                            <el-option v-for="r in roles" :key="r.value" :label="r.label" :value="r.value" />
                        </el-select>
                    </el-form-item>
                    <el-form-item label="邮箱"><el-input v-model="form.email" /></el-form-item>
                </el-form>
                <template #footer>
                    <el-button @click="dialog = false">取消</el-button>
                    <el-button type="primary" @click="save">保存</el-button>
                </template>
            </el-dialog>
        </div>`,
        data() {
            return { rows: [], roles: [], loading: false, dialog: false, form: this.blankForm() };
        },
        methods: {
            fmtTime,
            blankForm() { return { id: null, username: '', password: '', full_name: '', role: 'readonly', email: '' }; },
            roleTag(role) { return { admin: 'danger', operator: 'warning', readonly: 'info' }[role] || 'info'; },
            load() {
                this.loading = true;
                api.listUsers().then(r => { this.rows = r.items; this.roles = r.roles; })
                    .finally(() => { this.loading = false; });
            },
            openCreate() { this.form = this.blankForm(); this.dialog = true; },
            openEdit(row) {
                this.form = { id: row.id, username: row.username, password: '', full_name: row.full_name, role: row.role, email: row.email };
                this.dialog = true;
            },
            save() {
                const payload = { full_name: this.form.full_name, role: this.form.role, email: this.form.email };
                const p = this.form.id
                    ? api.updateUser(this.form.id, payload)
                    : api.createUser({ ...payload, username: this.form.username, password: this.form.password });
                p.then(() => { this.$message.success('已保存'); this.dialog = false; this.load(); });
            },
            resetPwd(row) {
                this.$prompt(`为「${row.username}」设置新密码（至少 8 位）`, '重置密码', {
                    inputType: 'password', inputPattern: /^.{8,}$/, inputErrorMessage: '至少 8 位'
                }).then(({ value }) => {
                    return api.resetPassword(row.id, value);
                }).then(() => { this.$message.success('密码已重置'); }).catch(() => {});
            },
            remove(row) {
                this.$confirm(`删除用户「${row.username}」？`, '警告', { type: 'warning' }).then(() => {
                    return api.deleteUser(row.id);
                }).then(() => { this.$message.success('已删除'); this.load(); }).catch(() => {});
            }
        },
        mounted() { this.load(); }
    };

    // ======================================================================
    // 审计日志
    // ======================================================================
    const Audit = {
        template: `
        <div>
            <h2 class="page-title">审计日志</h2>
            <div class="oc-toolbar">
                <el-input v-model="q.username" placeholder="操作人" clearable style="width:160px" @keyup.enter="load" @clear="load" />
                <el-input v-model="q.action" placeholder="动作（start/stop/login...）" clearable style="width:220px" @keyup.enter="load" @clear="load" />
                <el-button :icon="Search" @click="load">查询</el-button>
                <el-button :icon="Refresh" @click="load">刷新</el-button>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column label="操作人" prop="username" width="120" />
                    <el-table-column label="动作" prop="action" width="150" />
                    <el-table-column label="对象" prop="target" min-width="180" show-overflow-tooltip />
                    <el-table-column label="详情" prop="detail" min-width="200" show-overflow-tooltip />
                    <el-table-column label="IP" prop="client_ip" width="130" />
                    <el-table-column label="结果" width="80">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.result === 'success' ? 'success' : 'danger'">{{ row.result }}</el-tag>
                        </template>
                    </el-table-column>
                </el-table>
            </div>
        </div>`,
        data() {
            return { rows: [], loading: false, q: { username: '', action: '' } };
        },
        methods: {
            fmtTime,
            load() {
                this.loading = true;
                api.auditLogs({ username: this.q.username || undefined, action: this.q.action || undefined, limit: 300 })
                    .then(r => { this.rows = r.items; })
                    .finally(() => { this.loading = false; });
            }
        },
        mounted() { this.load(); }
    };

    global.OCComponents = { Overview, Resources, Operations, Schedules, Accounts, Users, Audit };
})(window);
