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
                f'hci{index}' for index in range(maximum)
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
    ):
        self.manager = manager or SessionManager(sessions_root=sessions_root)
        self.controller = EdgeAgentController(self.manager, max_devices)
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
        self.transport.publish('session_event', summary)
        for operator in operators:
            payload = dict(operator)
            payload.setdefault('operator_id', payload.get('operator'))
            self.transport.publish('status', payload)

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
    parser.add_argument('--max-devices', type=int, default=4)
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
