"""Tests for the stable LAN alias publisher."""

from unittest.mock import Mock, patch

from muse_web.lan_access import MdnsPublisher, global_ipv4_addresses, lan_ip


def test_global_ipv4_addresses_parses_linux_ip_output():
    completed = Mock(
        returncode=0,
        stdout=(
            '2: wlo1    inet 10.22.235.213/20 brd 10.22.239.255 scope global wlo1\n'
            '3: eno1    inet 192.168.0.20/24 brd 192.168.0.255 scope global eno1\n'
        ),
    )
    with patch('muse_web.lan_access.subprocess.run', return_value=completed):
        assert global_ipv4_addresses() == ['10.22.235.213', '192.168.0.20']


def test_lan_ip_prefers_manual_laboratory_network():
    with patch(
        'muse_web.lan_access.global_ipv4_addresses',
        return_value=['10.22.235.213', '192.168.0.20'],
    ), patch.dict('os.environ', {}, clear=True):
        assert lan_ip() == '192.168.0.20'


def test_lan_ip_honors_assigned_explicit_address():
    with patch(
        'muse_web.lan_access.global_ipv4_addresses',
        return_value=['192.168.0.20', '192.168.0.21'],
    ), patch.dict(
        'os.environ', {'MUSE_WEB_LAN_IP': '192.168.0.21'}, clear=True
    ):
        assert lan_ip() == '192.168.0.21'


def test_mdns_publisher_tracks_address_changes_and_stops_cleanly():
    first = Mock()
    first.poll.return_value = None
    second = Mock()
    second.poll.return_value = None
    popen = Mock(side_effect=[first, second])
    addresses = iter(['10.22.230.10', '10.22.230.10', '10.22.231.11'])
    publisher = MdnsPublisher(
        address_factory=lambda: next(addresses),
        popen_factory=popen,
    )

    publisher._refresh()
    publisher._refresh()
    publisher._refresh()
    publisher._stop_process()

    assert popen.call_args_list[0].args[0] == [
        'avahi-publish-address', '-a', '-R',
        'muse-research.local', '10.22.230.10',
    ]
    assert popen.call_args_list[1].args[0][-1] == '10.22.231.11'
    first.terminate.assert_called_once_with()
    second.terminate.assert_called_once_with()


def test_mdns_publisher_does_not_publish_loopback():
    popen = Mock()
    publisher = MdnsPublisher(
        address_factory=lambda: '127.0.0.1',
        popen_factory=popen,
    )

    with patch('muse_web.lan_access.shutil.which', return_value='/usr/bin/tool'):
        publisher._refresh()

    popen.assert_not_called()
