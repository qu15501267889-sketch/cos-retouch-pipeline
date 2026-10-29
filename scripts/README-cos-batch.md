# -*- coding: utf-8 -*-
"""cos_batch.py — 批量 2K/4K 编辑提交脚本（cos-retouch-pipeline 第 6 步）

关键设计（实机验证，改前先读）：
- per-job resolution：jobs.json 每条可选 "resolution"（默认 2048；原生 4K 写 4096）
  —— 2026-10-27 妲己 _05 新增，stage_edit 里 resolution=j.get("resolution", 2048)
- UnetLoaderGGUFAdvanced 三参数防黑图（Q8 GGUF 反量化 NaN）：
  dequant_dtype=float32 + patch_dtype=float16 + patch_on_device=True（BOOLEAN 非字符串）
- TextEncodeQwenImage21 图连法：inputs key "images.image_1": [LoadImage节点号, 0]，
  vae 必须连（节点出 latent 直连 KSampler.latent_image）
- cfg=1.0 负词无效；euler/simple；编辑 40 步 denoise=1.0
- 每张完成后 POST /free {"unload_models": true, "free_memory": true}
  防长跑显存碎片化 offload 死锁（代价每张 +1-2 分钟重载，净赚）
- 串行逐张，禁并发（8G 卡 OOM）；断点续跑：输出夹同名成图自动跳过
- import 头必须含 shutil（漏了第 1 张后崩，实测坑）
- resolution 是面积档非长边：2048≈4.2MP 按原图比例缩放；4096 档 8G 卡无耗时实测，
  提交前向用户声明未验证（2K/40步=19min 实测基准）

jobs.json 形状：
[{"input": "绝对路径.png", "prompt": "五段提示词全文",
  "out_name": "落盘名(无扩展)", "seed": 20261026, "resolution": 4096}]
落盘：<OUTDIR>/<out_name>.png

> ⚠ OUTDIR 是脚本顶部硬编码常量，**每单换图片夹必须先 patch 脚本改 OUTDIR**，
> 否则会落盘到上一单的夹子（2026-10-29 王昭君单→嫦娥单踩坑）。
"""
