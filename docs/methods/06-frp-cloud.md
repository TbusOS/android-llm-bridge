---
title: 方案 F · frp / 云中转（占位）
type: method-placeholder
status: planned-m3
created: 2026-04-15
updated: 2026-04-15
owner: sky
tags: [frp, ngrok, cloud-relay, method-f, planned]
---

# 方案 F · frp / 云中转

> 📋 **占位文档。M3 实现。**

---

## 一、是什么

当板子不在本地 / 客户现场 / 跨地理远程调试时，借助一台**公网中转服务器**让 Linux 调试端和板子建立通路。

典型实现：
- [frp](https://github.com/fatedier/frp) —— 国内常用，开源自建
- [ngrok](https://ngrok.com/) —— 易用，有云服务
- [zrok](https://zrok.io/) —— 新兴，P2P 优先
- [Tailscale](https://tailscale.com/) —— 零配置 VPN，严格说不算中转而是 overlay 网络

---

## 二、架构

```
┌──────────────┐      公网       ┌──────────────┐     内网      ┌────────┐
│  Linux 调试   │ ◄─────────────► │   中转服务器  │ ◄───────────► │  板子   │
│   (frpc)     │                  │    (frps)     │               │ (frpc) │
└──────────────┘                  └──────────────┘               └────────┘

  板子通过 frpc 把本地 sshd (2222) 暴露成中转机的 tcp:XXXX
  Linux 通过 frpc 访问中转机的 tcp:XXXX，等同于直连板子
```

**典型场景**：
- 客户现场 POS 机死机，需要远程诊断
- 出差时调试实验室的板子
- 跨国团队协同 debug
- 客户现场录屏 + 回传

---

## 三、为什么第一版不做

- **安全合规复杂**：公网暴露设备调试接口风险大，客户接受度问题
- **依赖外部服务**：frp 要自建服务器，ngrok 要付费账户
- **稳定性** 取决于网络 / 中转服务
- **延迟高**，不适合实时交互
- M1 聚焦本地 / 同网段即可满足绝大多数研发场景

---

## 四、预期实现

### FrpTransport（复用 SshTransport 接口）

```python
class FrpTransport(SshTransport):
    """方案 F: frp 中转 + ssh。

    Setup:
      1. 在中转机跑 frps
      2. 在板子里跑 frpc，把 sshd 暴露到中转机
      3. 本端直接 ssh <relay-host>:<remote-port>
    """
    def __init__(self, relay_host: str, remote_port: int, **ssh_kwargs):
        super().__init__(host=relay_host, port=remote_port, **ssh_kwargs)
```

### 引导脚本

```bash
alb setup frp \
  --relay-host frp.example.com \
  --relay-token XXX \
  --device-sshd-port 2222 \
  --remote-name device-A
# 生成板子端 frpc.ini → alb push → 启动

# 或连接已配好的：
alb setup frp --connect device-A
```

### 安全机制

必须内置：
- 双向 TLS + 强 token
- 连接白名单
- 日志审计（所有操作记录）
- 可撤销 token
- 自动关闭空闲连接

---

## 五、LLM 集成的特殊考量

远程调试时 LLM 的 MCP server 应该**跑在 Linux 调试端**，不是跑在板子或中转机。LLM 通过 MCP 发命令 → alb 走 frp 隧道 → 板子执行。

---

## 六、参考

- [frp GitHub](https://github.com/fatedier/frp)
- [ngrok docs](https://ngrok.com/docs)
- [Tailscale for IoT](https://tailscale.com/use-cases/iot)
- [zrok open source](https://github.com/openziti/zrok)

---

## 七、关联 TODO

- `src/alb/transport/frp.py` —— 待实现
- `scripts/setup-method-frp.sh` —— 待实现
- `docs/security-cloud-relay.md` —— 云中转专门的安全指南（M3 写）
- `registry.py` 里已登记 `status="planned"`

---

## 八、可选替代方案（更轻量）

不需要中转的远程方案：

| 方案 | 描述 |
|-----|-----|
| Tailscale | 零配置 mesh VPN，设备间 P2P 直连 |
| ZeroTier | 类似 Tailscale |
| Cloudflare Tunnel | Cloudflare 的隧道方案 |
| SSH Jump Host | 一台跳板机 + 普通 ssh |

M3 时可以优先做 **Tailscale 集成**，因为它是 P2P + 零配置 + 工业级安全，比 frp 更现代。
