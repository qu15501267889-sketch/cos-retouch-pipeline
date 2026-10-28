# Skill 维护与发布（GitHub 仓库 / 跨机移植 / 维护工具坑）

> 2026-10-27 妲己单收尾沉淀。触发：用户说「上传到我github上面」「别忘了rel」「直接发一个新版本」——skill 大改后同步发布。

## 仓库身份

- 仓库 `qu15501267889-sketch/cos-retouch-pipeline`（public，main 分支，纯 skill 源码仓库）
- gh CLI 已登录（keyring，账号 qu15501267889-sketch）
- 用户全局规矩：skill 代码改动才推 GitHub 发版；不重复发同一 tag；要挂 zip 时才 `gh release upload --clobber`（v2.3.0 未挂资产）

## 发布流程（v2.3.0 实测全跑通）

0. **先把 SKILL.md frontmatter 的 version 改成本次要发的 tag 号**（v2.3.0 当时漏了，frontmatter 停在 2.2.0）
1. skill 源目录旁建临时 `_repo`：`mkdir _repo && cp -r SKILL.md references scripts _repo/`（不拷 .git）
2. `cd _repo && git init -b main && git remote add origin https://github.com/qu15501267889-sketch/cos-retouch-pipeline.git && git fetch -q origin main`
3. `git reset -q --soft origin/main` —— 基线进 index、本地新文件全保留；仓库独有文件（.gitignore README.md）会显示 D
4. `git checkout -q origin/main -- .gitignore README.md` —— 恢复仓库独有文件
5. `git config user.name "qu15501267889-sketch" && git config user.email "qu15501267889-sketch@users.noreply.github.com"` —— **临时目录没有全局 git 身份，不配必报 "Author identity unknown"**（repo 级配置即可，别动全局）
6. `git add -A && git commit -m "vX.Y.Z: 变更摘要" && git push -q origin main`
7. `gh release create vX.Y.Z -R qu15501267889-sketch/cos-retouch-pipeline --title "..." --notes "..."` —— 正文用 markdown，按「新增 / 修复 / 管线变化」分段（v2.3.0 是先例格式）；纯源码 tag 不挂资产
8. 清理：**先 `cd` 出 _repo 再 `rm -rf _repo`** —— cwd 在目录里时 rm 报 "Device or resource busy"（实测）

## 待办（下次发布顺手做）

- 仓库 description 还是 8K 旧口径（「幸存图tiled 8K超分」）→ `gh repo edit --description` 改成 2K 交付 + 满意后 4K 收尾口径
- 仓库 README 的「七步管线 / 不做超分」段落已过时（现行：2K 交付 + 第 8 步 4K 点名收尾）→ 同次提交一并更新

## 跨机移植（用户问过「放到其他电脑还会不会复现」）

- 规则/表格/脚本随 skill 目录整体走（SKILL.md + references + scripts 拷走即可）；agent 个人记忆不随行 → **重要规则必须落 skill 文件不落记忆**（preferences.md 在 references 里，跟着走）
- `scripts/cos_batch.py` 头部四路径为本机专属，换机先核：`SERVER`（127.0.0.1:8188）/ `COMFY`（D:\AI\ComfyUI_windows_portable）/ `OUTDIR`（写死了罗莱短剧\coser测试）/ `JOBS_FILE`（workspace）
- 目标机需装好：ComfyUI + 模型清单（见 `references/qwen21.md`）+ gh CLI 登录；挡板脚本 cos-preflight.py 仅依赖 stdlib+PIL，可移植

## 维护工具坑（本 skill 文件全是 BOM+CRLF）

- **skill_view 裸名歧义**：`skill_view('cos-retouch-pipeline')` 因根目录与 creative/ 各有一份同名副本而报 Ambiguous → 用 `cos-retouch-pipeline/SKILL.md` 相对路径形式
- **read_file 对 BOM 文件报 binary 拒读**：references/*.md 全带 BOM+CRLF → 一律 Python `io.open(p, encoding='utf-8-sig')` 读
- **写入规矩**：改 BOM 文件优先 Python `io.open(..., encoding='utf-8-sig', newline='')`（历史实测 patch 工具对 BOM+CRLF 文件不稳）；新建无 BOM 文件可走 skill_manage write_file
- **大改后跑引用完整性校验**：SKILL.md 引用的 references/scripts 文件必须全部存在且无孤儿文件（v2.3.0 整改的验收口径）
