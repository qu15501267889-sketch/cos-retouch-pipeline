# 4K 点名超分——自切块 GGUF+restore 管线（2026-10-26 妲己单启用）

> **触发（2026-10-27 定案）**：管线默认止步 2K；4K = 用户对图**满意之后**的收尾步骤（SKILL 第 8 步）——agent 主动问「要不要 4K」点头才跑，或用户直接点名某张。源图以用户写出的文件名为准。
> 8K Ray tiled 在 8G 卡是结构性 OOM（历史实测，勿再试）；4K 自切块是当前可行档。

## 与「外部超分禁令」的关系
外部超分模型（4x-UltraSharp/RealESRGAN）放真人脸必形变——任何档位永禁。
本管线 = Qwen GGUF 自己重绘重建，脸由 restore LoRA 管，无外部模型介入。

## 管线
1. PIL LANCZOS 把源图 2 倍预放大（妲己单：1760×2368 → 3520×4736 ≈16.7MP）
2. 切 1792² 块、重叠 128px（grid() 自动算；3×3=9 块）
3. 逐块：上传 → GGUF Q8（UnetLoaderGGUFAdvanced 三参数防黑图：dequant_dtype=float32 + patch_dtype=float16 + patch_on_device=True）+ qwen-image-2.1-restore LoRA 1.0 + 16 步 + 英文 restore 提示词（resolution=0 保持块尺寸）
4. 逐块与 LANCZOS 源做 per-channel 色彩匹配（mean/std 对齐，防块间色差）
5. 128px 线性羽化加权拼接 → 存 TARGET
6. 开跑前 POST /free 卸显存；ledger（pids+tiles）断点续跑；单块 error 自动重提一次，两次失败才判死

## 成本与排期
4060Ti 8G：约 20min/块，9 块 ≈ 2-3 小时。长活，让用户自己排期，agent 别盯等。
9 块一次性全提交，ComfyUI 队列串行消化，别开多进程。

## 提速选项（2026-10-26 提出、**未实测验证**——验证前别当成熟方案用）
- **关键区切块**：只重建脸/上身/裙摆腿 3-4 块，其余区域 LANCZOS 直通+同款羽化拼接（估 60-80min）。逻辑：背景无细节不值得烧 GPU，观感损失预计极小。
- **全块降 8 步**：快一倍，restore 管线低步数未实测（turbo LoRA 低步数色斑前科，非同物但警惕）。
- 用户拍板验证后回写本文件并更新成本数字。

## 脚本
`scripts/upscale_4k_tiled_template.py` = 实跑逻辑模板，换单只改头部【配置块】7 项
（SRC/TARGET/LEDGER/PREFIX/SEED/TARGET_W×H）。
先例：workspace/yunying_4k_tiled.py（云缨）→ workspace/daji_4k_tiled.py（妲己）。

## 坑
- 上传/输出前缀不换 → 撞上单遗留文件（PREFIX 必换）
- TARGET 已存在脚本直接 skip——重跑先删 TARGET 或删 ledger
- 换支撑物/姿势类编辑单先细识图再定切块范围，脸和踩踏接触点必须在重建块内
