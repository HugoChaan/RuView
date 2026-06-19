"""
业务服务器示例
- 订阅 RuView WebSocket 实时感知流
- 检测语义事件（跌倒 / 无活动 / 异常）
- 对外暴露 REST API 供 App 查询
- 可扩展：接入 FCM / 短信 / 企业微信 推送
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Any

import httpx
import websockets
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("app")

# ── 配置（全部从环境变量读取）────────────────────────────────────────
RUVIEW_WS   = os.getenv("RUVIEW_WS",   "ws://ruview:3001/ws/sensing")
RUVIEW_API  = os.getenv("RUVIEW_API",  "http://ruview:3000")

# 推送目标（填入后即生效，留空跳过）
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")   # 任意 HTTP 地址
FCM_KEY     = os.getenv("FCM_KEY",     "")   # Firebase Server Key
FCM_TOPIC   = os.getenv("FCM_TOPIC",   "alerts")

# ── 内存状态（生产替换为 Redis）──────────────────────────────────────
latest_frame: dict = {}
alert_history: deque = deque(maxlen=100)   # 最近 100 条告警
vitals_history: deque = deque(maxlen=300)  # 最近 300 帧生命体征


# ── 告警检测逻辑 ──────────────────────────────────────────────────────
def detect_events(frame: dict) -> list[dict]:
    """从一帧感知数据里提取需要告警的事件"""
    events = []
    ts = frame.get("timestamp", time.time())
    vitals = frame.get("vital_signs", {})

    for person in frame.get("persons", []):
        pid = person.get("id")
        conf = person.get("confidence", 0)

        # ① 跌倒检测
        if person.get("fall_detected") and conf > 0.6:
            events.append({
                "type":       "fall_detected",
                "person_id":  pid,
                "confidence": conf,
                "position":   person.get("position"),
                "timestamp":  ts,
            })

        # ② 长时间无运动（motion_score < 2 且有人在场）
        if person.get("motion_score", 99) < 2.0 and conf > 0.7:
            events.append({
                "type":        "no_movement",
                "person_id":   pid,
                "motion_score": person.get("motion_score"),
                "timestamp":   ts,
            })

    # ③ 生命体征异常
    br = vitals.get("breathing_rate_bpm")
    hr = vitals.get("heart_rate_bpm")
    br_conf = vitals.get("breathing_confidence", 0)

    if br and br_conf > 0.6:
        if br < 6:
            events.append({"type": "breathing_too_slow", "bpm": br, "timestamp": ts})
        elif br > 25:
            events.append({"type": "breathing_too_fast", "bpm": br, "timestamp": ts})

    if hr and hr < 40:
        events.append({"type": "heart_rate_low", "bpm": hr, "timestamp": ts})

    return events


# ── 推送逻辑 ──────────────────────────────────────────────────────────
async def push(event: dict):
    """把事件推送到外部系统，按需扩展"""
    log.warning("🚨 ALERT: %s", json.dumps(event, ensure_ascii=False))

    # 1. 通用 Webhook（企业微信 / 钉钉 / 自定义后端 都走这里）
    if WEBHOOK_URL:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(WEBHOOK_URL, json=event)
                log.info("Webhook sent: %s", WEBHOOK_URL)
        except Exception as e:
            log.error("Webhook failed: %s", e)

    # 2. Firebase FCM（App 推送）
    if FCM_KEY:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(
                    "https://fcm.googleapis.com/fcm/send",
                    headers={"Authorization": f"key={FCM_KEY}"},
                    json={
                        "to": f"/topics/{FCM_TOPIC}",
                        "notification": {
                            "title": _alert_title(event),
                            "body":  _alert_body(event),
                        },
                        "data": event,
                    },
                )
                log.info("FCM sent to topic: %s", FCM_TOPIC)
        except Exception as e:
            log.error("FCM failed: %s", e)


def _alert_title(event: dict) -> str:
    return {
        "fall_detected":       "⚠️ 跌倒警报",
        "no_movement":         "⚠️ 长时间无活动",
        "breathing_too_slow":  "⚠️ 呼吸过慢",
        "breathing_too_fast":  "⚠️ 呼吸过快",
        "heart_rate_low":      "⚠️ 心率异常",
    }.get(event["type"], "⚠️ 异常告警")


def _alert_body(event: dict) -> str:
    t = event["type"]
    if t == "fall_detected":
        return f"检测到跌倒，置信度 {event.get('confidence', 0):.0%}，请立即确认"
    if t == "no_movement":
        return f"人员长时间无运动，运动分 {event.get('motion_score', 0):.1f}"
    if "breathing" in t:
        return f"呼吸率异常：{event.get('bpm', 0):.1f} 次/分"
    if "heart" in t:
        return f"心率异常：{event.get('bpm', 0):.1f} 次/分"
    return str(event)


# ── RuView WebSocket 订阅任务 ─────────────────────────────────────────
# 告警冷却：同一类型事件 30s 内不重复告警
_last_alert: dict[str, float] = {}
COOLDOWN_SEC = 30

async def ruview_listener():
    """持续订阅 RuView 实时数据流，断线自动重连"""
    while True:
        try:
            log.info("Connecting to RuView: %s", RUVIEW_WS)
            async with websockets.connect(RUVIEW_WS, ping_interval=20) as ws:
                log.info("RuView connected ✓")
                async for raw in ws:
                    frame = json.loads(raw)
                    latest_frame.update(frame)

                    # 记录生命体征历史
                    if "vital_signs" in frame:
                        vitals_history.append({
                            "ts": frame.get("timestamp"),
                            **frame["vital_signs"],
                        })

                    # 检测并推送告警
                    for event in detect_events(frame):
                        etype = event["type"]
                        now = time.time()
                        if now - _last_alert.get(etype, 0) >= COOLDOWN_SEC:
                            _last_alert[etype] = now
                            alert_history.appendleft(event)
                            await push(event)

        except Exception as e:
            log.error("RuView disconnected: %s — retry in 5s", e)
            await asyncio.sleep(5)


# ── FastAPI 应用 ───────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(ruview_listener())
    yield
    task.cancel()

app = FastAPI(title="RuView 业务服务器", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件（仪表盘 UI）
from fastapi.staticfiles import StaticFiles
import pathlib
static_dir = pathlib.Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")


# ── API 端点 ───────────────────────────────────────────────────────────
@app.get("/")
def index():
    return {"status": "ok", "service": "ruview-app"}


@app.get("/api/status")
def status():
    """服务健康 + 最新感知状态"""
    return {
        "status": "ok",
        "ruview_connected": bool(latest_frame),
        "tick": latest_frame.get("tick"),
        "source": latest_frame.get("source"),
        "person_count": len(latest_frame.get("persons", [])),
    }


@app.get("/api/vitals")
def vitals():
    """最新生命体征"""
    return latest_frame.get("vital_signs", {})


@app.get("/api/presence")
def presence():
    """当前在场人数和位置"""
    persons = latest_frame.get("persons", [])
    return {
        "count": len(persons),
        "persons": [
            {
                "id":           p["id"],
                "confidence":   p["confidence"],
                "position":     p.get("position"),
                "motion_score": p.get("motion_score"),
                "zone":         p.get("zone"),
            }
            for p in persons
        ],
    }


@app.get("/api/alerts")
def alerts(limit: int = 20):
    """最近告警记录"""
    return {"alerts": list(alert_history)[:limit]}


@app.get("/api/vitals/history")
def vitals_history_api(limit: int = 60):
    """生命体征历史（最近 N 帧）"""
    return {"history": list(vitals_history)[-limit:]}
