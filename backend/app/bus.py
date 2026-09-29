"""Telemetry bus. The field simulator publishes SCADA-style messages; the digital twin
subscribes. With MQTT_HOST set, messages go through a real MQTT broker (e.g. Mosquitto);
otherwise an in-process bus with identical topics and payloads is used, so the twin code is
the same either way. In production the publisher side is replaced by an OPC-UA/SCADA bridge."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable

from . import config

log = logging.getLogger(__name__)
Handler = Callable[[str, dict], None]
TOPIC_ROOT = "baghewala"


class InProcBus:
    kind = "in-process"

    def __init__(self):
        self._handlers: list[Handler] = []
        self.published = 0

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, topic: str, payload: dict) -> None:
        self.published += 1
        for h in self._handlers:
            h(topic, payload)

    @property
    def status(self) -> str:
        return "in-process bus (set MQTT_HOST to use an MQTT broker)"

    def close(self):
        pass


class MqttBus:
    kind = "mqtt"

    def __init__(self, loop: asyncio.AbstractEventLoop, host: str, port: int):
        import paho.mqtt.client as mqtt

        self._loop = loop
        self._handlers: list[Handler] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self.published = 0
        self.host, self.port = host, port
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="baghewala-twin")
        self._client.on_message = self._on_message
        self._client.connect(host, port, keepalive=30)
        self._client.subscribe(f"{TOPIC_ROOT}/#", qos=0)
        self._client.loop_start()
        self._consumer = loop.create_task(self._consume())

    def _on_message(self, client, userdata, msg):  # paho network thread
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            return
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (msg.topic, payload))

    async def _consume(self):
        while True:
            topic, payload = await self._queue.get()
            for h in self._handlers:
                try:
                    h(topic, payload)
                except Exception:  # keep consuming
                    log.exception("bus handler failed for %s", topic)

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def publish(self, topic: str, payload: dict) -> None:
        self.published += 1
        self._client.publish(topic, json.dumps(payload), qos=0)

    @property
    def status(self) -> str:
        return f"MQTT broker {self.host}:{self.port}"

    def close(self):
        self._consumer.cancel()
        self._client.loop_stop()
        self._client.disconnect()


def make_bus(loop: asyncio.AbstractEventLoop):
    if config.MQTT_HOST:
        try:
            bus = MqttBus(loop, config.MQTT_HOST, config.MQTT_PORT)
            log.info("connected to MQTT broker %s:%s", config.MQTT_HOST, config.MQTT_PORT)
            return bus
        except Exception as exc:
            log.warning("MQTT broker %s:%s unavailable (%s); falling back to in-process bus", config.MQTT_HOST, config.MQTT_PORT, exc)
    return InProcBus()
