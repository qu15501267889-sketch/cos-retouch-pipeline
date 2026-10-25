# COS 修图管线 · cos-retouch-pipeline

> 真人 coser 写写真后期流水线 —— Hermes Agent 技能（Skill）
> **触发词：「你好cos」** · 输入图片路径 → 输出 2K 修图成片 + 8K 超分成片
> 全程本机 ComfyUI + Qwen-Image-2.1，API 提交，不碰用户活画布

---

## 它解决什么问题

COS 摄影后期（换场景 / 加特效 / 修脸 / 放大出图）通常要手动开 ComfyUI 拼工作流、手写提示词、手动换图重跑。本技能把它固化成一条**八步管线**：agent 接到图片路径后自己识图、给认定结论、组装提示词、批量提交渲染、等你挑选、再对幸存图跑 8K 超分。

设计核心是**两阶段交付**：

```
第一阶段（2K 批量）          第二阶段（8K 超分）
┌─────────────────┐   你挑   ┌─────────────────┐
│ 全夹图逐张 2K 编辑 │ ──────→ │ 只对幸存图 tiled │
│ 全部落盘          │  删废片  │ 分块重绘 → 8K    │
└─────────────────┘ ────────→ └─────────────────┘
        ↑停住等拍板                     ↑停住等拍板
```

**超分是最贵的一步（8K tiled ≈10-15 分钟/张），绝不能给注定被删的废片烧显卡时间。**
幸存者判定 = 输出夹里仍存在的文件，被删的自动出局，无需任何状态记录。

---

## 八步管线

| 步 | 内容 | 要点 |
|---|---|---|
| 1 | **启用** | 收到「你好cos」或"路径+后期意图"即进入，不寒暄 |
| 2 | **输入图片路径** | 扫张数自动判单张/批量；输出夹默认=图所在夹 |
| 3 | **识图 + 报认定** | 逐张看图：角色（必标置信度：确定/疑似/认不出）、场景光线、服装、姿势、瑕疵清单、特效候选三维依据（角色元素+场景光线+姿势动势）；输入图自带 AI 修复痕迹必须如实告知，由用户裁决 |
| 4 | **定改法** | 用户说了照办；授权自拟则先给方案过目；换场景按 scenes.md 三铁律选 |
| 5 | **定参数** | 只问 3 项：目标模型（Qwen本地 / gpt-image / 双版本）、特效档位（轻/中/强，可三档都要）、输出夹 |
| 6 | **生图（原生 2K）** | 提示词先过目 → 全部上传 → 全部入队（连续 POST）→ ComfyUI 队列自己串行跑，零间隔；提交前报总耗时 |
| 7 | **用户挑选（强制停点）** | "编辑版全在输出夹，挑完删掉不要的，说『超分』我跑下一步" —— 绝不自动进第 8 步 |
| 8 | **超分 8K** | 幸存图全部入队 tiled 分块工作流（Qwen2.1 重绘，8G 显存唯一能上 8K 的姿势），禁外部超分模型（毁脸） |

---

## 提示词五段模板（每次都用）

```text
你是Cosplay人像后期综合处理师。专业摄影棚人物全身人像，真实相机拍摄。对这张照片执行以下后期处理，只做下面明确列出的事，未提及的部分一律保持原样。

保持不变：
- 人物五官与参考图完全一致，眼型/双眼皮/唇/鼻/下巴轮廓逐项复制，不变成另一个人
- 动作姿势不变，构图取景不变
- 人物穿着参考图那套服装，与参考图一致
- <有手持/挥舞道具时>道具形制完整连贯，不断裂不溶解不变形
- 不做缩放、旋转、平移，保持照片原有透视关系

处理内容：
1. <做什么 + 在哪 + 画面长什么样 + 如何与原片融合>

严禁：<基础禁止项 + 各启用模块的专项禁止项>

一致性：所有修改区域光影一致、噪点一致，新增元素符合照片原有透视
```

硬纪律：真人锚点双句齐上（锁人物段，CG 词只许修饰特效）；保真四件不可砍（五官/身材/服装/道具结构）；cfg=1 负词无效，一切约束写正向；修瑕疵措辞逐字引用 `portrait.md`（唯一权威源，禁自创改写）。

---

## 实测沉淀的硬知识（踩坑实录，不是抄文档）

**Qwen-Image-2.1 GGUF 黑图根因**
- `UnetLoaderGGUFAdvanced` 必须显式 `dequant_dtype=float32` + `patch_dtype=float16`，否则 Q8_0 反量化出 NaN → 全黑图
- `patch_on_device` 是 **BOOLEAN**（True=补丁驻 GPU）。文档里写 `=cpu` 是错的——实测两次成功出图的 history 里该参数都是 True，字符串 "cpu" 被服务端 coerce 成了布尔

**8G 显存账本（RTX 4060 Ti）**
- 文本编码器 `qwen3vl_8b_int8_convrot` = 8.8GB，**比整张卡还大**；扩散模型 Q8 = 7.1GB
- 两者合计 ≈16.5GB vs 8GB 显存：本来就踩在极限上，靠"编码完释放、再装扩散模型"的交接才跑得动
- **掉进 offload（如日志 `5557MB offloaded`）= 死循环**：KSampler 第一步都走不完（WebSocket 收不到 progress、功耗掉到 50-60W、显存利用率 5%）。此时等不出来，唯一解 = **重启 ComfyUI**
- **长时间运行 → 显存碎片化**：连续跑 4 小时后即使队列干净也会复现 offload 死锁，重启即愈（实测：重启后 VRAM 可用从 2GB 回到 7GB）

**后台软件抢显存是真实杀手**
- 剪映在前台时单步 106 秒；关掉后同任务单步 20 秒（**5 倍差距**，功耗 50W→140W）
- 抖音/Chrome/WebView 这类带硬件加速的进程同样占显存，渲染前能关则关

**超分必须 tiled**
- 整图重跑 8K → 爆显存，实测 `execution_interrupted`（KSampler 37 分钟后被系统打断）
- tiled 分块（2MP/块，overlap 128）显存恒定，8G 卡能稳定出 8K
- 4x-UltraSharp / RealESRGAN 等外部超分模型放大真人脸必形变，一律禁用

**批量脚本最低级的坑**
- 漏 `import shutil` → 批量第 1 张后脚本崩，第 2 张没提交（实测）。模板已把 import 写死在文件头并注释标注

**运行环境事实**
- RTX 4060 Ti 8G 基准耗时：2K/40 步编辑 ≈19 分钟/张（满载时）；8K tiled 超分 ≈10-15 分钟/张
- 启动参数 `--reserve-vram 1` 只能保底，不能解决碎片化

---

## 环境要求

- Windows + [ComfyUI](https://github.com/comfyanonymous/ComfyUI)（实测 0.37.0）+ ComfyUI 自带 python_embeded
- 显卡 ≥8GB（实测 RTX 4060 Ti 8G；更低显存需换更小的文本编码器）
- 模型清单（放 ComfyUI `models/` 对应目录）：

| 文件 | 位置 | 用途 |
|---|---|---|
| `qwen-image-2.1-UC-Q8_0.gguf` | diffusion_models | 编辑阶段扩散模型 |
| `rayQwen21_turboV13Int8convrot.safetensors` | diffusion_models/qwen | 超分阶段扩散模型 |
| `qwen3vl_8b_int8_convrot.safetensors` | text_encoders | 文本编码器（两阶段共用） |
| `qwen_image_2.1_vae_bf16.safetensors` | vae | VAE（两阶段共用） |
| `qwen-image-2.1-restore.safetensors` | loras | 超分画质修复 LoRA |
| `Qwen-Image-2.1-viggle-turbo-v0.3-6step-lora-r128.safetensors` | loras | 6 步加速 LoRA |
- ComfyUI 节点包：GGUF、**AusBoss Nodes**（tiled 超分）、essentials（ImageSharpen）

---

## 安装（Hermes Agent）

```bash
# 复制到技能库
git clone https://github.com/qu15501267889-sketch/cos-retouch-pipeline.git
cp -r cos-retouch-pipeline ~/.hermes/skills/cos-retouch-pipeline
# references/ 与 scripts/ 一并带上，SKILL.md 里按相对路径引用
```

技能即装即用：description 触发词已前置（「你好cos」），Hermes 索引命中即加载。

## 用法

```
你：你好cos
AI：（进入八步管线，问图片路径）
你：C:\Users\me\Desktop\罗莱短剧\coser测试
AI：（识图+报认定 → 等你确认 → 问改法/模型/档位 → 提示词过目 → 批量 2K 出图）
你：（看图，删掉不要的）
你：超分
AI：（幸存图 tiled 8K，落盘 _8K.png）
```

批量的两个阶段用脚本驱动（可选，agent 也能直接走 API）：

```bash
D:/AI/ComfyUI_windows_portable/python_embeded/python.exe scripts/cos_batch.py edit      # 阶段一：全入队串行 2K 编辑
D:/AI/ComfyUI_windows_portable/python_embeded/python.exe scripts/cos_batch.py upscale   # 阶段二：幸存图 tiled 8K
```

脚本内建：断点对账（已有成图自动跳过）、单张失败跳过不炸批、全部入队由 ComfyUI 队列串行（脚本崩了队列照跑）。

---

## 目录结构

```
cos-retouch-pipeline/
├── SKILL.md                    # 八步管线主文件（frontmatter + 硬纪律 + Pitfalls + 验收清单）
├── README.md                   # 本文件
├── references/
│   ├── portrait.md             # 修脸/头发/修容措辞 —— 唯一权威源，逐字引用
│   ├── effects.md              # 44 种特效库：视觉描述/融入写法/强度词/变体
│   ├── scenes.md               # 场景替换三铁律 + 10 场景库 + 前后景配套
│   ├── qwen21.md               # Qwen-Image-2.1 写作规范 + 黑图根因 + 节点接法
│   ├── upscale-8k.md           # 8K tiled 超分完整 API 节点图（UI 工作流 → API 结构）
│   ├── preferences.md          # 用户偏好跨夹记忆（读写规则内建）
│   └── template.json           # 字段结构示例（非措辞来源）
└── scripts/
    └── cos_batch.py            # 两阶段批量脚本（edit / upscale）
```

---

## 致谢

- 超分工作流基于 **AusBoss** 的 Qwen Image 2.1 Tiled Upscale 节点与工作流设计
- 模型：Qwen-Image-2.1（GGUF 量化社区）、rayQwen21 turbo、restore LoRA
- 本技能从两轮真人 coser 实拍测试中迭代而来，测试样本含漫展实拍多人多场景

## License

MIT
