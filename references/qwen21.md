# Qwen-Image-2.1 写作规范与限制（详细）

来源：Qwen-Image-2.1 官方 README（HuggingFace Qwen/Qwen-Image-2.1）+ 本机实机验证沉淀。

## 官方事实
- 7B 视觉生成组件（32 层 Single-Stream DiT），统一文生图 + 图像编辑 + RGBA 透明图。
- 原生 2K：1:1=2048×2048；官方支持比例：4:3(2400×1792) / 3:4(1792×2400) / 3:2(2528×1696) / 2:3(1696×2528) / 16:9(2752×1536) / 9:16(1536×2752)。
- 默认 40 步；cfg=1（蒸馏/turbo 系）→ negative prompt 无效。
- 编辑：image + prompt → 出图；最多 10 张参考图；支持圆圈/涂鸦/遮罩局部标注编辑；身份保真（人物/产品）。
- 透明图（RGBA）：prompt 前缀写 "This is an RGBA image with transparency..."。

## 写词纪律（实机验证，血泪沉淀）
1. 词精简，勿绝对词：不加「绝不/必须/所有/唯一」类绝对词，堆砌反而抽风。
2. 以参考图为准：上传图穿什么就输出什么，只写「参考图那套服装」，绝不锁死具体服装类型（工装裤/裙/连体lo服都成立）。曾犯「下装若是牛仔裤/工装裤/喇叭裤…」枚举句 = 错，换衣服就废。
3. 五官身材保真句不可砍：精简只砍风格堆砌与否定词；保真三件独立保留：
   - 五官：五官与参考图完全一致，眼型/双眼皮/唇/鼻/下巴轮廓逐项复制，绝不变成另一个人
   - 身材：按参考图真实轮廓复刻，腰臀差/腰线/肩宽/腿长对照，不美化不平匀化
   - 服装：【所有视角】人物都穿着参考图那套，与参考图一致
4. 保真上限 = 参考图分辨率：低分辨率参考图（如 399×624）是硬天花板，词煮不出个体身材，根治 = 高分辨率全身正面图。
5. 放大别用外部超分：4x-UltraSharp / RealESRGAN_x4plus 放大低分辨率脸 → 形变损坏（实测）。正解 = Qwen 重绘轻度放大（砍 LoRA，主模型原生 16 步 + denoise≈0.3）+ 888 ImageSharpen(radius1 sigma0.5 alpha0.5) 轻锐化；或 1.5x 轻缩放。
6. viggle-turbo LoRA（4/6 步蒸馏）只作文生图加速，不作放大/保真——低步数蒸馏在平滑色区产生彩色噪点/色斑（实测）。
7. 风格锚点：
   - 国漫（稳）：「3D半写实CG渲染」一句定死，Qwen 自带标准身材预设。
   - 真人（易飘）：强锚点一句「专业摄影棚人物全身人像，真实相机拍摄」+ 宽容句独立保留；不堆否定（「不是磨皮/不是树脂」cfg=1 无效还占位）、不过度细节枚举（毛孔/飞发/血丝）。
8. 真人词飘三根源：否定词堆砌 / 过度细节枚举 / 缺强风格锚点。

## 本机模型清单（D:\AI\ComfyUI_windows_portable\ComfyUI\models）
- diffusion_models/qwen-image-2.1-UC-Q8_0.gguf —— Qwen-Image-2.1 通用创作/编辑，GGUF Q8
- diffusion_models/qwen/rayQwen21_turboV13Int8convrot.safetensors —— Ray Qwen turbo 蒸馏版（主模型，Int8）
- loras/qwen-image-2.1-restore.safetensors —— 修复 LoRA
- loras/Qwen-Image-2.1-viggle-turbo-4step-r64 / v0.3-6step —— 加速 LoRA（放大禁用）
- vae/qwen_image_2.1_vae_bf16.safetensors
- upscale_models/4x-UltraSharp.pth / RealESRGAN_x4plus.pth —— 外部超分（放大脸禁用）

## 黑图根因（GGUF 必读）
- Q8_0 GGUF 用默认 `UnetLoaderGGUF`（dequant_dtype=default）→ 反量化出 NaN → latent 全 NaN → VAEDecode 裁剪成 0 → **全黑图**。
- 必须用 `UnetLoaderGGUFAdvanced`，显式三参数缺一不可：`dequant_dtype=float32`、`patch_dtype=float16`、`patch_on_device=true（BOOLEAN 类型，true=补丁驻 GPU；服务端会把字符串 coerce 成布尔，实测两次成功出图 history 里均为 True）`。float32 反量化后 latent 干净（absmax≈16，0 NaN）。
- 图生图编辑接法：`LoadImage` → `TextEncodeQwenImage21` 的 `images` 输入（API key=`images.image_1`，可多图 image_1..image_16）。
- cfg=1.0、euler、simple、40 步；resolution=2048 出 2K（参考图按比例缩放，32 倍数）。

## 出图参数建议
- 图生图编辑：图 + 纯文本提示词，40 步，比例按官方表选（竖图 3:4/2:3，横图 4:3/16:9，方 1:1）。
- 原生 2K 直出优先；事后放大只走 Qwen 重绘轻度 + ImageSharpen，禁外部 4x 超分。
- cfg=1：所有「不许/保持一致」写进提示词正文（保持不变/严禁段），不写 negative。
