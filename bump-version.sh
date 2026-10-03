#!/usr/bin/env bash
# ./bump-version.sh 1.0
# build_tools/bump_version.py 校验并更新 app_metadata.json；提交、Tag 和推送在这里直接用 git 完成。
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$script_dir"

if [[ $# -lt 1 ]]; then
    echo "Usage: ./bump-version.sh <major.minor> [--build-number N] [--no-tag] [--no-push] [--no-commit]"
    echo
    echo "Updates app_metadata.json, commits it on main, creates the annotated tag v<major.minor>.<HEAD hash>,"
    echo "pushes app_common main, then pushes main together with the tag to origin."
    echo "--no-tag skips the tag, --no-push keeps the commit and tag local,"
    echo "--no-commit only updates app_metadata.json."
    exit 1
fi

fail() { echo "[ERROR] $*" >&2; exit 1; }

# 优先使用仓库 .venv；脚本只依赖标准库，没有 .venv 时回退到系统 Python 3。
if [[ -x .venv/bin/python3 ]]; then python=.venv/bin/python3
elif command -v python3 >/dev/null 2>&1; then python=python3
else fail "Python 3 not found. Run init_dev.sh or install Python 3."
fi

plan="$(SUPERBIRDTOOLS_BUMP_ENTRY=1 "$python" build_tools/bump_version.py "$@")"
version='' commit=0 create_tag=0 push=0 push_tag=0 submodule=''
while IFS= read -r line; do
    value="${line#*=}"
    case "${line%%=*}" in
        version) version="$value" ;;
        commit) commit="$value" ;;
        create_tag) create_tag="$value" ;;
        push) push="$value" ;;
        push_tag) push_tag="$value" ;;
        submodule) submodule="$value" ;;
    esac
done <<<"$plan"
[[ -n "$version" ]] || fail "build_tools/bump_version.py returned an incomplete plan."
prefix="$version"

if [[ "$commit" == 1 ]]; then
    # --only 只提交版本文件，保留其他路径的暂存内容和本地运行态修改。
    git commit --only -m "Bump version to $version" -- app_metadata.json ||
        fail "app_metadata.json was updated, but the commit failed. Changes were kept; fix the Git error, then commit only app_metadata.json and rerun: ./bump-version.sh $prefix"
fi

# 版本提交完成后再取 HEAD，Tag 与打包时使用同一个 commit。
if [[ "$create_tag" == 1 || "$push" == 1 ]]; then
    plan="$(SUPERBIRDTOOLS_BUMP_ENTRY=1 "$python" build_tools/bump_version.py "$@" --finalize)"
    while IFS= read -r line; do
        value="${line#*=}"
        case "${line%%=*}" in
            version) version="$value" ;;
            create_tag) create_tag="$value" ;;
        esac
    done <<<"$plan"
fi
tag="v$version"

if [[ "$create_tag" == 1 ]]; then
    head="$(git rev-parse HEAD)"
    git tag -a "$tag" "$head" -m "Release $tag" ||
        fail "Version commit $head was kept, but creating tag $tag failed. Fix the Git error and rerun the same version. Existing tags are never overwritten."
    echo "Created annotated tag $tag at $head."
fi

if [[ "$push" == 1 ]]; then
    # CI 需要能下载主仓库引用的子模块提交，所以先推送子模块 main。
    if [[ -n "$submodule" ]]; then
        git -C "$submodule" push origin refs/heads/main:refs/heads/main ||
            fail "The local version commit and tag were kept, but pushing $submodule main failed. Fix the Git error, then rerun: ./bump-version.sh $prefix"
    fi
    refs=(refs/heads/main:refs/heads/main)
    [[ "$push_tag" == 1 ]] && refs+=("refs/tags/$tag:refs/tags/$tag")
    # --atomic：main 与 Tag 要么一起推送成功，要么都不更新远端。
    git push --atomic origin "${refs[@]}" ||
        fail "The local version commit and tag were kept, but pushing to origin failed. Fix the Git error (for example, merge origin/main into main), then rerun: ./bump-version.sh $prefix"
    if [[ "$push_tag" == 1 ]]; then echo "Pushed main and $tag to origin."; else echo "Pushed main to origin."; fi
fi
