#!/usr/bin/env bash
# ============================================================
# feishu-minutes OpenClaw 技能 — 安装 / 更新脚本
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/super-wuxiu/feishu-minutes/main/install.sh | bash
#   # 或者直接告诉 OpenClaw 执行上面这条命令
#
# 文档参考：https://docs.openclaw.ai/tools/skills
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/super-wuxiu/feishu-minutes.git"
SKILL_NAME="feishu-minutes"

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---- 跨平台 sed -i 封装 ----
portable_sed_i() {
    # 用法: portable_sed_i 's|old|new|' file
    local expr="$1" file="$2"
    if sed --version 2>/dev/null | grep -q GNU; then
        sed -i "$expr" "$file"
    else
        sed -i '' "$expr" "$file"
    fi
}

# ---- 检测 OpenClaw workspace 路径 ----
detect_workspace() {
    # 按照文档：默认 ~/.openclaw/workspace
    # 如果 OPENCLAW_PROFILE 存在且不等于 "default"，则为 ~/.openclaw/workspace-<profile>
    local base="${HOME}/.openclaw"
    local profile="${OPENCLAW_PROFILE:-default}"

    if [[ "$profile" != "default" && -n "$profile" ]]; then
        echo "${base}/workspace-${profile}"
    else
        echo "${base}/workspace"
    fi
}

# ---- 检测 skills 目录 ----
# 按照 OpenClaw 文档的 Skills 位置优先级：
#   1. <workspace>/skills        — 工作区内技能（最高优先级，openclaw skills install 的目标）
#   2. ~/.openclaw/skills        — 共享/托管技能（managed skills，跨工作区共享）
#   3. ~/.agents/skills          — 个人 agent 技能
# 本脚本对齐 `openclaw skills install` 行为，默认安装到 <workspace>/skills
detect_skills_dir() {
    # 1. 用户显式指定（最高优先级）
    if [[ -n "${OPENCLAW_SKILLS_DIR:-}" ]]; then
        echo "$OPENCLAW_SKILLS_DIR"
        return
    fi

    # 2. Docker/容器环境：/home/node 或 /home/clouduser
    for docker_home in /home/node /home/clouduser; do
        local docker_dir="${docker_home}/.openclaw/workspace/skills"
        if [[ -d "$docker_dir" ]]; then
            echo "$docker_dir"
            return
        fi
    done

    # 3. 本地环境：根据 OPENCLAW_PROFILE 计算 workspace 路径
    local workspace
    workspace=$(detect_workspace)

    local workspace_skills="${workspace}/skills"
    if [[ -d "$workspace_skills" ]]; then
        echo "$workspace_skills"
        return
    fi

    # 4. workspace 存在但 skills/ 子目录不在 → 创建它
    if [[ -d "$workspace" ]]; then
        mkdir -p "$workspace_skills"
        echo "$workspace_skills"
        return
    fi

    # 5. 兜底：~/.openclaw/skills（managed skills 目录，跨工作区共享）
    local managed_dir="${HOME}/.openclaw/skills"
    if [[ -d "$managed_dir" ]]; then
        echo "$managed_dir"
        return
    fi

    # 6. 如果 ~/.openclaw 存在，创建 managed skills 目录
    if [[ -d "${HOME}/.openclaw" ]]; then
        mkdir -p "$managed_dir"
        echo "$managed_dir"
        return
    fi

    # 7. 找不到
    echo ""
}

# ---- 备份用户配置 ----
backup_user_config() {
    local skill_md="$1/SKILL.md"
    ENC_FILE_BAK=""
    SECRET_ENV_BAK=""

    if [[ -f "$skill_md" ]]; then
        # 使用 sed 提取反引号中的值（兼容 macOS + Linux）
        ENC_FILE_BAK=$(sed -n 's/^ENC_FILE[[:space:]]*=[[:space:]]*`\([^`]*\)`.*$/\1/p' "$skill_md" 2>/dev/null | head -1 || true)
        SECRET_ENV_BAK=$(sed -n 's/^SECRET_ENV[[:space:]]*=[[:space:]]*`\([^`]*\)`.*$/\1/p' "$skill_md" 2>/dev/null | head -1 || true)
    fi
}

# ---- 恢复用户配置 ----
restore_user_config() {
    local skill_md="$1/SKILL.md"

    if [[ -n "$ENC_FILE_BAK" ]]; then
        portable_sed_i "s|ENC_FILE = \`\`|ENC_FILE = \`${ENC_FILE_BAK}\`|" "$skill_md"
        ok "已恢复 ENC_FILE = \`${ENC_FILE_BAK}\`"
    fi

    if [[ -n "$SECRET_ENV_BAK" ]]; then
        portable_sed_i "s|SECRET_ENV = \`\`|SECRET_ENV = \`${SECRET_ENV_BAK}\`|" "$skill_md"
        ok "已恢复 SECRET_ENV = \`${SECRET_ENV_BAK}\`"
    fi
}

# ---- 检查依赖 ----
check_deps() {
    local missing=()
    for cmd in git python3; do
        if ! command -v "$cmd" &>/dev/null; then
            missing+=("$cmd")
        fi
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        err "缺少依赖: ${missing[*]}"
        err "请先安装后重试。"
        exit 1
    fi
}

# ============================================================
# 主流程
# ============================================================
main() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║   feishu-minutes  技能安装 / 更新工具       ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════╝${NC}"
    echo ""

    # 1. 检查依赖
    check_deps

    # 2. 检测 skills 目录
    SKILLS_DIR=$(detect_skills_dir)
    if [[ -z "$SKILLS_DIR" ]]; then
        err "找不到 OpenClaw skills 目录。"
        err "请确认 OpenClaw 已安装（https://docs.openclaw.ai/install），"
        err "或设置环境变量 OPENCLAW_SKILLS_DIR 后重试。"
        exit 1
    fi
    info "Skills 目录: ${SKILLS_DIR}"

    TARGET_DIR="${SKILLS_DIR}/${SKILL_NAME}"

    # 3. 判断安装 / 更新
    if [[ -d "$TARGET_DIR/.git" ]]; then
        # ---- 更新模式 ----
        info "检测到已安装，执行更新..."

        # 备份用户配置
        backup_user_config "$TARGET_DIR"
        if [[ -n "$ENC_FILE_BAK" || -n "$SECRET_ENV_BAK" ]]; then
            info "已备份用户配置"
        fi

        cd "$TARGET_DIR"

        # 获取当前版本
        OLD_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

        # shallow clone 需要先 unshallow 才能正常 fetch
        if [[ -f ".git/shallow" ]]; then
            git fetch --unshallow origin 2>/dev/null || git fetch origin 2>/dev/null
        else
            git fetch origin 2>/dev/null
        fi

        LOCAL=$(git rev-parse HEAD)
        REMOTE=$(git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null)

        if [[ "$LOCAL" == "$REMOTE" ]]; then
            ok "已是最新版本 (${OLD_HASH})，无需更新。"
            echo ""
            exit 0
        fi

        git reset --hard origin/main 2>/dev/null || git reset --hard origin/master 2>/dev/null
        NEW_HASH=$(git rev-parse --short HEAD)

        # 恢复用户配置
        restore_user_config "$TARGET_DIR"

        ok "更新完成！ ${OLD_HASH} → ${NEW_HASH}"

    elif [[ -d "$TARGET_DIR" ]]; then
        # ---- 目录存在但不是 git 仓库（手动复制的） ----
        warn "检测到手动安装的版本，将替换为 git 管理版本..."

        # 备份用户配置
        backup_user_config "$TARGET_DIR"
        if [[ -n "$ENC_FILE_BAK" || -n "$SECRET_ENV_BAK" ]]; then
            info "已备份用户配置"
        fi

        # 备份并替换
        BACKUP_DIR="${TARGET_DIR}.bak.$(date +%s)"
        mv "$TARGET_DIR" "$BACKUP_DIR"
        info "旧版本已备份至: ${BACKUP_DIR}"

        git clone --depth 1 "$REPO_URL" "$TARGET_DIR" 2>/dev/null
        NEW_HASH=$(cd "$TARGET_DIR" && git rev-parse --short HEAD)

        # 恢复用户配置
        restore_user_config "$TARGET_DIR"

        ok "已替换为 git 管理版本 (${NEW_HASH})"

    else
        # ---- 全新安装 ----
        info "开始安装..."
        git clone --depth 1 "$REPO_URL" "$TARGET_DIR" 2>/dev/null
        NEW_HASH=$(cd "$TARGET_DIR" && git rev-parse --short HEAD)
        ok "安装完成！版本: ${NEW_HASH}"
    fi

    echo ""
    echo -e "${GREEN}────────────────────────────────────────────────${NC}"
    echo -e "  技能路径: ${TARGET_DIR}"
    echo -e "  下一步:  发送 /new 开始新会话，技能将自动加载"
    echo -e "          （如已开启 skills.load.watch 则会自动刷新）"
    echo -e "${GREEN}────────────────────────────────────────────────${NC}"
    echo ""
}

main "$@"
