---
title: 方案 E · scrcpy 屏幕镜像（占位）
type: method-placeholder
status: planned-m3
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [scrcpy, screen-mirror, method-e, planned]
---

# 方案 E · scrcpy 屏幕镜像

> 📋 **占位文档。M3 实现。**

---

## 一、是什么

[scrcpy](https://github.com/Genymobile/scrcpy) 是 Genymobile 开源的安卓屏幕镜像工具。通过 adb 把板子屏幕 + 音频实时投到主机窗口，支持鼠标键盘操控、录屏、截屏。

严格说 scrcpy 不是"传输方案"而是"视觉能力" —— 底层仍然走 adb (方案 A/B)，只是多了**屏幕视觉维度**。

---

## 二、放进方案矩阵的理由

有些调试需要看屏幕实际情况：
- UI 崩溃时看最后一帧
- 录屏复现 bug
- 远程给客户演示
- 用鼠标操作（比 adb input 方便）

但它的特殊性：
- 输入是**视觉**（不是文字），LLM 不能直接解读（需要 vision model）
- 能力独立，不是 Transport ABC 的标准接口能覆盖的

---

## 三、为什么第一版不做

- LLM 直接用的价值低（看屏幕需要 vision model 参与）
- 实现复杂（WebRTC / H.264 解码 / 输入事件映射）
- M1 先把文本能力做扎实，屏幕留给 M3

---

## 四、预期实现

不作为 `Transport`，而是独立 capability：

```python
# src/alb/capabilities/screen.py

async def screen_snapshot(transport: Transport) -> Result[ScreenshotResult]:
    """截取当前屏幕。返回 PNG 路径。
    兼容 A/B/C (走 adb screencap 或 ssh screencap)，不支持 G。
    """

async def screen_record(transport, duration, output) -> Result:
    """录屏 N 秒。走 adb screenrecord。"""

async def screen_mirror_start(transport, port) -> Result:
    """启动 scrcpy 守护，M3 功能。
    Web UI 里可以嵌入 WebRTC 流实时查看。
    """

async def screen_tap(x: int, y: int) -> Result:
    """模拟点击。"""

async def screen_swipe(x1, y1, x2, y2, duration_ms) -> Result:
    """模拟滑动。"""
```

配合 Web UI（M3）可以在浏览器里看到实时屏幕 + 点击操作。

---

## 五、对 LLM 的价值

- **静态截图** → 给 vision model 分析（"屏幕上有 Error 对话框吗"）
- **坐标点击** → 配合 LLM 做自动化操作
- **录屏** → 保存复现证据

---

## 六、参考

- [scrcpy GitHub](https://github.com/Genymobile/scrcpy)
- [Android UIAutomator](https://developer.android.com/training/testing/other-components/ui-automator)
- [uiautomator2 Python lib](https://github.com/openatx/uiautomator2)（可能集成）

---

## 七、关联 TODO

- `src/alb/capabilities/screen.py` —— 待实现
- `scripts/setup-method-scrcpy.sh` —— 安装 scrcpy 依赖
- `registry.py` 里已登记 `status="planned"`
