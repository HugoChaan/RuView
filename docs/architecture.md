# RuView 系统架构图

## 完整联动架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              物理感知层                                      │
│                                                                             │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                 │
│   │  ESP32-S3 #1 │    │  ESP32-S3 #2 │    │  ESP32-S3 #3 │                 │
│   │   ~¥35-50    │    │   ~¥35-50    │    │   ~¥35-50    │                 │
│   │              │    │              │    │              │                 │
│   │ • CSI 采集   │    │ • CSI 采集   │    │ • CSI 采集   │                 │
│   │ • 信道跳频   │    │ • 信道跳频   │    │ • 信道跳频   │                 │
│   │ • 802.11n/ax │    │ • 802.11n/ax │    │ • 802.11n/ax │                 │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘                 │
│          │                  │                   │                          │
│          └──────────────────┼───────────────────┘                          │
│                             │ UDP :5005                                     │
│                    (CSI 帧, 100ms/帧)                                       │
└─────────────────────────────┼───────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         RuView 感知服务器（Rust + Axum）                     │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        信号处理管道                                  │   │
│  │                                                                     │   │
│  │  CSI 原始帧 → 相位对齐 → 多静态融合 → 场景建模 → 特征提取           │   │
│  │  (phase_align) (multistatic) (field_model) (RuvSense 16模块)        │   │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────▼──────────────────────────────────────┐    │
│  │                        神经网络推理层                                │    │
│  │                                                                     │    │
│  │  • CSI Encoder (128-dim, 82.3% 准确率)                              │    │
│  │  • 17关键点姿态估计 (82.69% torso-PCK@20)                           │    │
│  │  • 存在检测头 (< 1ms)                                               │    │
│  │  • 生命体征提取器 (呼吸 0.1-0.5Hz / 心率 0.8-2.0Hz)                │    │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌─────────────────────────────▼──────────────────────────────────────┐    │
│  │                       语义事件总线 (SemanticBus)                     │    │
│  │                                                                     │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │    │
│  │  │ FallRisk    │  │PossibleDistress│  │ ElderlyInactivityAnomaly │  │    │
│  │  │ 跌倒风险    │  │ 危险/求救    │  │ 老人异常无活动           │  │    │
│  │  └─────────────┘  └──────────────┘  └──────────────────────────┘  │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │    │
│  │  │SomeoneSleeping│ │   BedExit   │  │      NoMovement          │  │    │
│  │  │  睡眠检测   │  │   离床检测  │  │    长时间无运动           │  │    │
│  │  └─────────────┘  └──────────────┘  └──────────────────────────┘  │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │    │
│  │  │  RoomActive │  │BathroomOccupied│ │   MultiRoomTransition   │  │    │
│  │  │  房间活动   │  │  浴室占用   │  │    跨房间移动            │  │    │
│  │  └─────────────┘  └──────────────┘  └──────────────────────────┘  │    │
│  └────────────────────────────┬────────────────────────────────────────┘   │
│                               │                                             │
│  ┌────────────────────────────▼──────────────────────────────────────┐     │
│  │                          对外接口层                                 │     │
│  │                                                                    │     │
│  │  REST API          WebSocket           MQTT Publisher              │     │
│  │  :3000             :3001               → broker                   │     │
│  │                                                                    │     │
│  │  GET /health       ws/sensing          ruview/<id>/semantic/*      │     │
│  │  GET /api/v1/      (实时帧流)          ruview/<id>/vital_signs/*   │     │
│  │    sensing/latest                      ruview/<id>/presence        │     │
│  │    vital-signs                                                     │     │
│  │    status                                                          │     │
│  └───────────┬──────────────────┬─────────────────┬──────────────────┘     │
└─────────────────────────────────────────────────────────────────────────────┘
              │                  │                 │
              │ REST Poll        │ WebSocket       │ MQTT
              │ (500ms)          │ (实时推送)      │ (事件驱动)
              ▼                  ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            你的后端服务器                                    │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                        事件接收 & 路由                                │  │
│  │                                                                      │  │
│  │  if event.type == "fall_detected":                                   │  │
│  │      → 触发告警流程                                                  │  │
│  │  if event.type == "elderly_anomaly":                                 │  │
│  │      → 触发关怀提醒                                                  │  │
│  │  if event.type == "possible_distress":                               │  │
│  │      → 触发紧急通知                                                  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  ┌─────────────────┐   ┌───────────────────┐   ┌──────────────────────┐   │
│  │   业务逻辑层     │   │     数据存储       │   │     推送服务层        │   │
│  │                 │   │                   │   │                      │   │
│  │ • 告警去重      │   │ • 事件历史记录     │   │ • FCM (Android)      │   │
│  │ • 联系人管理    │   │ • 生命体征趋势     │   │ • APNs (iOS)         │   │
│  │ • 响应确认      │   │ • 用户配置         │   │ • 短信 (SMS)         │   │
│  │ • 告警升级      │   │ • 告警日志         │   │ • 电话呼叫           │   │
│  └─────────────────┘   └───────────────────┘   └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
          ┌─────────────┐  ┌──────────────┐  ┌──────────────┐
          │  手机 App   │  │   家人手机   │  │  护工/医护   │
          │             │  │             │  │             │
          │ • 实时查看  │  │ • 跌倒通知  │  │ • 紧急告警  │
          │   生命体征  │  │ • 异常提醒  │  │ • 多级升级  │
          │ • 历史回放  │  │ • 一键确认  │  │ • 响应记录  │
          │ • 设置配置  │  │             │  │             │
          └─────────────┘  └──────────────┘  └──────────────┘
```

---

## 数据流细节

### 1. 感知数据流（100ms 周期）

```
ESP32 CSI帧 (UDP)
    │
    ├─ subcarrier amplitude [56个子载波]
    ├─ phase [56个子载波]
    └─ RSSI, timestamp, node_id
    │
    ▼
RuView 感知服务器
    │
    ├─ 相位解缠绕 + LO偏移补偿
    ├─ 多节点加权融合
    ├─ 空间特征提取
    │
    ├─ [呼吸] 带通滤波 0.1-0.5Hz → 过零点计数 → BPM
    ├─ [心率] 带通滤波 0.8-2.0Hz → 过零点计数 → BPM
    ├─ [姿态] CSI Encoder → 128维嵌入 → 17关键点回归
    └─ [跌倒] 相位加速度阈值 + 3帧去抖 + 5s冷却
```

### 2. 语义事件触发条件

```
FallRisk 触发条件:
    score = 10 × 24h内跌倒次数 + 50 × 近60s运动方差
    score ≥ 70 → 触发告警事件

ElderlyAnomaly 触发条件:
    夜间时段(22:00-08:00) + presence=true + motion < 阈值 + 持续 > N分钟

PossibleDistress 触发条件:
    sudden_stillness(突然静止) + 之前有剧烈运动 + breathing异常
```

### 3. 告警推送时序

```
T+0ms    ESP32 检测到异常 CSI 变化
T+100ms  RuView 处理帧，fall_detected = true
T+150ms  SemanticBus 评分，score 超阈值，发出 FallRisk 事件
T+200ms  WebSocket/MQTT 推送到你的服务器
T+250ms  后端触发 FCM/APNs 推送
T+500ms  用户手机收到通知
```

---

## 集成代码示例

### WebSocket 接入（Python）

```python
import asyncio, json, httpx, websockets

RUVIEW_WS = "ws://localhost:3001/ws/sensing"
YOUR_SERVER = "https://your-api.com/alerts"

async def monitor():
    async with websockets.connect(RUVIEW_WS) as ws:
        async for raw in ws:
            frame = json.loads(raw)
            await process(frame)

async def process(frame):
    vitals = frame.get("vital_signs", {})
    
    for person in frame.get("persons", []):
        # 跌倒检测
        if person.get("fall_detected"):
            await alert("fall_detected", person, vitals)
        
        # 运动异常（confidence低但presence=true）
        if person.get("motion_score", 0) < 1.0 and person.get("confidence", 0) > 0.8:
            await alert("no_movement", person, vitals)

async def alert(event_type, person, vitals):
    async with httpx.AsyncClient() as client:
        await client.post(YOUR_SERVER, json={
            "type": event_type,
            "person_id": person["id"],
            "confidence": person["confidence"],
            "position": person["position"],
            "breathing_bpm": vitals.get("breathing_rate_bpm"),
            "heart_rate_bpm": vitals.get("heart_rate_bpm"),
            "timestamp": __import__("time").time(),
        })

asyncio.run(monitor())
```

### MQTT 接入（Python）

```python
import paho.mqtt.client as mqtt, json

def on_message(client, userdata, msg):
    topic = msg.topic  # e.g. ruview/node01/semantic/fall_risk
    data = json.loads(msg.payload)
    
    if "fall_risk" in topic and data.get("value", 0) >= 70:
        send_push_notification(data)

client = mqtt.Client()
client.on_message = on_message
client.connect("localhost", 1883)
client.subscribe("ruview/+/semantic/#")
client.loop_forever()
```

---

## 部署拓扑

### 家庭单节点部署

```
[ESP32-S3] ──WiFi──► [树莓派 / NUC / Mac Mini]
                              │
                      RuView Docker
                              │
                      你的后端服务
                              │
                    Internet ─┤
                              │
                        FCM / APNs
                              │
                          手机 App
```

### 多房间部署

```
卧室 [ESP32-S3] ──┐
客厅 [ESP32-S3] ──┼──WiFi──► [边缘服务器]
浴室 [ESP32-S3] ──┘              │
                          node_id 区分房间
                              │
                    ruview/bedroom/semantic/...
                    ruview/living/semantic/...
                    ruview/bathroom/semantic/...
```
