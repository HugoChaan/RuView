# RuView 快速启动指南

> 基于实际验证，记录从零到跑通的完整路径。

---

## 项目简介

RuView 是一个用 WiFi 信号做透墙感知的系统。通过 ESP32 芯片采集 CSI（信道状态信息），结合信号处理与神经网络，推断出：

- 人员存在与数量
- 呼吸率 / 心率
- 17 关键点姿态骨骼
- 跌倒检测
- 活动识别

无需摄像头，无需穿戴设备，只需普通 WiFi 硬件。

---

## 三条路径总览

| 路径 | 耗时 | 硬件要求 | 适合场景 |
|------|------|----------|----------|
| [路径一：Docker 模拟模式](#路径一docker-模拟模式无硬件5-分钟) | ~5 分钟 | 无 | 快速体验、验证系统 |
| [路径二：Python 本地验证](#路径二python-本地验证10-分钟) | ~10 分钟 | 无 | 验证信号处理管道 |
| [路径三：真实 ESP32 硬件](#路径三真实-esp32-硬件感知) | ~30 分钟 | ESP32-S3 | 真实感知数据 |

---

## 路径一：Docker 模拟模式（无硬件，5 分钟）

### 前提

- Docker 已安装并运行（验证：`docker --version`）

### 步骤

```bash
# 1. 拉取镜像
docker pull ruvnet/wifi-densepose:latest

# 2. 启动服务（模拟 CSI 数据）
docker run --rm \
  -p 127.0.0.1:3000:3000 \
  -p 127.0.0.1:3001:3001 \
  -e CSI_SOURCE=simulated \
  -e RUVIEW_ALLOW_UNAUTHENTICATED=1 \
  --name ruview-demo \
  ruvnet/wifi-densepose:latest
```

### 验证

服务启动后输出如下即为成功：

```
INFO sensing_server: HTTP:      http://localhost:3000
INFO sensing_server: WebSocket: ws://localhost:3001/ws/sensing
INFO sensing_server: Source:    simulated
INFO sensing_server: Simulated data source active (tick=100ms)
```

访问以下地址：

| 地址 | 说明 |
|------|------|
| `http://localhost:3000/ui/index.html` | 主 UI 可视化界面 |
| `http://localhost:3000/health` | 服务健康状态 |
| `http://localhost:3000/api/v1/status` | 感知引擎状态 |
| `http://localhost:3000/api/v1/sensing/latest` | 实时感知数据（JSON） |
| `ws://localhost:3001/ws/sensing` | WebSocket 实时数据流 |

### 典型 API 响应

```bash
# 健康检查
curl http://localhost:3000/health
# {"clients":0,"source":"simulated","status":"ok","tick":438}

# 感知状态
curl http://localhost:3000/api/v1/status
# {"source":"simulated","status":"ready","trust":{...}}

# 实时数据（包含骨骼关键点 + 生命体征）
curl http://localhost:3000/api/v1/sensing/latest
```

`/sensing/latest` 返回数据包含：
- `persons[]` — 检测到的人员列表，每人含 17 个骨骼关键点（x/y/z + 置信度）
- `vital_signs` — 呼吸率（BPM）、心率（BPM）及置信度
- `signal_field` — 20×20 RF 热力图网格
- `tick` — 帧计数，确认数据在实时更新

### 停止服务

```bash
docker stop ruview-demo
```

### 重启命令（一行快捷）

```bash
docker run --rm -p 127.0.0.1:3000:3000 -p 127.0.0.1:3001:3001 \
  -e CSI_SOURCE=simulated -e RUVIEW_ALLOW_UNAUTHENTICATED=1 \
  --name ruview-demo ruvnet/wifi-densepose:latest
```

---

## 路径二：Python 本地验证（10 分钟）

验证信号处理管道的确定性正确性，不需要任何硬件或网络。

### 前提

- Python 3.9+

### 步骤

```bash
# 1. 安装最小依赖
pip install numpy scipy

# 2. 运行确定性证明（必须输出 VERDICT: PASS）
cd /path/to/RuView
python archive/v1/data/proof/verify.py
```

预期输出：
```
VERDICT: PASS
```

如果 numpy/scipy 版本更新导致 hash 变化：
```bash
python archive/v1/data/proof/verify.py --generate-hash
python archive/v1/data/proof/verify.py
```

---

## 路径三：真实 ESP32 硬件感知

### 硬件清单

| 配置 | 硬件 | 参考价格 | 能力 |
|------|------|----------|------|
| 最低 | ESP32-S3 × 1 | ~¥35-50 | 基础存在检测、生命体征 |
| 推荐 | ESP32-S3 × 3-6 | ~¥200-300 | 全房间覆盖、精准姿态 |
| 完整 | ESP32 网格 + Cognitum Seed | ~¥1000 | 向量存储、证明链、105 模块 |

> **注意**：不能用 ESP32 原版或 ESP32-C3，单核无法运行 CSI DSP 管道。

### 软件前提

```bash
pip install esptool pyserial
```

### 步骤

**1. 查找串口号**

```bash
# macOS / Linux
ls /dev/cu.usb* 2>/dev/null || ls /dev/ttyUSB* 2>/dev/null

# Windows：设备管理器 → 端口 (COM & LPT)
```

**2. 烧录固件**

固件文件在项目 [Releases](https://github.com/ruvnet/RuView/releases) 页面下载，或使用 `firmware/` 目录中的预编译版本。

```bash
python -m esptool --chip esp32s3 \
  --port /dev/cu.usbserial-XXXX \
  --baud 460800 \
  write_flash \
    0x0     bootloader.bin \
    0x8000  partition-table.bin \
    0xf000  ota_data_initial.bin \
    0x20000 esp32-csi-node.bin
```

**3. 配置 WiFi**

```bash
# 查本机局域网 IP
ipconfig getifaddr en0   # macOS
# 或 ip route get 1 | awk '{print $7}'  # Linux

python firmware/esp32-csi-node/provision.py \
  --port /dev/cu.usbserial-XXXX \
  --ssid "你的WiFi名称" \
  --password "WiFi密码" \
  --target-ip 192.168.1.XX   # 换成你电脑的局域网 IP
```

**4. 启动服务器**

```bash
docker run --rm \
  -p 127.0.0.1:3000:3000 \
  -p 127.0.0.1:3001:3001 \
  -p 5005:5005/udp \
  -e CSI_SOURCE=esp32 \
  -e RUVIEW_ALLOW_UNAUTHENTICATED=1 \
  --name ruview-live \
  ruvnet/wifi-densepose:latest
```

**5. 验证连接**

```bash
curl http://localhost:3000/health
# source 字段应为 "esp32"，不再是 "simulated"
```

### 注意事项

- ESP32 和电脑必须在**同一局域网**（同一路由器）
- ESP32-S3 只支持 **2.4GHz WiFi**，不支持 5GHz
- 首次启动需约 **30 秒**环境校准，之后感知结果趋于稳定
- macOS / Linux 下多节点 UDP 开箱即用；Windows Docker Desktop 需额外运行中继：
  ```bash
  python scripts/udp-relay.py --listen-port 5005 --forward-port 5006
  ```

---

## 已知问题 & 解决方案

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| 容器启动报错 `refusing to start` | 安全检查，`RUVIEW_API_TOKEN` 未设置且绑定 0.0.0.0 | 加 `-e RUVIEW_ALLOW_UNAUTHENTICATED=1` 或绑定 127.0.0.1 |
| 容器用 `CSI_SOURCE=auto` 直接退出 exit 78 | 没检测到 ESP32 或主机 WiFi | 显式传 `-e CSI_SOURCE=simulated` |
| `--model` 指向 HF 模型报 `invalid magic` | JSONL 格式不兼容当前 Rust loader | 不传 `--model` 参数，走默认启发式模式 |
| Rust 构建失败，找不到 OpenBLAS | 缺少系统库 | macOS: `brew install openblas`；Ubuntu: `apt install libopenblas-dev` |
| verify.py hash 不匹配 | numpy/scipy 版本升级 | 重新生成：`python archive/v1/data/proof/verify.py --generate-hash` |

---

## 快速参考

```bash
# 启动（模拟）
docker run --rm -p 127.0.0.1:3000:3000 -p 127.0.0.1:3001:3001 \
  -e CSI_SOURCE=simulated -e RUVIEW_ALLOW_UNAUTHENTICATED=1 \
  --name ruview-demo ruvnet/wifi-densepose:latest

# 停止
docker stop ruview-demo

# 健康检查
curl http://localhost:3000/health

# 查看实时感知数据
curl http://localhost:3000/api/v1/sensing/latest | python3 -m json.tool

# Python 管道验证
python archive/v1/data/proof/verify.py

# Rust 完整测试
cd v2 && cargo test --workspace --no-default-features
```

---

*文档基于 RuView v0.7.x，验证环境：macOS + Docker 29.4.0*
