#!/usr/bin/env bash
# 一键出安装包：先 PyInstaller 打包，再用 Inno Setup 压成 Setup.exe
#
# 用法：
#   bash build_installer.sh              正常构建
#   bash build_installer.sh --no-build   跳过 PyInstaller，用现有 dist/BinFloater
#
# 产物：dist/BinFloater-Setup-<版本>.exe
#
# 需要 Inno Setup 6：winget install --id JRSoftware.InnoSetup -e
# 编译器不在 PATH 里时，用环境变量 ISCC 指定完整路径。
set -euo pipefail

PROJ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJ"

# ---- 找 Python：优先 PATH 里的 python / python3，找不到再看常见的按用户安装位置 ----
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in python python3; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ] && [ -n "${LOCALAPPDATA:-}" ]; then
  # Windows 上 python.org 的安装包默认装到 %LOCALAPPDATA%\Programs\Python\Python3xx
  for cand in "$LOCALAPPDATA"/Programs/Python/Python3*/python.exe; do
    [ -x "$cand" ] && { PY="$cand"; break; }
  done
fi
if [ -z "$PY" ]; then
  echo "错误：找不到 python，可用环境变量 PYTHON 指定" >&2
  exit 1
fi

# ---- 找 Inno Setup 编译器 ----
# 命令行工具 ISCC.exe 不在 PATH 里，winget 装的在 %LOCALAPPDATA%\Programs，
# 传统安装的在 Program Files (x86)。按可能性依次找，都不中就让用户指定。
if [ -z "${ISCC:-}" ]; then
  for cand in \
    "${LOCALAPPDATA:-}/Programs/Inno Setup 6/ISCC.exe" \
    "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" \
    "/c/Program Files/Inno Setup 6/ISCC.exe"
  do
    [ -f "$cand" ] && { ISCC="$cand"; break; }
  done
fi

if [ -z "${ISCC:-}" ] || [ ! -f "$ISCC" ]; then
  echo "错误：找不到 Inno Setup 编译器 ISCC.exe" >&2
  echo "安装：winget install --id JRSoftware.InnoSetup -e" >&2
  echo "或用环境变量 ISCC 指定路径，例如：" >&2
  echo "  ISCC='/c/Program Files (x86)/Inno Setup 6/ISCC.exe' bash build_installer.sh" >&2
  exit 1
fi

# ---- 版本号从 version.py 读，单一来源，别在别处硬编码 ----
VERSION="$("$PY" -c "import version; print(version.VERSION)")"
echo "版本：$VERSION"

if [ "${1:-}" != "--no-build" ]; then
  echo "== 1/2 清理并打包 =="
  # 不先杀掉旧进程的话，dist 里的 exe 是锁着的，删除会 PermissionError
  taskkill //F //IM BinFloater.exe >/dev/null 2>&1 || true
  "$PY" -m PyInstaller build_onedir.spec --noconfirm --clean
else
  echo "== 1/2 跳过打包，使用现有 dist/BinFloater =="
fi

if [ ! -f "dist/BinFloater/BinFloater.exe" ]; then
  echo "错误：dist/BinFloater/BinFloater.exe 不存在" >&2
  exit 1
fi

echo "== 2/2 生成安装包 =="
# BINF_VERSION 通过环境变量传给 .iss 里的 GetEnv，版本号只在这里定义一次
BINF_VERSION="$VERSION" "$ISCC" "installer/BinFloater.iss"

OUT="dist/BinFloater-Setup-$VERSION.exe"
if [ -f "$OUT" ]; then
  SIZE="$(stat -c %s "$OUT")"
  echo
  echo "✅ 完成：$OUT（$((SIZE / 1024 / 1024)) MB，$SIZE 字节）"
else
  echo "构建失败：没找到 $OUT" >&2
  exit 1
fi
