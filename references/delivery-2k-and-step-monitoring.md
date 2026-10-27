# 8K 超分已砍 + 逐步进度汇报（2026-10-26）

## 交付节奏最终态：止步 2K，不做超分

用户原话「修改一下skills 不要8K了 8K要等待的时间太久了」。

- 现行交付 = **批量 2K 全落盘 + 成功/失败清单 + 逐张视觉自查**，做完即结束；用户在输出夹自由删图，无第二阶段。
- **用户没点名就不得加超分/放大**；`upscale` 子命令保留但只回一句「已移除」。**例外：用户点名某张要 4K** → 走 `references/upscale-4k-tiled.md` 的切块 GGUF 管线（Qwen 自重建，非外部超分模型），交付止步 4K，8K 仍永不跑。
- 本 skill 正文里第 7 步「超分落地（8K + tiled + 两阶段）」整段、`references/upscale-ray-tiled.md`、`references/qwen21-upscale-two-pass.md`、`scripts/qwen21_batch_queue.py` 的 upscale 部分，全部降级为**历史存档**，勿按其执行。
- 为什么砍（证伪依据）：8K tiled 在 4060Ti 8G 上三组参数对照全 OOM（`Currently allocated 12.87 GiB / Device limit 8.00 GiB`，报错逐字节相同），根因是 ray turbo 7.2G + qwen3vl 8B 编码器 8.8G 同时驻 GPU ≈15.5G > 8G，**tiled 只切每块采样激活，切不掉模型本体**。2026-10-25 那批 20 个 `y8k_out_t*` 切块任务即使排完也拼不出可用成图。
- 若要恢复：先解决模型常驻超显存（换 GGUF Q8 底模 / 换更小编码器 / 分离编码与采样），再重新向用户拍板，别默认复活。

## 用户要求「每一步都给我说」时怎么做

不要用固定 60 秒采样——会漏步、也会把同一值重复报。正确做法是**事件驱动逐步监控**：
连 `ws://127.0.0.1:8188/ws?clientId=x`，只在 `progress.value` 变化时打一行，格式带时间戳 + 内联功耗：

```
[15:16:56] STEP 7/40 (18%)  GPU=7579,70.01,100
[15:22:10] 采样完成，进入后续节点
[15:22:18] ✅ execution_success
```

- 可复用脚本：`comfyui-retouch-workflow` 技能的 `scripts/ws_step_monitor.py`（也支持 `--pid` 只看指定任务前缀、`--hours` 限时）。
- socket 超时不算失败：约 2 分钟打一次心跳，并**主动重连**（服务端重启会静默断流，不重连会空等到底）。
- 先起后台进程（background + notify_on_complete），再按需 poll 增量转述；**不要每步重新连 socket 探测**，会漏事件且开销大。
- 落到汇报里的判定口诀：
  - 步数在推进 + 功耗低（50-70W）= offload 慢跑，**能出图**，报预计时间即可，别误报成卡死
  - 步数零推进 + 功耗低 + `utilization.memory` <10% = 死锁，重启 ComfyUI

## 重启前必须存档 running 任务

`GET /queue` 取 `queue_running[0][2]` 的完整 workflow → 落 json（含 `prompt` 全文、seed、steps、resolution、SaveImage 前缀、LoadImage 名）。杀进程后提示词与 seed 全丢，无存档就只能重问用户。重提前用 assert 逐项校验关键参数一字未改再 POST——2026-10-26 妲己单（seed 866176）就是这么原样恢复的。

## 重启 ≠ 一定恢复满载

同一天第三次死锁（妲己原皮，40 步/res2048）：刚重启、队列干净、VRAM free 7075MB，一提交又落 `2087 MB usable, 5572 MB offloaded`，18 分钟 0 步、功耗 49W；重启后重提同一任务 → 4 分钟推到 3/40，**能走但只有 60W**（仍 offload，40 步约 50 分钟）。
结论：`qwen3vl_8b` 编码器 8.8G > 整卡使「重启后偶尔顺利、偶尔又掉 offload」成为常态；长期方案是换更小编码器或分离编码/采样，不是反复重启。
