#!/usr/bin/env bash
# =============================================================================
# ops-center 运维中台 - 统一部署脚本
# 同时适用于：开发本机（macOS / Linux）与云服务器（Linux）
#
# 常用命令：
#   ./deploy.sh bootstrap    检测并自动安装基础环境（Python >= 3.9 / 源码编译锁定 3.12.14 / pip / venv / curl / git）
#   ./deploy.sh env-check    只检测基础环境，不做任何安装
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

# 载入 .env（若存在），使部署期配置（数据库密码、镜像加速器等）生效。
# 设计优先级：命令行传入的环境变量 > .env > 脚本默认值。
# 先暂存命令行/环境已有的覆盖项，source 之后恢复，确保命令行临时覆盖优先于 .env。
_CLI_DOCKER_REGISTRY_MIRROR="${DOCKER_REGISTRY_MIRROR:-}"
_CLI_MYSQL_IMAGE="${MYSQL_IMAGE:-}"
_CLI_AUTO_INSTALL="${AUTO_INSTALL:-}"
if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
fi
[ -n "$_CLI_DOCKER_REGISTRY_MIRROR" ] && DOCKER_REGISTRY_MIRROR="$_CLI_DOCKER_REGISTRY_MIRROR"
[ -n "$_CLI_MYSQL_IMAGE" ] && MYSQL_IMAGE="$_CLI_MYSQL_IMAGE"
[ -n "$_CLI_AUTO_INSTALL" ] && AUTO_INSTALL="$_CLI_AUTO_INSTALL"
unset _CLI_DOCKER_REGISTRY_MIRROR _CLI_MYSQL_IMAGE _CLI_AUTO_INSTALL

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_step()  { echo -e "${BLUE}[STEP]${NC} $*"; }

# -----------------------------------------------------------------------------
# 基础环境：平台识别 / Python / pip / venv / 常用工具
#   目标：在裸机 Linux（Ubuntu / Debian / RHEL 系 / Alpine）与开发机（macOS）上
#         一键补齐运行 ops-center 所需的基础环境（Python >= 3.9 起）。
#   约定：
#     - 幂等：已满足要求的组件不重复安装，重复执行无副作用
#     - 需要系统权限时使用 sudo；无权限时给出手动指引，不硬失败
#     - 非交互场景用 AUTO_INSTALL=yes|no 控制（默认：有终端时询问）
# -----------------------------------------------------------------------------
REQUIRED_PY="${REQUIRED_PY:-3.9}"
PYTHON_BIN="${PYTHON_BIN:-}"
# 源码编译兜底时锁定的 Python 版本与国内镜像（避免从 python.org 慢速/卡死下载）
PYTHON_SOURCE_VER="${PYTHON_SOURCE_VER:-3.12.14}"
PYTHON_MIRROR="${PYTHON_MIRROR:-https://mirrors.tuna.tsinghua.edu.cn/python}"
AUTO_INSTALL="${AUTO_INSTALL:-auto}"
# MySQL 镜像：默认走国内代理（华为云 SWR 公开的 Docker Hub 同步，免登录），
# 可用 MYSQL_IMAGE 覆盖；若设置了 DOCKER_REGISTRY_MIRROR（阿里云镜像加速器地址，
# 形如 https://xxxx.mirror.aliyuncs.com），则自动写入 docker daemon 的 registry-mirrors
# 并重启 docker，之后 mysql:8.0 直接经阿里云加速拉取（推荐国内阿里云用户使用）
MYSQL_IMAGE="${MYSQL_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/mysql:8.0}"
DOCKER_REGISTRY_MIRROR="${DOCKER_REGISTRY_MIRROR:-}"
OS_TYPE=""; OS_ID=""; OS_VER=""; OS_LIKE=""; PKG_MANAGER=""

version_ge() {  # version_ge 3.10 3.9 -> true
    local a b
    a="$(echo "${1:-0}" | awk -F. '{printf "%d%03d", $1, $2}')"
    b="$(echo "${2:-0}" | awk -F. '{printf "%d%03d", $1, $2}')"
    [ "$a" -ge "$b" ]
}

detect_platform() {
    OS_TYPE="$(uname -s)"
    OS_ID=""; OS_VER=""; OS_LIKE=""
    if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        . /etc/os-release
        OS_ID="${ID:-}"; OS_VER="${VERSION_ID:-}"; OS_LIKE="${ID_LIKE:-}"
    fi
    if [ "$OS_TYPE" = "Darwin" ]; then
        PKG_MANAGER="brew"
        return
    fi
    PKG_MANAGER=""
    for pm in apt-get dnf yum apk zypper; do
        if command -v "$pm" >/dev/null 2>&1; then PKG_MANAGER="$pm"; break; fi
    done
}

is_root()  { [ "$(id -u)" -eq 0 ]; }
as_root()  { if is_root; then "$@"; else sudo "$@"; fi; }
has_priv() { is_root || { command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; }; }

confirm() {
    case "$AUTO_INSTALL" in
        yes|1|true) return 0 ;;
        no|0|false) return 1 ;;
    esac
    if [ -t 0 ]; then
        printf '%s [y/N] ' "$1"
        read -r ans || ans="n"
        case "$ans" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
    fi
    return 1
}

py_ver_of() { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || echo "0.0"; }

find_python() {
    local cand ver
    for cand in "${PYTHON_BIN:-}" python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || continue
        cand="$(command -v "$cand")"
        ver="$(py_ver_of "$cand")"
        if version_ge "$ver" "$REQUIRED_PY"; then
            PYTHON_BIN="$cand"
            return 0
        fi
    done
    # 源码安装 / 精简镜像的常见位置，显式补查（PATH 可能未包含）
    for cand in /usr/local/bin/python3.1[3-9] /usr/local/bin/python3.1[0-9] /usr/local/bin/python3.9; do
        [ -x "$cand" ] || continue
        ver="$(py_ver_of "$cand")"
        if version_ge "$ver" "$REQUIRED_PY"; then
            PYTHON_BIN="$cand"
            return 0
        fi
    done
    return 1
}

python_has_venv() { "$1" -c 'import venv, ensurepip' >/dev/null 2>&1; }

ensure_pip() {
    local py="$1"
    "$py" -m pip --version >/dev/null 2>&1 && return 0
    log_step "为 $py 安装 pip"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py || return 1
    elif command -v wget >/dev/null 2>&1; then
        wget -qO /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py || return 1
    else
        log_warn "系统缺少 curl/wget，无法自动安装 pip"
        return 1
    fi
    as_root "$py" /tmp/get-pip.py >/dev/null 2>&1 || "$py" /tmp/get-pip.py --user >/dev/null 2>&1
}

install_python_apt() {
    as_root apt-get update -y || log_warn "apt-get update 失败，继续尝试安装"
    local cand
    cand="$(apt-cache policy python3 2>/dev/null | awk '/Candidate:/{print $2}' | head -1)"
    if version_ge "${cand:-0.0}" "$REQUIRED_PY"; then
        log_step "apt 安装 Python ${cand}（含 venv / pip / 开发头文件）"
        as_root apt-get install -y python3 python3-venv python3-pip python3-dev
        return 0
    fi
    if [ "${OS_ID:-}" = "ubuntu" ] || [ "${OS_LIKE:-}" != "${OS_LIKE#*ubuntu*}" ]; then
        log_step "仓库 Python 版本 ${cand:-未知} 低于 $REQUIRED_PY，改用 deadsnakes 安装 Python 3.12"
        as_root apt-get install -y software-properties-common ca-certificates curl
        as_root add-apt-repository -y ppa:deadsnakes/ppa
        as_root apt-get update -y
        as_root apt-get install -y python3.12 python3.12-venv python3.12-dev
        as_root apt-get install -y python3.12-distutils || true
        ensure_pip "$(command -v python3.12)"
        return 0
    fi
    log_warn "当前 Debian 系仓库 Python 版本为 ${cand:-未知}，低于 $REQUIRED_PY"
    return 1
}

install_python_rpm() {
    local pm="$1"
    log_step "$pm 安装 python3 / pip / 开发头文件"
    as_root "$pm" install -y python3 python3-pip python3-devel 2>/dev/null \
        || as_root "$pm" install -y python3 python3-pip 2>/dev/null || true
    if find_python; then return 0; fi
    if [ "$pm" = "dnf" ]; then
        log_step "仓库版本仍不足，尝试启用 python39 模块流"
        as_root dnf -y module reset python39 >/dev/null 2>&1 || true
        if as_root dnf -y module enable python39 >/dev/null 2>&1; then
            as_root dnf install -y python39 python39-pip python39-devel && return 0
        fi
    fi
    log_warn "$pm 仓库未提供 Python >= $REQUIRED_PY"
    return 1
}

install_python_apk() {
    log_step "apk 安装 python3 / py3-pip / 开发头文件"
    as_root apk add --no-cache python3 py3-pip python3-dev
    find_python
}

install_python_brew() {
    if ! command -v brew >/dev/null 2>&1; then
        log_warn "未检测到 Homebrew"
        if confirm "是否自动安装 Homebrew（需下载 Xcode Command Line Tools，较慢）？"; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || return 1
        else
            return 1
        fi
    fi
    log_step "brew 安装 python@3.12"
    brew install python@3.12
    find_python
}

install_python_from_source() {
    local ver="$PYTHON_SOURCE_VER" tmp jobs
    if ! confirm "是否从源码编译安装 Python $ver（默认从国内镜像下载，耗时约 5-15 分钟）？"; then
        return 1
    fi
    log_step "安装编译依赖"
    case "$PKG_MANAGER" in
        apt-get) as_root apt-get install -y build-essential zlib1g-dev libssl-dev libffi-dev \
                    libbz2-dev libreadline-dev libsqlite3-dev wget curl ;;
        dnf)     as_root dnf install -y gcc make openssl-devel bzip2-devel libffi-devel zlib-devel \
                    readline-devel sqlite-devel xz-devel wget curl \
                    || as_root dnf groupinstall -y "Development Tools" ;;
        yum)     as_root yum install -y gcc make openssl-devel bzip2-devel libffi-devel zlib-devel \
                    readline-devel sqlite-devel xz-devel wget curl \
                    || as_root yum groupinstall -y "Development Tools" ;;
        apk)     as_root apk add --no-cache build-base openssl-dev libffi-dev zlib-dev bzip2-dev \
                    readline-dev sqlite-dev xz-dev ;;
        *)       log_warn "未知包管理器 ${PKG_MANAGER:-无}，请自行确保 gcc/openssl 开发库已安装" ;;
    esac
    tmp="$(mktemp -d)"
    # 优先国内镜像，失败回退官方源；设置超时避免长时间卡死
    local urls=(
        "${PYTHON_MIRROR%/}/$ver/Python-$ver.tgz"
        "https://www.python.org/ftp/python/$ver/Python-$ver.tgz"
    )
    local ok=0
    for u in "${urls[@]}"; do
        log_step "下载 Python $ver 源码：$u"
        if curl -fsSL --connect-timeout 20 --max-time 600 "$u" -o "$tmp/Python-$ver.tgz"; then
            ok=1
            break
        fi
        log_warn "下载失败，尝试下一个源"
    done
    if [ "$ok" -ne 1 ]; then
        rm -rf "$tmp"
        log_error "源码下载失败，请检查网络连通性（或手动下载 Python-$ver.tgz 到 $tmp 后重试）"
        return 1
    fi
    tar -xzf "$tmp/Python-$ver.tgz" -C "$tmp" || { rm -rf "$tmp"; return 1; }
    jobs="$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)"
    log_step "编译安装到 /usr/local（make -j$jobs）"
    ( cd "$tmp/Python-$ver" \
        && ./configure --prefix=/usr/local --with-ensurepip=install >/dev/null 2>&1 \
        && make -j"$jobs" >/dev/null 2>&1 \
        && as_root make altinstall >/dev/null 2>&1 ) || { rm -rf "$tmp"; log_error "编译安装失败"; return 1; }
    rm -rf "$tmp"
    find_python
}

install_python() {
    case "$PKG_MANAGER" in
        apt-get) install_python_apt ;;
        dnf|yum) install_python_rpm "$PKG_MANAGER" ;;
        apk)     install_python_apk ;;
        brew)    install_python_brew ;;
        *)       log_warn "未识别的包管理器，无法自动安装 Python"; return 1 ;;
    esac
}

ensure_venv_module() {
    local py="$1" ver
    python_has_venv "$py" && return 0
    ver="$(py_ver_of "$py")"
    log_warn "$py 缺少 venv 模块，尝试补装"
    case "$PKG_MANAGER" in
        apt-get)
            as_root apt-get update -y || true
            as_root apt-get install -y "python${ver}-venv" 2>/dev/null \
                || as_root apt-get install -y python3-venv 2>/dev/null || true
            ;;
        dnf|yum) as_root "$PKG_MANAGER" install -y "python${ver/./}-devel" 2>/dev/null || true ;;
        apk)     as_root apk add --no-cache python3-dev py3-virtualenv 2>/dev/null || true ;;
    esac
    python_has_venv "$py"
}

ensure_base_tools() {
    local miss=""
    command -v curl >/dev/null 2>&1 || miss="$miss curl"
    command -v git  >/dev/null 2>&1 || miss="$miss git"
    [ -z "$miss" ] && return 0
    log_warn "缺少常用工具：$miss"
    if ! has_priv; then
        log_warn "无 root/sudo 权限，跳过安装（curl 用于健康检查，git 用于 update 拉代码）"
        return 0
    fi
    if ! confirm "是否安装缺少的工具？"; then
        log_warn "已跳过，继续执行"
        return 0
    fi
    case "$PKG_MANAGER" in
        apt-get) as_root apt-get update -y || true; as_root apt-get install -y $miss ;;
        dnf)     as_root dnf install -y $miss ;;
        yum)     as_root yum install -y $miss ;;
        apk)     as_root apk add --no-cache $miss ;;
        brew)    brew install $miss ;;
    esac
}

manual_python_hint() {
    cat <<EOF
${YELLOW}请手动安装 Python >= ${REQUIRED_PY} 后重试：${NC}
  Ubuntu/Debian : sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip python3-dev
  Ubuntu 20.04  : sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
  RHEL/Rocky 8+ : sudo dnf module enable -y python39 && sudo dnf install -y python39 python39-devel python39-pip
  CentOS 7      : 仓库版本过低，建议源码编译或升级至 Rocky / Alma 8+
  Alpine        : apk add --no-cache python3 py3-pip python3-dev
  macOS         : brew install python@3.12
  通用源码安装  : https://mirrors.tuna.tsinghua.edu.cn/python/  (或 https://www.python.org/downloads/)
EOF
}

ensure_base_env() {
    local check_only="${1:-no}"
    detect_platform
    log_info "平台：$OS_TYPE ${OS_ID:-}${OS_VER:+ $OS_VER}  包管理器：${PKG_MANAGER:-未识别}"

    if ! find_python; then
        log_warn "未找到 Python >= $REQUIRED_PY"
        if [ "$check_only" = "yes" ]; then
            manual_python_hint
            return 1
        fi
        if ! has_priv && [ "$PKG_MANAGER" != "brew" ]; then
            log_error "当前用户无 root/sudo 权限，无法自动安装"
            manual_python_hint
            exit 1
        fi
        if ! confirm "是否自动安装 Python >= $REQUIRED_PY ？"; then
            manual_python_hint
            exit 1
        fi
        install_python || install_python_from_source
        if ! find_python; then
            log_error "自动安装后仍未找到满足要求的 Python"
            manual_python_hint
            exit 1
        fi
    fi

    log_info "Python：$(py_ver_of "$PYTHON_BIN")  ($PYTHON_BIN)"

    if [ "$check_only" = "yes" ]; then
        python_has_venv "$PYTHON_BIN" || log_warn "venv 模块不可用，创建虚拟环境会失败"
    else
        if ! python_has_venv "$PYTHON_BIN"; then
            ensure_venv_module "$PYTHON_BIN" \
                || log_warn "venv 仍不可用，可改用：pip install virtualenv"
        fi
        ensure_base_tools
    fi
}

print_env_summary() {
    echo "--------------------------------------------------------------"
    printf "%-10s %s\n" "Python" "$(py_ver_of "${PYTHON_BIN:-python3}")  (${PYTHON_BIN:-未找到})"
    printf "%-10s %s\n" "venv"   "$(python_has_venv "${PYTHON_BIN:-python3}" 2>/dev/null && echo 可用 || echo 不可用)"
    printf "%-10s %s\n" "pip"    "$("${PYTHON_BIN:-python3}" -m pip --version 2>/dev/null | awk '{print $1" "$2}')"
    printf "%-10s %s\n" "curl"   "$(command -v curl >/dev/null 2>&1 && echo 已安装 || echo 缺失)"
    printf "%-10s %s\n" "git"    "$(command -v git  >/dev/null 2>&1 && echo 已安装 || echo 缺失)"
    printf "%-10s %s\n" "docker" "$(command -v docker >/dev/null 2>&1 && echo '已安装（可选）' || echo '缺失（仅 mysql-up 需要）')"
    echo "--------------------------------------------------------------"
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
apply_docker_mirror() {
    # 将阿里云镜像加速器地址写入容器运行时的镜像源配置
    #   - docker-ce : 写 /etc/docker/daemon.json 的 registry-mirrors，需重启 docker
    #   - podman    : 写 /etc/containers/registries.conf 的 [[registry.mirror]]，即时生效，无需重启
    local mirror="$DOCKER_REGISTRY_MIRROR"
    [ -z "$mirror" ] && return 0
    # 去掉协议头，registries.conf 用纯主机名更稳
    local host="${mirror#*://}"
    host="${host%/}"

    # 识别运行时：docker 命令是否为 podman 别名
    if docker --version 2>/dev/null | grep -qi podman; then
        # ---------------- Podman ----------------
        local f="/etc/containers/registries.conf"
        log_step "配置 Podman 镜像加速器：$mirror (写入 $f)"
        as_root mkdir -p "$(dirname "$f")"
        # 幂等：先删除旧的 ops-center 段
        local stripped; stripped="$(mktemp)"
        as_root bash -c "awk 'BEGIN{s=0} /# >>> ops-center mirror/{s=1;next} /# <<< ops-center mirror/{s=0;next} !s{print}' $f > $stripped 2>/dev/null || true"
        as_root mv "$stripped" "$f"
        # 追加新段（即时生效，无需重启）
        as_root bash -c "cat >> $f" <<EOF

# >>> ops-center mirror
[[registry]]
location = "docker.io"
[[registry.mirror]]
location = "$host"
# <<< ops-center mirror
EOF
        log_info "Podman 环境：镜像加速器已写入 $f，配置即时生效，无需重启"
        return 0
    fi

    # ---------------- Docker CE ----------------
    local f="/etc/docker/daemon.json"
    log_step "配置 Docker 镜像加速器：$mirror"
    local json="{}"
    [ -f "$f" ] && json="$(as_root cat "$f" 2>/dev/null || echo '{}')"
    local new
    new="$(python3 - "$json" "$mirror" <<'PY' || true
import sys, json
raw = (sys.argv[1] or '').strip() or '{}'
try:
    d = json.loads(raw)
except Exception:
    d = {}
if not isinstance(d, dict):
    d = {}
mirrors = d.get("registry-mirrors") or []
m = sys.argv[2]
if m not in mirrors:
    mirrors.insert(0, m)
d["registry-mirrors"] = mirrors
print(json.dumps(d, indent=2, ensure_ascii=False))
PY
)"
    if [ -z "$new" ]; then
        log_warn "生成 daemon.json 失败，请手动在 $f 配置 registry-mirrors 后重试"
        return 0
    fi
    local tmp="$(mktemp)"
    printf '%s\n' "$new" > "$tmp"
    as_root mkdir -p /etc/docker
    as_root mv "$tmp" "$f"
    local os_type; os_type="$(uname -s)"
    if [ "$os_type" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
        log_step "重启 docker 使镜像加速生效"
        as_root systemctl restart docker
        for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 2; done
    else
        log_warn "请手动重启 Docker 使镜像加速生效（macOS: 重启 Docker Desktop；其他: 重启 docker 服务）"
    fi
}

mysql_up() {
    if ! command -v docker >/dev/null 2>&1; then
        log_error "未检测到 docker，无法自动拉起 MySQL"
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        log_error "Docker / Podman 未就绪，请先启动容器运行时（docker 需启动 daemon；podman rootless 需有活动 session）"
        exit 1
    fi
    # 阿里云镜像加速器：写入镜像源配置（docker 写 daemon.json 并重启；podman 写 registries.conf 即时生效）
    if [ -n "${DOCKER_REGISTRY_MIRROR:-}" ]; then
        apply_docker_mirror
    fi
    if docker ps -a --format '{{.Names}}' | grep -qx 'ops-center-mysql'; then
        log_info "MySQL 容器已存在，启动之"
        docker start ops-center-mysql >/dev/null
    else
        log_step "创建 MySQL 8 容器 ops-center-mysql"
        as_root mkdir -p /data/mysql
        as_root chown -R 999:999 /data/mysql 2>/dev/null || true
        # 镜像选择：设了阿里云镜像加速器则走 mysql:8.0（经 daemon mirror 加速），
        # 否则用 MYSQL_IMAGE（默认华为云代理）；拉取失败回退 MYSQL_IMAGE
        local img="$MYSQL_IMAGE"
        [ -n "${DOCKER_REGISTRY_MIRROR:-}" ] && img="mysql:8.0"
        if ! docker image inspect "$img" >/dev/null 2>&1; then
            log_step "拉取 MySQL 镜像：$img"
            if ! docker pull "$img" 2>/dev/null; then
                log_warn "镜像拉取失败，回退：$MYSQL_IMAGE"
                img="$MYSQL_IMAGE"
                docker pull "$img" 2>/dev/null || { log_error "MySQL 镜像拉取失败，请检查网络或 DOCKER_REGISTRY_MIRROR"; exit 1; }
            fi
        fi
        docker run -d --name ops-center-mysql \
            -e MYSQL_ROOT_PASSWORD=rootpass123 \
            -e MYSQL_DATABASE=ops_center \
            -e MYSQL_USER=opscenter \
            -e MYSQL_PASSWORD=opscenter123 \
            -e TZ=Asia/Shanghai \
            -p 3306:3306 \
            -v /data/mysql:/var/lib/mysql \
            --restart unless-stopped \
            "$img" --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null
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
    # 提取文件顶部连续注释块作为帮助信息（跳过 shebang 行）
    awk 'NR==1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "${BASH_SOURCE[0]}"
    exit 0
}

main() {
    local cmd="${1:-}"
    [ -z "$cmd" ] && usage

    # 以下命令不需要 Python 虚拟环境
    case "$cmd" in
        help|-h|--help) usage ;;
        mysql-up) mysql_up; exit 0 ;;
        stop)    setup_env_file; do_stop;   exit 0 ;;
        status)  setup_env_file; do_status; exit 0 ;;
        logs)    setup_env_file; do_logs;   exit 0 ;;
        bootstrap|env-setup)
            ensure_base_env
            print_env_summary
            log_info "基础环境就绪，下一步：./deploy.sh install"
            exit 0 ;;
        env-check)
            if ensure_base_env yes; then
                print_env_summary
                log_info "基础环境满足要求"
            else
                print_env_summary
                log_error "基础环境存在缺失项"
                exit 1
            fi
            exit 0 ;;
    esac

    # 其余命令：先确保基础环境齐备（首次在裸机部署时会自动安装）
    ensure_base_env
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
