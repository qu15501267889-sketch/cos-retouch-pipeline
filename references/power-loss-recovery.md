# 断电/重启恢复单（2026-10-26 妲己单实测）

用户说「电脑关机了」「重新跑一下」时的恢复流程。

## 恢复检查清单（按序）
1. **ComfyUI 在线检查**：`GET http://127.0.0.1:8188/system_stats`（5 秒超时）。开机自启通常几秒~1 分钟内回来；连不上等 90 秒轮询，还不行才让用户手动开。
2. **断电前成图可能已渲染完但未拷贝**：先查 `D:\AI\ComfyUI_windows_portable\ComfyUI\output\` 下本次任务 prefix 的残留文件（如 daji_retouch_00001_.png），用 PIL `Image.open(p).load()` 验完整性——load 成功 = 渲染已完成只是脚本没走到 shutil.copy，可直接采用或留作对照；load 失败 = 半图，重跑为准。
3. **jobs.json + 输入 png 在 workspace 不丢**（C 盘文件重启不动），断点续跑逻辑照常：目标文件已存在自动跳过。
4. **监控进程已死**：旧 terminal session 全部失效，重新起 `cos_batch.py edit` 即可，队列空的重提。

## 实测数据点
- 妲己单断电场景：ComfyUI 约 15 秒内随开机自启回来；半成品成图实为完整 1760×2368 RGBA 6.9MB（load 通过）。
- 用户取消（他自己点 Cancel/清队列）：脚本收到 `execution_interrupted`（node=KSampler）+ `/free` 返回非 JSON 报 JSONDecodeError（不阻断，正常现象），exit code 0。重新提交前确认 `/queue` running=0 pending=0。

## 重跑动作与禁区（原 restart-after-power-loss.md 合并）

（重跑前 30 秒核对）

1. **ComfyUI 是否回来**：开机自启，`GET /system_stats` 拒连就是还没起完，轮询等（90 秒内通常回来）；长期不回才让用户手动开。
2. **任务状态三查**：
   - `cos_jobs.json` 在 → 提示词定稿没丢
   - 输入图（workspace 的 cos_in_*.png 或原图）在 → 不用重新转格式
   - 输出夹目标文件**不在** → 脚本断点续跑会自动重渲染，直接 `cos_batch.py edit`
3. **ComfyUI output 夹残留核对**：断电前那次可能已渲染完、只差拷贝落盘。
   - 用 PIL 打开验证完整性（`im.load()` 不抛错 + 尺寸正确 ≈1760×2368 RGBA）
   - **前缀对账**：残留文件的 filename_prefix 必须与本次 jobs.json 的 out_name 一致才是同一版本。妲己单残留是 `daji_retouch_00001_.png`（旧命名），本次 out_name 是 `王者荣耀-妲己原皮_cos后期_01` → 不同版本，残留不作数、重跑为准。
   - 前缀一致且提示词内容一致 → 可直接拷贝到输出夹交付，省约 19 分钟。

## 重跑动作

- 同 seed 同提示词直接重提（用户说「重新跑」= 原配置重跑，不是改稿重跑）
- 入队后报显存爬升状态确认加载正常（3→4.3G 爬升 = 正常加载，非 offload）
- 完成后照常走第 7 步交付（视觉自查 + 清单）

## 别做的事

- 别因断电重新问参数/重新过提示词（用户已确认过的配置有效）
- 别把旧版残留成图直接交付（提示词迭代过的单子，残留 = 旧版效果）
- 别在 ComfyUI 没回来时空转重试提交
