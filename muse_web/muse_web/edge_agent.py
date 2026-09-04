"""Long-running local agent controlled by the cloud WebSocket gateway."""

import argparse
import os
import signal
import threading
import time

from muse_hrc.cloud_transport import AgentWebSocketTransport
from muse_web.session_manager import SessionManager


ALLOWED_ACTIONS = {
    'prepare_pipeline',
    'start_recording',
    'stop_recording',
    'stop_session',
    'status',
    'start_section',
    'finish_section',
    'submit_ground_truth',
}


class EdgeAgentController:
    """Translate versioned cloud commands into local SessionManager calls."""

    def __init__(self, manager, max_devices=4):
        self.manager = manager
        self.max_devices = max(1, int(max_devices))

    def handle(self, payload):
        action = payload.get('action')
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f'Comando cloud no permitido: {action}')
        if action == 'prepare_pipeline':
            requested = int(payload.get('max_devices', self.max_devices))
            maximum = min(max(requested, 1), self.max_devices)
            hci_devices = ','.join(
                f'hci{index}' for index in range(1, maximum + 1)
            )
            return self.manager.start(
                payload.get('subject_code', 'participante'),
                payload.get('experiment', 'experimento'),
                hci_devices,
                payload.get('notes', ''),
                'standalone',
            )
        if action == 'start_recording':
            return self.manager.start_recording()
        if action == 'stop_recording':
            return self.manager.stop_recording()
        if action == 'stop_session':
            return self.manager.stop(export=True)
        if action == 'status':
            return self.manager.status()
        if action == 'start_section':
            return self.manager.start_workshop_section(
                payload['section_id'], payload.get('operator')
            )
        if action == 'finish_section':
            return self.manager.finish_workshop_section(
                payload['section_id'], payload['operator']
            )
        scores = {
            name: int(payload[name])
            for name in (
                'task_engagement', 'effort', 'persistence', 'flow'
            )
        }
        return self.manager.submit_ground_truth(
            payload['operator'], payload['section_id'], scores
        )


class EdgeAgent:
    """Publish local snapshots while the transport handles remote commands."""

    def __init__(
        self,
        cloud_url,
        agent_id,
        token,
        sessions_root=None,
        max_devices=4,
        manager=None,
        cognitive_model_path=None,
        cognitive_window_seconds=60.0,
        cognitive_update_seconds=30.0,
        cognitive_cloud_enabled=False,
    ):
        self.manager = manager or SessionManager(sessions_root=sessions_root)
        self.controller = EdgeAgentController(self.manager, max_devices)
        self.cognitive_update_seconds = max(5.0, float(cognitive_update_seconds))
        self._last_cognitive_snapshot_at = 0.0
        self._latest_cognitive_snapshot = {
            'enabled': bool(cognitive_cloud_enabled),
            'state': 'disabled' if not cognitive_cloud_enabled else 'initializing',
        }
        self.cognitive_monitor = None
        if cognitive_cloud_enabled:
            from muse_ml.realtime import RealtimeCognitiveMonitor, RealtimeConfig

            self.cognitive_monitor = RealtimeCognitiveMonitor(RealtimeConfig(
                model_path=cognitive_model_path,
                window_seconds=float(cognitive_window_seconds),
                update_seconds=self.cognitive_update_seconds,
            ))
        self.transport = AgentWebSocketTransport(
            cloud_url,
            agent_id,
            token,
            command_handler=self.controller.handle,
            log_callback=self._log,
        )
        self.running = False

    def run(self):
        self.running = True
        self.transport.start()
        try:
            while self.running:
                self.publish_snapshot()
                time.sleep(2.0)
        finally:
            self.transport.stop()
            self.manager.shutdown()

    def stop(self):
        self.running = False

    def publish_snapshot(self):
        status = self.manager.status()
        summary = dict(status)
        summary.pop('log_tail', None)
        operators = summary.pop('operators', [])
        summary['workshop'] = self.manager.workshop_status()
        summary['cognitive_state'] = self.cognitive_snapshot(status, operators)
        self.transport.publish('session_event', summary)
        for operator in operators:
            payload = dict(operator)
            payload.setdefault('operator_id', payload.get('operator'))
            self.transport.publish('status', payload)

    def cognitive_snapshot(self, status, operators):
        """Return a throttled cognitive-state snapshot for cloud clients."""

        if self.cognitive_monitor is None:
            return self._latest_cognitive_snapshot
        now = time.monotonic()
        if now - self._last_cognitive_snapshot_at < self.cognitive_update_seconds:
            return self._latest_cognitive_snapshot
        self._last_cognitive_snapshot_at = now
        if not status.get('running'):
            self._latest_cognitive_snapshot = {
                'enabled': True,
                'state': 'waiting_for_session',
                'model_loaded': self.cognitive_monitor.model is not None,
                'model_path': self.cognitive_monitor.config.model_path,
                'model_error': self.cognitive_monitor.model_error,
                'window_seconds': self.cognitive_monitor.config.window_seconds,
                'update_seconds': self.cognitive_monitor.config.update_seconds,
                'generated_at': time.time(),
                'operators': [],
            }
            return self._latest_cognitive_snapshot
        operator_ids = [
            item.get('operator_id') or item.get('operator')
            for item in operators
            if item.get('operator_id') or item.get('operator')
        ]
        self._latest_cognitive_snapshot = self.cognitive_monitor.snapshot(
            self.manager.active_database_path(),
            operator_ids,
        )
        return self._latest_cognitive_snapshot

    @staticmethod
    def _log(level, message):
        print(f'[{level.upper()}] {message}', flush=True)


def build_parser():
    parser = argparse.ArgumentParser(
        description='Agente local Muse controlado por la GUI cloud.',
    )
    parser.add_argument('--cloud-url', required=True)
    parser.add_argument('--agent-id', required=True)
    parser.add_argument(
        '--agent-token',
        default=os.environ.get('MUSE_AGENT_TOKEN', ''),
    )
    parser.add_argument('--sessions-root')
    parser.add_argument('--max-devices', type=int, default=1)
    parser.add_argument(
        '--enable-cognitive-cloud',
        action='store_true',
        help='Publica predicciones cognitivas derivadas de EEG/PPG a la GUI cloud.',
    )
    parser.add_argument(
        '--cognitive-model',
        default=os.environ.get('MUSE_COGNITIVE_MODEL'),
        help='Ruta a xgboost_final.joblib u otro modelo entrenado con muse_ml.',
    )
    parser.add_argument(
        '--cognitive-window-seconds',
        type=float,
        default=float(os.environ.get('MUSE_COGNITIVE_WINDOW_SECONDS', '60')),
    )
    parser.add_argument(
        '--cognitive-update-seconds',
        type=float,
        default=float(os.environ.get('MUSE_COGNITIVE_UPDATE_SECONDS', '30')),
    )
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    if not arguments.agent_token:
        raise SystemExit(
            'Define MUSE_AGENT_TOKEN o proporciona --agent-token.'
        )
    agent = EdgeAgent(
        arguments.cloud_url,
        arguments.agent_id,
        arguments.agent_token,
        sessions_root=arguments.sessions_root,
        max_devices=arguments.max_devices,
        cognitive_model_path=arguments.cognitive_model,
        cognitive_window_seconds=arguments.cognitive_window_seconds,
        cognitive_update_seconds=arguments.cognitive_update_seconds,
        cognitive_cloud_enabled=arguments.enable_cognitive_cloud,
    )

    def request_stop(_signum, _frame):
        agent.stop()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, 'SIGBREAK'):
        signal.signal(signal.SIGBREAK, request_stop)
    agent.run()


if __name__ == '__main__':
    main()
