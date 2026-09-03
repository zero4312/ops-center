#!/usr/bin/env bash
# =============================================================================
# ops-center 运维中台 - 统一部署脚本
# 同时适用于：开发本机（macOS / Linux）与云服务器（Linux）
#
# 常用命令：
#   ./deploy.sh install      安装依赖 + 初始化数据库 + 创建管理员（首次部署）
#   ./deploy.sh start        启动服务（后台常驻）
#   ./deploy.sh stop         停止服务
#   ./deploy.sh restart      重启服务
#   ./deploy.sh status       查看运行状态
#   ./deploy.sh logs         实时查看日志
#   ./deploy.sh update       拉取代码 + 更新依赖 + 重启（升级用）
#   ./deploy.sh mysql-up     用 Docker 快速起一个 MySQL（本机无数据库时用）
#   ./deploy.sh db-init      仅初始化/升级数据库表结构
#   ./deploy.sh reset-admin  重置管理员密码
#   ./deploy.sh service      安装为系统服务（Linux: systemd / macOS: launchd）
#
# 全部配置集中在 .env 文件，首次运行会自动从 .env.example 生成。
# =============================================================================
set -euo pipefail

APP_NAME="ops-center"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

VENV_DIR="$PROJECT_DIR/.venv"
RUN_DIR="$PROJECT_DIR/run"
LOG_DIR="$PROJECT_DIR/logs"
DATA_DIR="$PROJECT_DIR/data"
PID_FILE="$RUN_DIR/$APP_NAME.pid"
LOG_FILE="$LOG_DIR/$APP_NAME.log"
ENV_FILE="$PROJECT_DIR/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

# -----------------------------------------------------------------------------
# 基础环境
# -----------------------------------------------------------------------------
detect_python() {
    # 优先使用受管 Python，其次系统 python3；要求 >= 3.9
    for cand in "${PYTHON_BIN:-}" python3.11 python3.12 python3.13 python3; do
        [ -n "$cand" ] || continue
        if command -v "$cand" >/dev/null 2>&1; then
            local ver
            ver="$("$cand" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo 0)"
            local major minor
            major="${ver%%.*}"; minor="${ver##*.}"
            if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
                PYTHON_BIN="$(command -v "$cand")"
                return 0
            fi
        fi
    done
    log_error "未找到 Python >= 3.9，请先安装"
    exit 1
}

prepare_dirs() {
    mkdir -p "$RUN_DIR" "$LOG_DIR" "$DATA_DIR"
}

setup_env_file() {
    if [ ! -f "$ENV_FILE" ]; then
        if [ -f "$PROJECT_DIR/.env.example" ]; then
            cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
            chmod 600 "$ENV_FILE"
            log_warn "已自动生成 .env（来自 .env.example），请按需修改："
            log_warn "  vi $ENV_FILE"
        else
            log_error "缺少 .env.example，无法生成 .env"
            exit 1
        fi
    fi
    # 加载环境变量（export 到当前 shell）
    set -a
    # shellcheck disable=SC1090
    [ -f "$ENV_FILE" ] && . "$ENV_FILE"
    set +a
}

create_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        log_step "创建 Python 虚拟环境 $VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    VENV_PY="$VENV_DIR/bin/python"
    VENV_PIP="$VENV_DIR/bin/pip"
}

install_deps() {
    log_step "安装 / 更新 Python 依赖"
    "$VENV_PIP" install --quiet --upgrade pip
    # 国内云服务器可用镜像提速：取消下行注释
    # "$VENV_PIP" install --quiet -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    "$VENV_PIP" install --quiet -r requirements.txt
    log_info "依赖安装完成"
}

# -----------------------------------------------------------------------------
# 数据库
# -----------------------------------------------------------------------------
mysql_up() {
    if ! command -v docker >/dev/null 2>&1; then
        log_error "未检测到 docker，无法自动拉起 MySQL"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker daemon 未运行，请先启动 Docker Desktop"
        exit 1
    fi
    if docker ps -a --format '{{.Names}}' | grep -qx 'ops-center-mysql'; then
        log_info "MySQL 容器已存在，启动之"
        docker start ops-center-mysql >/dev/null
    else
        log_step "创建 MySQL 8 容器 ops-center-mysql"
        docker run -d --name ops-center-mysql \
            -e MYSQL_ROOT_PASSWORD=rootpass123 \
            -e MYSQL_DATABASE=ops_center \
            -e MYSQL_USER=opscenter \
            -e MYSQL_PASSWORD=opscenter123 \
            -e TZ=Asia/Shanghai \
            -p 3306:3306 \
            --restart unless-stopped \
            mysql:8.0 --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null
    fi
    log_info "等待 MySQL 就绪"
    for i in $(seq 1 60); do
        if docker exec ops-center-mysql mysqladmin ping -h 127.0.0.1 --silent 2>/dev/null; then
            log_info "MySQL 已就绪 (127.0.0.1:3306, db=ops_center, user=opscenter)"
            log_info "请确保 .env 中 OPS_DATABASE_URL=mysql+pymysql://opscenter:opscenter123@127.0.0.1:3306/ops_center?charset=utf8mb4"
            return 0
        fi
        sleep 2
    done
    log_error "MySQL 启动超时，请检查容器日志：docker logs ops-center-mysql"
    exit 1
}

db_init() {
    log_step "初始化 / 升级数据库表结构"
    PYTHONPATH="$PROJECT_DIR/backend" "$VENV_PY" -m app.scripts.init_db
    log_info "数据库初始化完成"
}

# -----------------------------------------------------------------------------
# 服务进程管理
# -----------------------------------------------------------------------------
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null || echo '')"
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

do_start() {
    if is_running; then
        log_warn "服务已在运行 (PID $(cat "$PID_FILE"))"
        return 0
    fi
    prepare_dirs
    log_step "启动 $APP_NAME"
    PYTHONPATH="$PROJECT_DIR/backend" \
      nohup "$VENV_PY" -m uvicorn app.main:app \
        --host "${OPS_HOST:-0.0.0.0}" --port "${OPS_PORT:-8000}" \
        --workers 1 \
        >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    local pid
    pid="$(cat "$PID_FILE")"
    # 等待端口就绪
    for i in $(seq 1 40); do
        if curl -sf "http://127.0.0.1:${OPS_PORT:-8000}/api/health" >/dev/null 2>&1; then
            log_info "服务已启动：PID=$pid  端口=${OPS_PORT:-8000}"
            log_info "访问地址：http://127.0.0.1:${OPS_PORT:-8000}"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            log_error "服务启动失败，日志尾部："
            tail -30 "$LOG_FILE"
            rm -f "$PID_FILE"
            exit 1
        fi
        sleep 1
    done
    log_warn "健康检查未通过，但进程存活。查看日志：./deploy.sh logs"
}

do_stop() {
    if ! is_running; then
        log_info "服务未运行"
        rm -f "$PID_FILE"
        return 0
    fi
    local pid
    pid="$(cat "$PID_FILE")"
    log_step "停止服务 (PID $pid)"
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 15); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        log_warn "优雅停止超时，强制终止"
        kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    log_info "服务已停止"
}

do_status() {
    if is_running; then
        local pid
        pid="$(cat "$PID_FILE")"
        log_info "运行中：PID=$pid  端口=${OPS_PORT:-8000}"
        if curl -sf "http://127.0.0.1:${OPS_PORT:-8000}/api/health" >/dev/null 2>&1; then
            log_info "健康检查：正常"
        else
            log_warn "健康检查：异常（进程在但接口无响应）"
        fi
    else
        log_info "服务未运行"
    fi
}

do_logs() {
    prepare_dirs
    tail -f "$LOG_FILE"
}

do_update() {
    if [ -d "$PROJECT_DIR/.git" ]; then
        log_step "拉取最新代码"
        git -C "$PROJECT_DIR" pull --ff-only || log_warn "git pull 失败，继续用本地代码"
    fi
    install_deps
    db_init
    do_stop || true
    do_start
    log_info "更新完成"
}

# -----------------------------------------------------------------------------
# 安装为系统服务
# -----------------------------------------------------------------------------
install_service() {
    local os_type
    os_type="$(uname -s)"
    if [ "$os_type" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
        local svc="/etc/systemd/system/${APP_NAME}.service"
        log_step "安装 systemd 服务 -> $svc"
        cat > /tmp/${APP_NAME}.service <<EOF
[Unit]
Description=ops-center 运维中台
After=network.target mysql.service

[Service]
Type=simple
User=${SUDO_USER:-root}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
EnvironmentFile=${ENV_FILE}
Environment="PYTHONPATH=${PROJECT_DIR}/backend"
ExecStart=${VENV_DIR}/bin/python -m uvicorn app.main:app --host ${OPS_HOST:-0.0.0.0} --port ${OPS_PORT:-8000} --workers 1
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/${APP_NAME}.log
StandardError=append:${LOG_DIR}/${APP_NAME}.log

[Install]
WantedBy=multi-user.target
EOF
        sudo mv /tmp/${APP_NAME}.service "$svc"
        sudo systemctl daemon-reload
        sudo systemctl enable "$APP_NAME"
        sudo systemctl restart "$APP_NAME"
        log_info "已安装并启动 systemd 服务"
        log_info "常用：systemctl status|restart|stop ${APP_NAME}；journalctl -u ${APP_NAME} -f"
    elif [ "$os_type" = "Darwin" ]; then
        local plist="$HOME/Library/LaunchAgents/com.opscenter.plist"
        log_step "安装 launchd 服务 -> $plist"
        cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.opscenter</string>
    <key>WorkingDirectory</key><string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin</string>
        <key>PYTHONPATH</key><string>${PROJECT_DIR}/backend</string>
    </dict>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/python</string>
        <string>-m</string><string>uvicorn</string>
        <string>app.main:app</string>
        <string>--host</string><string>${OPS_HOST:-0.0.0.0}</string>
        <string>--port</string><string>${OPS_PORT:-8000}</string>
        <string>--workers</string><string>1</string>
    </array>
    <key>RunAtLoad</key><false/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>${LOG_DIR}/${APP_NAME}.log</string>
    <key>StandardErrorPath</key><string>${LOG_DIR}/${APP_NAME}.log</string>
</dict>
</plist>
EOF
        launchctl unload "$plist" 2>/dev/null || true
        launchctl load "$plist"
        log_info "已安装并加载 launchd 服务"
        log_info "常用：launchctl list | grep opscenter"
    else
        log_error "当前系统（$os_type）不支持自动安装服务，请用 ./deploy.sh start 手动启动"
        exit 1
    fi
}

# -----------------------------------------------------------------------------
# 命令分发
# -----------------------------------------------------------------------------
usage() {
    sed -n '3,22p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
}

main() {
    local cmd="${1:-}"
    [ -z "$cmd" ] && usage

    # 以下命令不需要 venv
    case "$cmd" in
        help|-h|--help) usage ;;
        mysql-up) mysql_up; exit 0 ;;
    esac

    detect_python
    prepare_dirs
    setup_env_file
    create_venv

    case "$cmd" in
        install)
            install_deps
            db_init
            do_start
            ;;
        db-init)
            db_init
            ;;
        reset-admin)
            PYTHONPATH="$PROJECT_DIR/backend" "$VENV_PY" -m app.scripts.init_db --reset-admin
            ;;
        start)   do_start ;;
        stop)    do_stop ;;
        restart) do_stop; do_start ;;
        status)  do_status ;;
        logs)    do_logs ;;
        update)  do_update ;;
        service) install_service ;;
        *)
            log_error "未知命令：$cmd"
            usage
            ;;
    esac
}

main "$@"
