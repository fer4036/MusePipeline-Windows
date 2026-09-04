"""Tests for safe automatic Muse identification."""

from muse_hrc.ble_identity import (
    extract_current_scan_addresses,
    extract_device_addresses,
    identify_muse,
    parse_manufacturer_ids,
    summarize_properties,
)


SCAN_OUTPUT = """
[NEW] Device 76:A4:A1:9A:91:85 76-A4-A1-9A-91-85
[NEW] Device 98:17:3C:6A:27:17 ihoment_H6006_2717
[DEL] Device 76:A4:A1:9A:91:85 76-A4-A1-9A-91-85
"""


def test_extracts_unique_addresses_from_scan():
    addresses = extract_device_addresses(SCAN_OUTPUT)

    assert addresses == {
        '76:a4:a1:9a:91:85',
        '98:17:3c:6a:27:17',
    }


def test_current_scan_excludes_removed_and_cached_devices():
    current_output = (
        '[CHG] Device 00:55:DA:BB:CC:8E RSSI: -52\n'
        '[NEW] Device 76:A4:A1:9A:91:85 Other\n'
        '[DEL] Device 76:A4:A1:9A:91:85 Other\n'
    )

    assert extract_current_scan_addresses(current_output) == {
        '00:55:da:bb:cc:8e',
    }


def test_identifies_muse_by_name():
    properties = 'Name: Muse-S-A123\nAlias: Muse-S-A123\n'

    assert identify_muse('76:A4:A1:9A:91:85', properties) == 'nombre/alias Muse'


def test_identifies_muse_by_interaxon_service_uuid():
    properties = (
        'Name: 76-A4-A1-9A-91-85\n'
        'UUID: Vendor specific '
        '(0000fe8d-0000-1000-8000-00805f9b34fb)\n'
    )

    assert identify_muse('76:A4:A1:9A:91:85', properties) == (
        'UUID de servicio Interaxon/Muse'
    )


def test_identifies_muse_by_proprietary_gatt_uuid():
    properties = 'UUID: Vendor specific (273e0013-4c4d-454d-96be-f03bac821358)\n'

    assert identify_muse('76:A4:A1:9A:91:85', properties) == (
        'UUID de servicio Interaxon/Muse'
    )


def test_rejects_unrelated_named_and_anonymous_devices():
    light = 'Name: ihoment_H6006_2717\nAlias: ihoment_H6006_2717\n'
    anonymous = 'Name: 76-A4-A1-9A-91-85\nRSSI: -62\n'

    assert identify_muse('98:17:3C:6A:27:17', light) is None
    assert identify_muse('76:A4:A1:9A:91:85', anonymous) is None


def test_supports_configured_manufacturer_id():
    properties = 'ManufacturerData.Key: 0x1234 (4660)\n'
    manufacturer_ids = parse_manufacturer_ids('0x1234, 0xabcd')

    assert identify_muse(
        '76:A4:A1:9A:91:85',
        properties,
        manufacturer_ids,
    ) == 'ID de fabricante 0x1234'


def test_property_summary_only_contains_diagnostic_fields():
    properties = 'Name: Other\nRSSI: -70\nConnected: no\n'

    assert summarize_properties(properties) == 'Name: Other; RSSI: -70'
