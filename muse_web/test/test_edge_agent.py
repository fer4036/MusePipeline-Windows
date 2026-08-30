"""Cloud-to-local command mapping tests for the Windows edge agent."""

from unittest.mock import Mock

import pytest

from muse_web.edge_agent import EdgeAgentController, build_parser


def test_prepare_pipeline_always_selects_python_and_bounds_device_count():
    manager = Mock()
    manager.start.return_value = {'running': True}
    controller = EdgeAgentController(manager, max_devices=4)

    result = controller.handle({
        'action': 'prepare_pipeline',
        'subject_code': 'p01',
        'experiment': 'baseline',
        'max_devices': 12,
    })

    assert result == {'running': True}
    manager.start.assert_called_once_with(
        'p01', 'baseline', 'hci1,hci2,hci3,hci4', '', 'standalone'
    )


def test_ground_truth_command_preserves_operator_section_and_scores():
    manager = Mock()
    controller = EdgeAgentController(manager)
    payload = {
        'action': 'submit_ground_truth',
        'operator': 'operador_a',
        'section_id': 'paso_2',
        'task_engagement': 5,
        'effort': 4,
        'persistence': 3,
        'flow': 2,
    }

    controller.handle(payload)

    manager.submit_ground_truth.assert_called_once_with(
        'operador_a', 'paso_2', {
            'task_engagement': 5,
            'effort': 4,
            'persistence': 3,
            'flow': 2,
        }
    )


def test_unknown_cloud_command_is_rejected():
    with pytest.raises(ValueError, match='no permitido'):
        EdgeAgentController(Mock()).handle({'action': 'delete_all'})


def test_cognitive_cloud_reporting_is_opt_in():
    arguments = build_parser().parse_args([
        '--cloud-url', 'wss://example.test/ws/agent/lab',
        '--agent-id', 'lab',
        '--agent-token', 'token',
    ])

    assert arguments.enable_cognitive_cloud is False

    enabled = build_parser().parse_args([
        '--cloud-url', 'wss://example.test/ws/agent/lab',
        '--agent-id', 'lab',
        '--agent-token', 'token',
        '--enable-cognitive-cloud',
        '--cognitive-model', 'ml_output/models/xgboost_final.joblib',
    ])

    assert enabled.enable_cognitive_cloud is True
    assert enabled.cognitive_model.endswith('xgboost_final.joblib')
