"""Resilient outbound WebSocket used by a local acquisition agent."""

import asyncio
import platform
import queue
import random
import ssl
import threading
import time

from muse_hrc.cloud_protocol import CloudEnvelope, PROTOCOL_VERSION


class AgentWebSocketTransport:
    """Maintain one authenticated outbound WSS connection with retries."""

    def __init__(
        self,
        url,
        agent_id,
        token,
        session_id='',
        command_handler=None,
        log_callback=None,
        connect_factory=None,
        heartbeat_seconds=20.0,
        queue_size=1000,
    ):
        if not url.startswith(('ws://', 'wss://')):
            raise ValueError('La URL del cloud debe comenzar con ws:// o wss://')
        self.url = url
        self.agent_id = agent_id
        self.token = token
        self.session_id = session_id
        self.command_handler = command_handler
        self.log_callback = log_callback
        self.connect_factory = connect_factory
        self.heartbeat_seconds = float(heartbeat_seconds)
        self._outgoing = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._thread = None
        self.connected = False

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def publish(self, message_type, payload):
        envelope = CloudEnvelope(
            type=message_type,
            agent_id=self.agent_id,
            session_id=self.session_id,
            payload=dict(payload),
        )
        try:
            self._outgoing.put_nowait(envelope)
        except queue.Full:
            try:
                self._outgoing.get_nowait()
                self._outgoing.put_nowait(envelope)
            except (queue.Empty, queue.Full):
                return False
        return True

    def _thread_main(self):
        asyncio.run(self._run())

    async def _run(self):
        attempt = 0
        while not self._stop.is_set():
            try:
                await self._connected_session()
                attempt = 0
            except Exception as error:
                self.connected = False
                attempt += 1
                delay = min(60.0, 2 ** min(attempt, 5))
                delay += random.uniform(0.0, 0.5)
                self._log('warning', f'WebSocket desconectado: {error}')
                await self._wait_or_stop(delay)

    async def _connected_session(self):
        connect = self.connect_factory
        if connect is None:
            try:
                from websockets.asyncio.client import connect
            except ImportError as error:
                raise RuntimeError(
                    'Instala websockets para habilitar el enlace cloud'
                ) from error
        ssl_context = (
            ssl.create_default_context() if self.url.startswith('wss://')
            else None
        )
        headers = {'Authorization': f'Bearer {self.token}'}
        async with connect(
            self.url,
            additional_headers=headers,
            ssl=ssl_context,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as websocket:
            self.connected = True
            self._log('info', 'Agente conectado al gateway cloud')
            await websocket.send(CloudEnvelope(
                type='hello',
                agent_id=self.agent_id,
                session_id=self.session_id,
                payload={
                    'protocol_version': PROTOCOL_VERSION,
                    'platform': platform.platform(),
                },
            ).to_json())
            sender = asyncio.create_task(self._sender(websocket))
            receiver = asyncio.create_task(self._receiver(websocket))
            done, pending = await asyncio.wait(
                (sender, receiver),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
            self.connected = False

    async def _sender(self, websocket):
        last_heartbeat = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_heartbeat >= self.heartbeat_seconds:
                await websocket.send(CloudEnvelope(
                    type='heartbeat',
                    agent_id=self.agent_id,
                    session_id=self.session_id,
                    payload={'queued_messages': self._outgoing.qsize()},
                ).to_json())
                last_heartbeat = now
            try:
                envelope = self._outgoing.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
            await websocket.send(envelope.to_json())

    async def _receiver(self, websocket):
        async for serialized in websocket:
            envelope = CloudEnvelope.from_json(serialized)
            if envelope.type != 'command':
                continue
            success = True
            message = ''
            result = {}
            try:
                if self.command_handler is None:
                    raise RuntimeError('El agente no acepta comandos')
                result = self.command_handler(envelope.payload) or {}
            except Exception as error:
                success = False
                message = str(error)
            await websocket.send(CloudEnvelope(
                type='command_ack',
                agent_id=self.agent_id,
                session_id=self.session_id,
                payload={
                    'command_id': envelope.message_id,
                    'success': success,
                    'message': message,
                    'result': result,
                },
            ).to_json())

    async def _wait_or_stop(self, seconds):
        deadline = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)

    def _log(self, level, message):
        if self.log_callback is not None:
            self.log_callback(level, message)
