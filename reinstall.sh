#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: reinstall.sh [options]

选项:
  --prefix <dir>    指定安装目录（默认: $HOME/.local/bin）
  -h, --help        显示本帮助

该脚本会先调用卸载，再重新安装最新版 dk。

示例:
  ./reinstall.sh
  ./reinstall.sh --prefix /usr/local/bin
  curl -fsSL https://raw.githubusercontent.com/gaoxiang89/oroio/main/reinstall.sh | bash
USAGE
}

die() {
  printf 'reinstall.sh: %s\n' "$*" >&2
  exit 1
}

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-${0}}")" 2>/dev/null && pwd || true)
TMP_DIR=$(mktemp -d)

fetch_script() {
  local name="$1"
  local local_path="$SCRIPT_DIR/$name"

  if [ -n "$SCRIPT_DIR" ] && [ -f "$local_path" ]; then
    echo "$local_path"
    return 0
  fi

  command -v curl >/dev/null 2>&1 || die "需要 curl 以下载 $name"

  local url="https://raw.githubusercontent.com/gaoxiang89/oroio/main/$name"
  local ts
  ts=$(date +%s)
  # 打印到 stderr，避免被命令替换捕获
  printf '正在从 %s 下载 %s...\n' "$url" "$name" >&2
  curl -fsSL -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "${url}?ts=${ts}" \
    -o "$TMP_DIR/$name" || die "下载 $name 失败"
  chmod +x "$TMP_DIR/$name"
  echo "$TMP_DIR/$name"
}

cleanup() {
  [ -n "$TMP_DIR" ] && rm -rf "$TMP_DIR"
}
trap cleanup EXIT

main() {
  local prefix="${DK_PREFIX:-$HOME/.local/bin}"

  while [ $# -gt 0 ]; do
    case "$1" in
    --prefix)
      shift || die "--prefix 需要路径"
      prefix="$1"
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      die "未知参数: $1"
      ;;
    *)
      break
      ;;
    esac
    shift || true
  done

  local uninstall_script install_script
  uninstall_script=$(fetch_script "uninstall.sh")
  install_script=$(fetch_script "install.sh")

  bash "$uninstall_script" --prefix "$prefix" --quiet

  bash "$install_script" --prefix "$prefix" --reinstall

  printf '\n重装完成: dk 已重新写入 %s/dk\n' "$prefix"
}

main "$@"
