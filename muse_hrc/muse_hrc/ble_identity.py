"""Pure helpers for identifying Muse headbands from BlueZ properties."""

import re

MAC_PATTERN = re.compile(r'(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b')
MUSE_NAME_PATTERN = re.compile(r'(?i)(?:^|[^a-z0-9])muse(?:[^a-z0-9]|$)')
MUSE_OUI_PREFIXES = ('00:55:da',)

# FE8D is the Bluetooth SIG service UUID assigned to Interaxon. The 273e
# namespace contains the proprietary Muse GATT services/characteristics.
MUSE_BLE_IDENTIFIERS = (
    'fe8d',
    '0000fe8d-0000-1000-8000-00805f9b34fb',
    '273e',
)


def extract_device_addresses(*outputs):
    """Extract normalized, unique BLE addresses from bluetoothctl output."""
    addresses = set()
    for output in outputs:
        addresses.update(match.lower() for match in MAC_PATTERN.findall(output or ''))
    return addresses


def extract_current_scan_addresses(output):
    """Extract devices observed in this scan, excluding removals and cache only."""
    observed = set()
    removed = set()
    for line in (output or '').splitlines():
        matches = MAC_PATTERN.findall(line)
        if not matches:
            continue
        normalized = {match.lower() for match in matches}
        if '[DEL]' in line:
            removed.update(normalized)
        else:
            observed.update(normalized)
    return observed - removed


def parse_manufacturer_ids(value):
    """Normalize a CSV manufacturer-ID parameter to lowercase hex strings."""
    return {
        item.strip().lower()
        for item in (value or '').split(',')
        if item.strip()
    }


def identify_muse(mac, properties, manufacturer_ids=()):
    """Return the matching Muse property name, or ``None`` when unrecognized."""
    normalized_mac = (mac or '').lower()
    normalized_properties = (properties or '').lower()

    names = re.findall(
        r'(?im)^\s*(?:name|alias):\s*(.+?)\s*$',
        properties or '',
    )
    if any(MUSE_NAME_PATTERN.search(name) for name in names):
        return 'nombre/alias Muse'

    if any(identifier in normalized_properties for identifier in MUSE_BLE_IDENTIFIERS):
        return 'UUID de servicio Interaxon/Muse'

    advertised_manufacturers = set(re.findall(
        r'(?im)^\s*manufacturerdata\.key:\s*(0x[0-9a-f]+)',
        properties or '',
    ))
    configured_manufacturers = {
        str(identifier).strip().lower() for identifier in manufacturer_ids
    }
    matching_manufacturers = advertised_manufacturers & configured_manufacturers
    if matching_manufacturers:
        identifier = sorted(matching_manufacturers)[0]
        return f'ID de fabricante {identifier}'

    if normalized_mac.startswith(MUSE_OUI_PREFIXES):
        return 'prefijo MAC Muse conocido'

    return None


def summarize_properties(properties):
    """Return a compact diagnostic summary of relevant BlueZ properties."""
    relevant_keys = (
        'name:',
        'alias:',
        'uuid:',
        'servicedata.',
        'manufacturerdata.key:',
        'rssi:',
    )
    lines = []
    for line in (properties or '').splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(relevant_keys):
            lines.append(stripped)
    return '; '.join(lines) if lines else 'sin propiedades identificadoras'
