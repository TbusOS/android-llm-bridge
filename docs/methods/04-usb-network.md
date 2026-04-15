---
title: 方案 D · USB 网络共享（IP over USB）占位
type: method-placeholder
status: planned-m3
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [usb, rndis, method-d, planned]
---

# 方案 D · USB 网络共享（IP over USB）

> 📋 **占位文档。M3 实现。**

---

## 一、是什么

板子通过 USB 向主机暴露一个虚拟网卡（RNDIS / CDC-ECM / NCM），主机侧得到一个 IP 可达的网络接口。之后走**任何 IP 协议**（ssh / adb TCP / HTTP / ...）。

```
  板子 USB 口 ──RNDIS──► Windows/Linux 主机
       ↓                    ↓
  板子 IP (192.168.42.129)  主机 IP (192.168.42.1)
                                 ↓ ssh android-dev
```

---

## 二、和其他方案的关系

- **不等于 B (adb WiFi)**：不走 WiFi，走 USB
- **不等于 A (adb USB)**：不走 adb 协议，走 IP
- **可以叠加 C**：板子里装 sshd，通过 USB 虚拟网卡 ssh 进去

可以把 D 想成"无需 WiFi 的 C"。

---

## 三、为什么值得预留

- 板子没有 WiFi（embedded 场景常见）
- 不想开 WiFi / 网络隔离环境
- 需要跨 SoC 稳定快速（USB 2.0 480Mbps，USB 3.0 5Gbps）
- 可以跑 rsync / sshfs / web 等方案 C 的全部玩法

---

## 四、为什么第一版不做

- 驱动生态复杂（Windows RNDIS 有好几个变种、可能要手动装驱动）
- Linux 端内核 cdc-ether 模块支持需要检查
- 板子端配置（usb0 接口 up / dhcpd 起）依赖板厂定制
- 收益和方案 C (WiFi) 重叠大

---

## 五、预期实现

```python
class UsbNetworkTransport(Transport):
    """方案 D: 通过 USB 虚拟网卡走 SSH。

    本质上是 SshTransport 的特化版本，只是发现/连接逻辑不同。
    """
    # 复用 SshTransport 的 shell / push / pull 等
    # 但 setup 阶段要：
    # 1. 检测主机端 USB 虚拟网卡接口
    # 2. 探测板子 IP（通常是固定的 192.168.42.129）
    # 3. ssh 连接
```

**引导脚本** `scripts/setup-method-usb-network.sh`：
1. 检测 USB 接口（Linux: `ip link | grep -i usb`）
2. 配置 IP / 路由
3. 探测板子
4. 调用方案 C 的 setup 流程

---

## 六、参考资料

- [Linux USB Gadget (configfs)](https://www.kernel.org/doc/Documentation/usb/gadget_configfs.txt)
- [Windows RNDIS 驱动说明](https://learn.microsoft.com/en-us/windows-hardware/drivers/network/overview-of-remote-ndis--rndis-)
- [CDC-NCM spec](https://www.usb.org/sites/default/files/NCM10_012011.zip)

---

## 七、关联 TODO

- `src/alb/transport/usb_network.py` —— 待实现
- `scripts/setup-method-usb-network.sh` —— 待实现
- `registry.py` 里已登记 `status="planned"`

M3 优先级不高，除非社区有明确需求。
