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
                <el-col :span="8">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-primary">{{ summary.total }}</div>
                        <div class="stat-label">资源总数</div>
                    </div>
                </el-col>
                <el-col :span="8">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-success">{{ runningTotal }}</div>
                        <div class="stat-label">运行中</div>
                    </div>
                </el-col>
                <el-col :span="8">
                    <div class="oc-card stat-card">
                        <div class="stat-value text-danger">{{ stoppedTotal }}</div>
                        <div class="stat-label">已停止</div>
                    </div>
                </el-col>
            </el-row>

            <div style="margin-top:16px">
                <h3 style="margin:0 0 12px;font-size:15px">
                    按应用
                    <span class="text-muted" style="font-size:12px;font-weight:normal;margin-left:8px">共 {{ Object.keys(appStats).length }} 个应用</span>
                </h3>
                <div v-loading="loadingApps" style="display:flex;flex-wrap:wrap;gap:10px">
                    <div v-for="(v, name) in appStats" :key="name" class="app-block"
                         style="flex:0 0 calc(12.5% - 9px);min-width:120px">
                        <div class="app-name" :title="name">{{ name }}</div>
                        <div class="app-sub">
                            <span class="text-muted">共 {{ v.total }} · </span>
                            <span class="text-success">运行 {{ v.running }}</span>
                            <span class="text-muted"> · </span>
                            <span class="text-danger">停止 {{ v.stopped }}</span>
                        </div>
                    </div>
                </div>
                <el-empty v-if="!loadingApps && !Object.keys(appStats).length" description="暂无资源" :image-size="60" />
            </div>
        </div>`,
        data() {
            return {
                summary: { by_type: {}, stop_saving_count: 0, total: 0 },
                appStats: {}, loadingApps: false
            };
        },
        computed: {
            runningTotal() { return this.sumTotals('running'); },
            stoppedTotal() { return this.sumTotals('stopped'); }
        },
        methods: {
            pct(n, total) { return total ? Math.round(n / total * 100) : 0; },
            sumTotals(k) {
                return Object.values(this.summary.by_type || {}).reduce((s, v) => s + (v[k] || 0), 0);
            },
            load() {
                api.summary({ app_id: this.appId }).then(r => { this.summary = r; });
                this.loadAppStats();
            },
            loadAppStats() {
                this.loadingApps = true;
                const map = {};
                const push = (items) => items.forEach(it => {
                    const name = it.app_name || '未分类';
                    if (!map[name]) map[name] = { total: 0, running: 0, stopped: 0 };
                    map[name].total += 1;
                    if (it.power_state === 'running') map[name].running += 1;
                    else if (it.power_state === 'stopped') map[name].stopped += 1;
                });
                const collect = (page, acc) => api.listResources({
                    app_id: this.appId, page, page_size: 500
                }).then(r => {
                    acc.push(...r.items);
                    if (r.items.length < r.page_size || page * r.page_size >= r.total || page >= 20) return acc;
                    return collect(page + 1, acc);
                });
                collect(1, []).then(items => { push(items); this.appStats = map; })
                    .finally(() => { this.loadingApps = false; });
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
                    <el-table-column label="实例规格" min-width="150" show-overflow-tooltip>
                        <template #default="{ row }">
                            <span :title="row.engine_version ? '引擎版本：' + row.engine_version : ''">{{ row.spec || '-' }}</span>
                        </template>
                    </el-table-column>
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
    // 执行记录（统一流水：手动/定时/自动、开机/关机）
    // ======================================================================
    const Operations = {
        props: ['appId', 'me'],
        emits: ['go'],
        template: `
        <div>
            <h2 class="page-title">执行记录</h2>

            <!-- 统一流水：手动/定时/自动、开机/关机 均在此可见 -->
            <div class="oc-section-title" style="display:flex;align-items:center;margin:4px 0 10px">
                <span>共 {{ total }} 条</span>
                <div class="oc-spacer"></div>
                <el-select v-model="filterAction" size="small" style="width:110px;margin-right:8px" placeholder="动作" @change="onFilterChange">
                    <el-option label="全部动作" value="" />
                    <el-option label="开机" value="start" />
                    <el-option label="节省关机" value="stop" />
                </el-select>
                <el-select v-model="filterTrigger" size="small" style="width:120px" placeholder="来源" @change="onFilterChange">
                    <el-option label="全部来源" value="" />
                    <el-option label="手动" value="manual" />
                    <el-option label="定时" value="schedule" />
                    <el-option label="自动" value="auto" />
                </el-select>
                <el-button :icon="Refresh" size="small" @click="loadAll">刷新</el-button>
            </div>
            <div class="oc-card task-cell-wrap" style="padding:8px 0">
                <el-table :data="filteredRows" v-loading="loadingAll" border size="small" stripe>
                    <el-table-column label="ID" prop="id" width="70" />
                    <el-table-column label="执行时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.started_at || row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column label="动作" width="90">
                        <template #default="{ row }"><el-tag size="small" :type="row.action === 'start' ? 'success' : 'danger'">{{ row.action_label }}</el-tag></template>
                    </el-table-column>
                    <el-table-column label="来源" width="80">
                        <template #default="{ row }">{{ row.trigger === 'schedule' ? '定时' : row.trigger === 'auto' ? '自动' : '手动' }}</template>
                    </el-table-column>
                    <el-table-column label="策略" prop="policy_name" width="150" show-overflow-tooltip />
                    <el-table-column label="目标（实例）" min-width="260">
                        <template #default="{ row }">
                            <div v-if="(row.target_instances || []).length">
                                <div>{{ row.target_instances.map(i => i.name).join('、') }}</div>
                                <div class="text-muted" style="font-size:12px;margin-top:2px">总计 {{ row.total || row.target_instances.length }} 台实例</div>
                            </div>
                            <span v-else>—</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="操作人" width="150">
                        <template #default="{ row }">
                            <span v-if="row.trigger === 'schedule'">{{ row.policy_name || row.operator }}</span>
                            <span v-else>{{ row.operator }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="状态" width="100">
                        <template #default="{ row }"><el-tag size="small" :type="(taskStatusMeta[row.status] || {}).type">{{ (taskStatusMeta[row.status] || {}).text }}</el-tag></template>
                    </el-table-column>
                    <el-table-column label="成功/失败/跳过" width="130">
                        <template #default="{ row }">
                            <span class="text-success">{{ row.succeed }}</span> /
                            <span class="text-danger">{{ row.failed }}</span> /
                            <span class="text-muted">{{ row.skipped }}</span>
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="80" fixed="right">
                        <template #default="{ row }"><el-button link type="primary" size="small" @click="openDetail(row)">详情</el-button></template>
                    </el-table-column>
                </el-table>
                <el-empty v-if="!loadingAll && !allRows.length" description="暂无执行记录" :image-size="60" />
                <el-pagination
                    layout="total, prev, pager, next"
                    :total="total"
                    :page-size="pageSize"
                    :current-page="page"
                    @current-change="onPage"
                    small
                    style="margin-top:12px;justify-content:flex-end"
                />
            </div>

            <!-- 详情抽屉 -->
            <el-drawer v-model="detailVisible" :title="'执行记录 #' + (detail.id || '') + ' 明细'" size="60%">
                <div v-if="detail.id">
                    <el-descriptions :column="3" border size="small" style="margin-bottom:16px">
                        <el-descriptions-item label="状态">
                            <el-tag size="small" :type="detail.session_status === 'running' ? 'success' : 'info'">
                                {{ detail.session_status === 'running' ? '运行中' : '已关闭' }}
                            </el-tag>
                        </el-descriptions-item>
                        <el-descriptions-item label="触发">{{ detail.trigger === 'schedule' ? '定时' : '手动' }}</el-descriptions-item>
                        <el-descriptions-item label="操作人">{{ detail.trigger === 'schedule' ? (detail.policy_name || detail.operator) : detail.operator }}</el-descriptions-item>
                        <el-descriptions-item label="开机时间">{{ fmtTime(detail.started_at || detail.created_at) }}</el-descriptions-item>
                        <el-descriptions-item label="关机时间">{{ detail.closed_at ? fmtTime(detail.closed_at) : '—' }}</el-descriptions-item>
                        <el-descriptions-item label="结果（成/败/跳）">{{ detail.succeed }}/{{ detail.failed }}/{{ detail.skipped }}</el-descriptions-item>
                    </el-descriptions>

                    <div style="margin-bottom:12px;display:flex;gap:10px;align-items:center">
                        <el-button v-if="detail.session_status === 'running'" type="danger" size="small"
                                   :icon="SwitchButton" :disabled="me.role === 'readonly'" @click="closeOne(detail)">一键关机</el-button>
                        <span class="text-muted" style="font-size:12px">目标实例：{{ detail.instance_names || '—' }}</span>
                    </div>

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

                    <template v-if="detail.close_task">
                        <h4 style="margin:16px 0 8px">一键关机结果（关机任务 #{{ detail.close_task.id }}）</h4>
                        <el-table :data="detail.close_task.items || []" border size="small" stripe>
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
                    </template>
                </div>
            </el-drawer>
        </div>`,
        data() {
            return {
                allRows: [],
                loadingAll: false,
                filterAction: '', filterTrigger: '',
                page: 1, pageSize: 20, total: 0,
                taskStatusMeta, itemStatusMeta,
                detailVisible: false, detail: {},
            };
        },
        computed: {
            filteredRows() {
                // 过滤已在后端按 action/trigger 完成，这里直接透传当前页数据
                return this.allRows;
            }
        },
        methods: {
            fmtTime,
            loadAll() {
                this.loadingAll = true;
                const p = { page: this.page, page_size: this.pageSize };
                if (this.filterAction) p.action = this.filterAction;
                if (this.filterTrigger) p.trigger = this.filterTrigger;
                api.listTasks(p)
                    .then(r => { this.allRows = r.items || []; this.total = r.total || 0; })
                    .catch(err => { this.$message.error('执行记录加载失败：' + (err.message || err)); })
                    .finally(() => { this.loadingAll = false; });
            },
            onFilterChange() { this.page = 1; this.loadAll(); },
            onPage(p) { this.page = p; this.loadAll(); },
            load() { this.loadAll(); },
            openDetail(row) {
                api.getTask(row.id).then(r => { this.detail = r; this.detailVisible = true; });
            },
            closeOne(row) {
                const n = (row.target_instances || []).length;
                this.$confirm(
                    '确定对开机会话 #' + row.id + ' 下的 ' + n + ' 个实例执行【节省关机】吗？关机后该会话将归档。',
                    '一键关机', { type: 'warning', confirmButtonText: '确定关机', cancelButtonText: '取消' }
                ).then(() => api.closeTask(row.id))
                 .then(() => {
                     this.$message.success('已发起节省关机，会话已归档');
                     this.load();
                 }).catch(() => {});
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
                    <el-table-column label="终止期限" width="180">
                        <template #default="{ row }">
                            <span v-if="row.end_date">{{ fmtTime(row.end_date) }}</span>
                            <span v-else class="text-muted">长期有效</span>
                            <div v-if="row.end_date && isExpired(row.end_date)" style="font-size:12px;margin-top:2px"><el-tag size="small" type="info">已过期·已停用</el-tag></div>
                        </template>
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
                    <el-table-column label="操作" width="280" fixed="right">
                        <template #default="{ row }">
                            <el-button link type="primary" size="small" @click="runNow(row)">立即执行</el-button>
                            <el-button link type="primary" size="small" @click="openHistory(row)">执行历史</el-button>
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
                    <el-form-item label="cron 模式">
                        <el-radio-group v-model="form.cronMode">
                            <el-radio-button value="simple">下拉选择</el-radio-button>
                            <el-radio-button value="advanced">高级表达式</el-radio-button>
                        </el-radio-group>
                    </el-form-item>
                    <el-form-item label="执行时间" v-if="form.cronMode === 'simple'">
                        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                            <span class="text-muted">分</span><el-select v-model="form.cronMin" style="width:86px"><el-option v-for="o in minuteOptions" :key="o.v" :label="o.l" :value="o.v" /></el-select>
                            <span class="text-muted">时</span><el-select v-model="form.cronHour" style="width:86px"><el-option v-for="o in hourOptions" :key="o.v" :label="o.l" :value="o.v" /></el-select>
                            <span class="text-muted">日</span><el-select v-model="form.cronDay" style="width:86px"><el-option v-for="o in dayOptions" :key="o.v" :label="o.l" :value="o.v" /></el-select>
                            <span class="text-muted">月</span><el-select v-model="form.cronMonth" style="width:86px"><el-option v-for="o in monthOptions" :key="o.v" :label="o.l" :value="o.v" /></el-select>
                            <span class="text-muted">周</span><el-select v-model="form.cronDow" style="width:120px"><el-option v-for="o in dowOptions" :key="o.v" :label="o.l" :value="o.v" /></el-select>
                        </div>
                        <div class="text-muted" style="font-size:12px;margin-top:4px">当前表达式：<span class="mono">{{ buildCron() }}</span>（{{ fmtCron(buildCron()) }}）</div>
                    </el-form-item>
                    <el-form-item label="cron 表达式" v-else>
                        <el-input v-model="form.cron_expr" placeholder="0 9 * * 1-5">
                            <template #append>Asia/Shanghai</template>
                        </el-input>
                        <div class="text-muted" style="font-size:12px;margin-top:4px">当前解析：{{ fmtCron(form.cron_expr) }}</div>
                    </el-form-item>
                    <el-form-item label="终止期限">
                        <el-date-picker v-model="form.end_date" type="datetime"
                            value-format="YYYY-MM-DD HH:mm:ss" format="YYYY-MM-DD HH:mm:ss"
                            placeholder="留空 = 长期有效" :clearable="true" style="width:100%" />
                        <div class="text-muted" style="font-size:12px;margin-top:4px">到期后将自动停用该策略；留空表示长期有效</div>
                    </el-form-item>
                    <el-form-item label="备注"><el-input v-model="form.remark" type="textarea" :rows="2" /></el-form-item>
                </el-form>
                <template #footer>
                    <el-button @click="dialog = false">取消</el-button>
                    <el-button type="primary" @click="save">保存</el-button>
                </template>
            </el-dialog>

            <el-dialog v-model="historyVisible" :title="'执行历史 · ' + historyPolicyName" width="860px">
                <el-table :data="historyRows" v-loading="historyLoading" border size="small" stripe>
                    <el-table-column label="任务ID" prop="id" width="80" />
                    <el-table-column label="执行时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.started_at || row.created_at) }}</template>
                    </el-table-column>
                    <el-table-column label="动作" width="90">
                        <template #default="{ row }"><el-tag size="small" :type="row.action === 'start' ? 'success' : 'danger'">{{ row.action_label }}</el-tag></template>
                    </el-table-column>
                    <el-table-column label="来源" width="80">
                        <template #default="{ row }">{{ row.trigger === 'schedule' ? '定时' : row.trigger === 'auto' ? '自动' : '手动' }}</template>
                    </el-table-column>
                    <el-table-column label="状态" width="100">
                        <template #default="{ row }"><el-tag size="small" :type="(taskStatusMeta[row.status] || {}).type">{{ (taskStatusMeta[row.status] || {}).text }}</el-tag></template>
                    </el-table-column>
                    <el-table-column label="成功/失败/跳过" width="130">
                        <template #default="{ row }"><span class="text-success">{{ row.succeed }}</span> / <span class="text-danger">{{ row.failed }}</span> / <span class="text-muted">{{ row.skipped }}</span></template>
                    </el-table-column>
                    <el-table-column label="操作" width="80">
                        <template #default="{ row }"><el-button link type="primary" size="small" @click="openHistoryDetail(row)">详情</el-button></template>
                    </el-table-column>
                </el-table>
                <el-empty v-if="!historyLoading && !historyRows.length" description="该策略暂无执行记录" :image-size="60" />
            </el-dialog>

            <el-drawer v-model="historyDetailVisible" :title="'任务 #' + (historyDetail.id || '') + ' 明细'" size="60%">
                <div v-if="historyDetail.id">
                    <el-descriptions :column="3" border size="small" style="margin-bottom:16px">
                        <el-descriptions-item label="状态"><el-tag size="small" :type="(taskStatusMeta[historyDetail.status] || {}).type">{{ (taskStatusMeta[historyDetail.status] || {}).text }}</el-tag></el-descriptions-item>
                        <el-descriptions-item label="动作">{{ historyDetail.action_label }}</el-descriptions-item>
                        <el-descriptions-item label="来源">{{ historyDetail.trigger === 'schedule' ? '定时' : historyDetail.trigger === 'auto' ? '自动' : '手动' }}</el-descriptions-item>
                        <el-descriptions-item label="操作人">{{ historyDetail.operator }}</el-descriptions-item>
                        <el-descriptions-item label="执行时间">{{ fmtTime(historyDetail.started_at || historyDetail.created_at) }}</el-descriptions-item>
                        <el-descriptions-item label="结果（成/败/跳）">{{ historyDetail.succeed }}/{{ historyDetail.failed }}/{{ historyDetail.skipped }}</el-descriptions-item>
                    </el-descriptions>
                    <el-table :data="historyDetail.items || []" border size="small" stripe>
                        <el-table-column label="资源" prop="resource_name" min-width="180" show-overflow-tooltip />
                        <el-table-column label="类型" prop="resource_type" width="70" />
                        <el-table-column label="账号" prop="account_name" width="120" show-overflow-tooltip />
                        <el-table-column label="状态" width="90">
                            <template #default="{ row }"><el-tag size="small" :type="(itemStatusMeta[row.status] || {}).type">{{ (itemStatusMeta[row.status] || {}).text }}</el-tag></template>
                        </el-table-column>
                        <el-table-column label="结果" prop="message" min-width="220" show-overflow-tooltip />
                    </el-table>
                </div>
            </el-drawer>
        </div>`,
        data() {
            return {
                rows: [], logs: [], apps: [], resourceOptions: [], loading: false,
                taskStatusMeta, itemStatusMeta, dialog: false,
                historyVisible: false, historyRows: [], historyLoading: false, historyPolicyName: '',
                historyDetailVisible: false, historyDetail: {},
                form: this.blankForm(),
                minuteOptions: [{ v: '*', l: '每分' }].concat(
                    Array.from({ length: 60 }, (_, i) => { const s = String(i).padStart(2, '0'); return { v: s, l: s }; })),
                hourOptions: [{ v: '*', l: '每时' }].concat(
                    Array.from({ length: 24 }, (_, i) => { const s = String(i).padStart(2, '0'); return { v: s, l: s }; })),
                dayOptions: [{ v: '*', l: '每天' }].concat(
                    Array.from({ length: 31 }, (_, i) => { const s = String(i + 1); return { v: s, l: s }; })),
                monthOptions: [{ v: '*', l: '每月' }].concat(
                    Array.from({ length: 12 }, (_, i) => { const s = String(i + 1); return { v: s, l: s }; })),
                dowOptions: [
                    { v: '*', l: '每天' },
                    { v: '1-5', l: '工作日(周一~周五)' },
                    { v: '1', l: '周一' }, { v: '2', l: '周二' }, { v: '3', l: '周三' },
                    { v: '4', l: '周四' }, { v: '5', l: '周五' }, { v: '6', l: '周六' }, { v: '0', l: '周日' },
                ],
            };
        },
        methods: {
            fmtTime, fmtCron,
            buildCron() {
                return `${this.form.cronMin} ${this.form.cronHour} ${this.form.cronDay} ${this.form.cronMonth} ${this.form.cronDow}`;
            },
            isExpired(endDate) {
                const d = new Date(String(endDate).replace(' ', 'T'));
                return !!d && d < new Date();
            },
            blankForm() {
                return {
                    id: null, name: '', action: 'start', scope: 'app',
                    target_app_id: null, target_resource_ids: [],
                    cronMode: 'simple',
                    cronMin: '0', cronHour: '9', cronDay: '*', cronMonth: '*', cronDow: '*',
                    cron_expr: '0 9 * * *',
                    end_date: null, remark: ''
                };
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
                const segs = (row.cron_expr || '* * * * *').trim().split(/\s+/);
                const isSimple = segs.length === 5 && segs.every(s => /^(\*|\d+|\d+-\d+)$/.test(s));
                this.form = {
                    id: row.id, name: row.name, action: row.action, scope: row.scope,
                    target_app_id: row.target_app_id, target_resource_ids: row.target_resource_ids || [],
                    cronMode: isSimple ? 'simple' : 'advanced',
                    cronMin: segs[0] || '*', cronHour: segs[1] || '*',
                    cronDay: segs[2] || '*', cronMonth: segs[3] || '*', cronDow: segs[4] || '*',
                    cron_expr: row.cron_expr,
                    end_date: row.end_date ? String(row.end_date).replace('T', ' ').slice(0, 19) : null,
                    remark: row.remark
                };
                this.dialog = true;
            },
            save() {
                const cron = this.form.cronMode === 'simple'
                    ? `${this.form.cronMin} ${this.form.cronHour} ${this.form.cronDay} ${this.form.cronMonth} ${this.form.cronDow}`
                    : (this.form.cron_expr || '').trim();
                const payload = {
                    name: this.form.name, action: this.form.action, scope: this.form.scope,
                    cron_expr: cron, remark: this.form.remark,
                    end_date: this.form.end_date || null
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
            },
            openHistory(row) {
                this.historyPolicyName = row.name;
                this.historyVisible = true;
                this.historyLoading = true;
                api.listTasks({ policy_id: row.id, page_size: 200 })
                    .then(r => { this.historyRows = r.items; })
                    .catch(err => { this.$message.error('执行历史加载失败：' + (err.message || err)); })
                    .finally(() => { this.historyLoading = false; });
            },
            openHistoryDetail(row) {
                api.getTask(row.id).then(r => { this.historyDetail = r; this.historyDetailVisible = true; });
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

    // ---------------------------------------------------------------------------
    // 系统设置（首个功能：每日自动更新资源时间点，可配置多个，保存即生效）
    // ---------------------------------------------------------------------------
    const SystemSettings = {
        props: ['me'],
        data() {
            return {
                times: ['09:00'],
                weekdays_only: true,
                loading: false,
                saving: false,
                nextRuns: []
            };
        },
        methods: {
            load() {
                this.loading = true;
                api.getSyncCron().then(r => {
                    this.times = (r.times && r.times.length) ? r.times.slice() : ['09:00'];
                    this.weekdays_only = r.weekdays_only !== false;
                    this.nextRuns = r.next_runs || [];
                }).catch(() => {}).finally(() => { this.loading = false; });
            },
            addTime() { this.times.push('09:00'); },
            removeTime(i) { this.times.splice(i, 1); },
            save() {
                if (this.me && this.me.role === 'readonly') {
                    ElementPlus.ElMessage.warning('只读角色不可修改');
                    return;
                }
                if (!this.times.length) {
                    ElementPlus.ElMessage.warning('请至少保留一个时间点');
                    return;
                }
                this.saving = true;
                api.saveSyncCron({ times: this.times, weekdays_only: this.weekdays_only })
                    .then(r => {
                        this.nextRuns = r.next_runs || [];
                        ElementPlus.ElMessage.success('已保存，自动更新调度已立即生效');
                    })
                    .finally(() => { this.saving = false; });
            }
        },
        mounted() { this.load(); },
        template: `
        <div>
            <h2 class="page-title">系统设置</h2>
            <el-card class="oc-card" v-loading="loading">
                <template #header><b>每日自动更新资源</b></template>
                <el-alert type="info" :closable="false" show-icon style="margin-bottom:16px"
                    title="平台将按下列时间点的每日自动同步各云账号的 ECS / RDS 资源状态（全量）。保存后立即生效，无需重启。" />
                <el-form label-width="110px" label-position="right">
                    <el-form-item label="更新时间点">
                        <div style="width:100%">
                            <div v-for="(t, i) in times" :key="i"
                                 style="display:flex;gap:8px;margin-bottom:8px;align-items:center">
                                <el-time-picker v-model="times[i]" value-format="HH:mm" format="HH:mm"
                                    placeholder="选择时间" style="width:170px" :disabled="saving" />
                                <el-button v-if="times.length > 1" circle @click="removeTime(i)" :disabled="saving">
                                    <el-icon><Delete /></el-icon>
                                </el-button>
                            </div>
                            <el-button @click="addTime" :disabled="saving">
                                <el-icon><Plus /></el-icon> 添加时间点
                            </el-button>
                        </div>
                    </el-form-item>
                    <el-form-item label="仅工作日">
                        <el-switch v-model="weekdays_only" :disabled="saving" />
                        <span class="text-muted" style="margin-left:10px">开启后仅周一至周五更新（周末不更新）</span>
                    </el-form-item>
                    <el-form-item>
                        <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
                        <span v-if="me && me.role === 'readonly'" class="text-muted" style="margin-left:10px">只读角色不可修改</span>
                    </el-form-item>
                </el-form>
                <div v-if="nextRuns.length" style="margin-top:8px">
                    <div class="text-muted" style="margin-bottom:8px">下次执行时间（北京时间）：</div>
                    <el-tag v-for="(r, i) in nextRuns" :key="i" style="margin:0 8px 8px 0">{{ r }}</el-tag>
                </div>
            </el-card>
        </div>
        `
    };

    // ======================================================================
    // 已释放资源（云上已删除，仅留记录可追溯，无操作按钮）
    // ======================================================================
    const ReleasedResources = {
        props: ['appId', 'env'],
        template: `
        <div>
            <h2 class="page-title">已释放资源
                <el-tag size="small" type="info" style="margin-left:8px">{{ total }} 条记录</el-tag>
            </h2>
            <el-alert type="info" :closable="false" show-icon
                      title="以下资源在云上已被删除，自动从「资源清单」移出并保留在此记录释放时间，仅供追溯，不可操作。"
                      style="margin-bottom:12px" />
            <div class="oc-toolbar">
                <el-input v-model="q.keyword" placeholder="实例名 / ID 搜索" clearable style="width:220px"
                          @keyup.enter="load(1)" @clear="load(1)" />
                <el-button :icon="Search" @click="load(1)">查询</el-button>
                <div class="oc-spacer"></div>
                <span class="text-muted">按释放时间倒序</span>
            </div>

            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe empty-text="暂无已释放资源">
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
                    <el-table-column label="状态" width="100">
                        <template #default="{ row }">
                            <el-tag size="small" type="warning">下线归档</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="应用" prop="app_name" width="140" show-overflow-tooltip />
                    <el-table-column label="实例ID" prop="resource_id" min-width="160" show-overflow-tooltip />
                    <el-table-column label="释放时间" width="170">
                        <template #default="{ row }">{{ fmtTime(row.released_at) }}</template>
                    </el-table-column>
                    <el-table-column label="上次同步" width="170">
                        <template #default="{ row }">{{ fmtTime(row.last_sync_at) }}</template>
                    </el-table-column>
                </el-table>
            </div>

            <div style="display:flex;justify-content:flex-end;align-items:center">
                <span class="text-muted" style="margin-right:12px">共 {{ total }} 条</span>
                <el-pagination layout="prev, pager, next" :total="total" :page-size="q.page_size"
                               :current-page="q.page" @current-change="p => { q.page = p; load(); }" />
            </div>
        </div>`,
        data() {
            return {
                rows: [], total: 0, loading: false,
                q: { keyword: '', page: 1, page_size: 50 }
            };
        },
        methods: {
            fmtTime,
            load(page) {
                if (page) this.q.page = page;
                this.loading = true;
                api.listResources({
                    only_deleted: true,
                    keyword: this.q.keyword || undefined,
                    page: this.q.page, page_size: this.q.page_size
                }).then(r => {
                    // 按释放时间倒序，最近释放的排在最前
                    const items = (r.items || []).slice().sort((a, b) => {
                        const ta = a.released_at ? String(a.released_at) : '';
                        const tb = b.released_at ? String(b.released_at) : '';
                        return tb.localeCompare(ta);
                    });
                    this.rows = items;
                    this.total = r.total;
                }).finally(() => { this.loading = false; });
            }
        },
        mounted() { this.load(); }
    };

    // ======================================================================
    // 应用管理（手动维护应用列表：停用/启用/新建）
    // ======================================================================
    const AppManager = {
        props: ['me'],
        template: `
        <div>
            <h2 class="page-title">应用管理</h2>
            <el-alert type="info" :closable="false" show-icon style="margin-bottom:12px"
                      title="停用（软删除）的应用不计入概览与资源筛选，其资源回退到按实例名自动解析的归属；可在本页重新启用。" />
            <div class="oc-toolbar">
                <el-button type="primary" @click="openCreate">新建应用</el-button>
                <div class="oc-spacer"></div>
                <span class="text-muted">共 {{ rows.length }} 个应用（含已停用）</span>
            </div>
            <div class="oc-card" style="padding:8px 0">
                <el-table :data="rows" v-loading="loading" border size="small" stripe>
                    <el-table-column label="应用名称" prop="name" min-width="170" show-overflow-tooltip />
                    <el-table-column label="编码" prop="code" width="140" show-overflow-tooltip />
                    <el-table-column label="负责人" prop="owner" width="110" />
                    <el-table-column label="资源数" width="90" align="center">
                        <template #default="{ row }">{{ row.stats.total }}</template>
                    </el-table-column>
                    <el-table-column label="运行中" width="80" align="center">
                        <template #default="{ row }"><span class="text-success">{{ row.stats.running }}</span></template>
                    </el-table-column>
                    <el-table-column label="已停止" width="80" align="center">
                        <template #default="{ row }"><span class="text-danger">{{ row.stats.stopped }}</span></template>
                    </el-table-column>
                    <el-table-column label="状态" width="100">
                        <template #default="{ row }">
                            <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '已停用' }}</el-tag>
                        </template>
                    </el-table-column>
                    <el-table-column label="操作" width="160" fixed="right">
                        <template #default="{ row }">
                            <el-button v-if="row.enabled" link type="danger" size="small" @click="disable(row)">停用</el-button>
                            <el-button v-else link type="success" size="small" @click="enable(row)">启用</el-button>
                        </template>
                    </el-table-column>
                </el-table>
            </div>

            <el-dialog v-model="createDialog" title="新建应用" width="460px">
                <el-form label-width="80px">
                    <el-form-item label="应用名称" required>
                        <el-input v-model="form.name" maxlength="128" placeholder="如 APC-DEMO" />
                    </el-form-item>
                    <el-form-item label="编码" required>
                        <el-input v-model="form.code" maxlength="128" placeholder="唯一，如 DEMO" />
                    </el-form-item>
                    <el-form-item label="负责人">
                        <el-input v-model="form.owner" maxlength="64" />
                    </el-form-item>
                </el-form>
                <template #footer>
                    <el-button @click="createDialog = false">取消</el-button>
                    <el-button type="primary" :loading="saving" @click="create">确定</el-button>
                </template>
            </el-dialog>
        </div>`,
        data() {
            return {
                rows: [], loading: false,
                createDialog: false, saving: false,
                form: { name: '', code: '', owner: '' }
            };
        },
        methods: {
            load() {
                this.loading = true;
                api.listApps().then(r => { this.rows = r.items; })
                    .finally(() => { this.loading = false; });
            },
            disable(row) {
                this.$confirm(
                    `确认停用应用「${row.name}」？其下资源将回退到按实例名自动解析的归属，不再计入概览。`,
                    '停用应用', { type: 'warning' }
                ).then(() => api.deleteApp(row.id))
                 .then(() => { this.$message.success('已停用'); this.load(); })
                 .catch(() => {});
            },
            enable(row) {
                api.updateApp(row.id, { enabled: true })
                    .then(() => { this.$message.success('已启用'); this.load(); });
            },
            openCreate() { this.form = { name: '', code: '', owner: '' }; this.createDialog = true; },
            create() {
                if (!this.form.name || !this.form.code) {
                    this.$message.warning('应用名称和编码必填');
                    return;
                }
                this.saving = true;
                api.createApp({ name: this.form.name, code: this.form.code, owner: this.form.owner, enabled: true })
                    .then(() => { this.$message.success('已创建应用'); this.createDialog = false; this.load(); })
                    .finally(() => { this.saving = false; });
            }
        },
        mounted() { this.load(); }
    };

    global.OCComponents = { Overview, Resources, ReleasedResources, Operations, Schedules, Accounts, Users, Audit, SystemSettings, AppManager };
})(window);
