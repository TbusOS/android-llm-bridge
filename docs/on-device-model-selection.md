---
title: 设备端 Agent 模型选型 —— 全球商用场景
type: design-doc
created: 2026-04-21
updated: 2026-04-21
owner: sky
tags: [on-device-agent, model-selection, commercial, license, soc, hardware]
status: recommendation (M4+ 参考)
---

# 设备端 Agent 模型选型 —— 全球商用

> 配套文档:[on-device-agent.html](./on-device-agent.html) 讲"为什么做"和"host ↔ device 协议";本文讲"设备端那个本地模型具体选哪个、需要什么硬件"。

> **适用范围**:任何需要在安卓设备(手机 / 平板 / 嵌入式 / 工控一体机 / 车机 / IoT 终端 等)里跑本地小模型做首轮 triage、事件上报、结构化抽取的**商用硬件产品**,面向**全球市场**销售。深度分析由宿主端大模型承担,设备端英文输出即可,host 再按需翻译或处理。

---

## 一、选型约束

全球 + 商用 + 硬件产品,约束比常规 demo 场景严得多:

1. **许可必须干净到法务一眼放行**
   - 不能有"700M MAU 以上要另谈"(Llama Community License)
   - 不能有事后可被单方修改的 Prohibited Use Policy(Gemma Terms)
   - 不能有"Built with X"品牌标注义务
   - 硬件一旦出厂即卖出,后续条款变动追不回已售设备,**模型供应方的单方改条款权**是最大雷

2. **硬件天花板不一**
   - 入门设备常见 2-4 GB RAM,中端 4-6 GB,高端 8 GB+
   - SoC 档次差异大:高通 SD 8 系 ↔ Allwinner 入门 跨几个数量级
   - 嵌入式 / 工业 / 车机 等场景多数**没有 GMS / AICore**,Google 系设备端方案直接出局
   - 手机场景 GMS 齐全但机型碎片化,不能假设都是 Pixel 8+

3. **能力诉求**
   - 英文够用(host 翻译)
   - 结构化输出 + tool calling 是核心
   - 中文 / 其它语言能力不重要(host 侧大模型处理)

4. **生命周期**
   - 硬件产品寿命常 5-8 年
   - 模型权重和许可要能撑这么久,不能中途被废

---

## 二、模型候选对比

### 2.1 推荐候选

| 模型 | 参数 | 许可 | 许可干净度 | 能力 | 硬件适配 |
|------|------|------|-----------|------|---------|
| **Phi-3.5-mini-instruct** | 3.8B | **MIT** | ★★★★★ | 英文推理同规模头部,tool calling 可用 | Q4_K_M ≈ 2.4 GB,需 4 GB+ RAM |
| **Phi-4-mini-instruct** | 3.8B | **MIT** | ★★★★★ | 比 3.5 更强,原生 function calling | 同上 |
| **IBM Granite 3.2-2B** | 2B | **Apache 2.0** | ★★★★★ | 企业级训练,结构化输出与 function calling 是重点 | Q4 ≈ 1.3 GB,3 GB RAM 够 |
| **IBM Granite 3.2-8B** | 8B | **Apache 2.0** | ★★★★★ | 强 | Q4 ≈ 4.5 GB,需 8 GB+ RAM |
| **SmolLM2-1.7B-Instruct** | 1.7B | **Apache 2.0** | ★★★★★ | 能力一般,但权重+数据+训练代码全开 | Q4 ≈ 1.0 GB,2-3 GB RAM 够 |
| Qwen2.5-3B-Instruct | 3B | Apache 2.0 | ★★★★☆ | 英文也强,tool calling 业界最好 | Q4 ≈ 1.9 GB | Alibaba 出身,美/欧部分市场法务会多问一轮 |

### 2.2 不推荐候选(及理由)

| 模型 | 许可 | 为什么对全球商用长生命周期不合适 |
|------|------|--------------------------------|
| **Gemma 3 系列** | Gemma Terms of Use | Google 保留单方更新 Prohibited Use Policy 的权利,5-8 年生命周期内有条款变动风险 |
| **Llama 3.2 1B/3B** | Llama Community License | 700M MAU 条款 + AUP + "Built with Llama" 标注义务,每次法务都要单独评估 |
| **Mistral Ministral 3B** | Research-only | 不授予商用权 |
| **OpenELM (Apple)** | Apple Sample Code License | 分发受限,无法随 app 上架 |
| **Gemini Nano (via AICore)** | Google AICore Terms | 需 Pixel 8+ / 带 AICore 的 GMS 设备,**多数商用硬件没有** |

### 2.3 MIT vs Apache 2.0 对企业部署的差别

- **MIT**:极简授权,没有显式专利授权条款(靠默示)
- **Apache 2.0**:**明授权专利**,且有专利反制条款(谁告我用专利,自动失去授权)

对"硬件+软件一起卖、全球市场容易被专利钓鱼"的场景,**Apache 2.0 的专利条款更友好**。但 MIT 也完全够用,Microsoft 用 MIT 发 Phi 是主动选择,企业法务普遍熟悉。

---

## 三、硬件需求 —— 不是所有安卓 SoC 都能跑

### 3.1 硬门槛(达不到枪毙 on-device LLM)

| 项 | 最低要求 | 原因 |
|----|---------|------|
| ARM 架构 | **ARMv8-A 64-bit** | 32-bit 无法运行 |
| ARMv8.2-A `dotprod` 扩展 | 必须有 `sdot` / `udot` 指令 | INT8 量化矩阵乘的核心加速,没有就比 FP32 还慢。**这是能用与否的分水岭** |
| RAM | ≥3 GB(跑 1.7-2B 模型)<br>≥4 GB(跑 3B 模型) | 权重 + KV cache + Android 系统 + app |
| 存储 | ≥8 GB(留 4 GB 给模型+OTA) | Q4 模型 1-2.5 GB |
| Android 版本 | **Android 10+, 64-bit userspace** | NDK 推理库只支持 arm64-v8a |

### 3.2 加速项(有则快很多,没有也能跑)

| 项 | 效果 |
|----|------|
| ARMv8.2 FP16 算术(`fphp` + `asimdhp`) | 混合精度快 1.5-2× |
| ARMv8.6-A `i8mm` 矩阵指令 | INT8 矩阵乘再快 2-3×(Cortex-A715/X3+) |
| ARMv9 SVE2 | 比 NEON 吞吐更高 |
| NPU(Hexagon / MTK APU / RK NPU) | INT8 再快 3-10×,显著省电 |
| GPU OpenCL / Vulkan(Adreno / Mali) | 中等提速 |

### 3.3 明确不需要

- Root
- GMS / Google Play Services
- AICore / Gemini Nano
- NNAPI(正在被弃用,改用厂商 NPU SDK)
- 联网

---

## 四、常见安卓 SoC 分档

### A 档:流畅跑 Phi-3.5-mini (3.8B)

**特征**:Cortex-A76/A78 大核 + A55 小核,ARMv8.2 dotprod + FP16,部分有 i8mm,带 NPU,4-8 GB RAM

- **Qualcomm**:Snapdragon 6 Gen 1、7 Gen 1、8 系列、**QCM6490**(IoT/嵌入式专用)、QCM8550
- **MediaTek**:Dimensity 700 / 800 / 900 / 9000 系、Helio G99
- **Rockchip**:**RK3588 / RK3588S**(6 TOPS NPU,嵌入式/工控/边缘计算首选)
- **Amlogic**:A311D2、S905X4

实测 Phi-3.5-mini Q4_K_M CPU 6-15 tokens/s,上 Hexagon / NPU 可到 20-40 tokens/s。

### B 档:适合 Granite 3.2-2B 或 SmolLM2-1.7B

**特征**:全 A55(ARMv8.2 dotprod + FP16,有小核没大核)或 A73+A53,3-4 GB RAM,NPU 弱或无

- **Qualcomm**:QCM4290、Snapdragon 4 Gen 2、SD 680 / 685
- **MediaTek**:Helio G85、Helio G37
- **Rockchip**:RK3566 / RK3568(0.8 TOPS NPU)
- **UNISOC**:T610 / T618 / T620 / T606 / T616(全球中低端设备主力)

实测 2B Q4 CPU 3-7 tokens/s。Phi-3.5-mini 能装下但 4 GB RAM 容易 OOM。

### C 档:不建议上 on-device LLM

**特征**:只有 Cortex-A53(**无 dotprod**)或更老,或 RAM <3 GB

- **Qualcomm**:SD 439 / 450 / 460 等老 4xx 系列
- **MediaTek**:MT6765 / 6762、Helio A22 / A25
- **UNISOC**:SC9863A
- **Rockchip**:RK3326、PX30、RK3399(A72+A53 过渡)
- **Allwinner**:A133、A64、H616 —— 全 A53,跳过

这档硬跑能启动,但 1-2 tokens/s 的速度做不到可用交互。**走宿主端联动方案**。

---

## 五、SKU 规划建议

硬件产品线通常跨多档位,建议按硬件分三条 SKU,不要一刀切:

### SKU 1 · "AI-Ready"(高端/新款)

**硬件规格写入 BOM / 合同**:
- ARMv8.2-A + `dotprod` + FP16
- ≥4 GB RAM(推荐 8 GB)
- ≥8 GB eMMC / UFS
- Android 11+
- 带 NPU(Hexagon / RK NPU 任一)

**候选 SoC**:QCM6490、SD 6 Gen 1、Dimensity 700+、RK3588、A311D2

**装载模型**:**Phi-3.5-mini (MIT) GGUF-Q4_K_M**

### SKU 2 · "Light AI"(中端)

- ARMv8.2-A + `dotprod`(淘汰纯 A53)
- ≥3 GB RAM
- ≥4 GB eMMC
- Android 10+
- NPU 可选

**候选 SoC**:QCM4290、SD 4 Gen 2、Helio G85/G99、UNISOC T618、RK3566

**装载模型**:**IBM Granite 3.2-2B (Apache 2.0)** 或 **SmolLM2-1.7B (Apache 2.0)**

### SKU 3 · "纯 Host"(低端/老款)

纯 A53 或 <3 GB RAM 的 SoC 不上 on-device agent,只做宿主端联动。通过 adb / ssh / UART 把设备日志送回宿主大模型。对常见的"日终巡检、故障上报、远程诊断"类需求完全够用。

---

## 六、推理栈

| 层 | 选择 | 许可 | 备注 |
|----|------|------|------|
| **推理引擎(首选)** | **llama.cpp** | MIT | ARM NEON / dotprod / i8mm 优化成熟,GGUF 通吃,高通 QNN 分支在推进 |
| 推理引擎(NPU 优先) | **ONNX Runtime Mobile** | MIT | Hexagon / QNN EP 最成熟,Phi 官方支持 |
| 推理引擎(PyTorch 生态) | **ExecuTorch** | BSD | PyTorch 新移动栈,Meta 主推 |
| 推理引擎(Google 生态) | LiteRT (原 TFLite) | Apache 2.0 | Phi / Granite 生态在 llama.cpp 更成熟,LiteRT 目前跑 Gemma 最顺,对我们推荐的模型不是首选 |

**推荐组合**:**llama.cpp + GGUF Q4_K_M**。在所有档位安卓 SoC 上都跑得起来,代码量小,移植成本低。如果某款设备带 Hexagon NPU 且要追求极限速度,再单独做一个 ONNX Runtime + QNN 的 variant。

---

## 七、SoC 验收 —— 工程样机五秒判定

拿到一台工程样机,跑一次:

```bash
adb shell cat /proc/cpuinfo | grep -E "asimddp|fphp|i8mm|sve"
```

关键字段:
- **`asimddp`**(= `dotprod`)→ **有:能上 INT8 推理**。没有直接判 C 档。
- **`fphp` + `asimdhp`** → 有:FP16 可用,速度再快 1.5-2×
- **`i8mm`** → 顶配(Cortex-A715/X3+),极少见于量产设备
- **`sve` / `sve2`** → ARMv9,目前安卓设备基本没有

再看 `/proc/meminfo` 的 `MemTotal`:
- ≥3.5 GB → B 档起步
- ≥7 GB → A 档

Android 版本:
```bash
adb shell getprop ro.build.version.release  # 需要 ≥10
adb shell getprop ro.product.cpu.abi        # 需要 arm64-v8a
```

四条命令,五秒出结论能不能装 on-device agent。

---

## 八、一句话结论

**全球商用场景,设备端模型首选 Phi-3.5-mini (MIT) + llama.cpp**。

- MIT 是所有许可里最干净的,Microsoft 不能事后单方改条款
- 3.8B 英文推理同规模头部,tool calling 足够做日志 triage
- llama.cpp 在 ARM 上的 dotprod / i8mm 优化最成熟

**RAM 受限(≤3 GB)换 IBM Granite 3.2-2B (Apache 2.0)**,Apache 2.0 的显式专利授权对硬件厂商更友好,IBM 背书企业法务放心。

**硬件硬门槛:ARMv8.2-A `dotprod` + 3 GB RAM + arm64 Android 10**。不达标的老 A53 / ≤2 GB 设备直接不上 on-device,走宿主端联动即可。

**绕开 Gemma / Llama / Mistral non-commercial / Apple OpenELM**,这些许可在全球商用长生命周期下都有后患。

中文 / 其它语言分析由宿主端大模型(Qwen2.5-3B / Claude / GPT)承担,设备端只做英文结构化上报 —— 分工干净,设备端零语言学习税。

---

## 九、下一步

- 设计协议层 → [on-device-agent.html](./on-device-agent.html)(host ↔ device WebSocket)
- 宿主端 agent 骨架 → [agent.md](./agent.md)
- 实施排期 → [project-plan.md](./project-plan.md) M4+
