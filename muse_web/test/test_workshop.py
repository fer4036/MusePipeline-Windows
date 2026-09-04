"""Tests for the physical xArm 6 workshop definition."""

from muse_web.workshop import PRACTICE, public_definition, section_by_id


def test_physical_workshop_has_the_six_declared_sections():
    definition = public_definition()
    assert definition['id'] == 'pick-and-place-physical'
    assert definition['title'] == 'Pick and Place en el robot físico xArm 6'
    assert definition['estimated_minutes'] == 60
    assert definition['ground_truth_interval_seconds'] == 600
    assert [section['id'] for section in definition['sections']] == [
        f'paso_{number}' for number in range(1, 7)
    ]
    assert sum(section['minutes'] for section in definition['sections']) == 65
    assert section_by_id('paso_6')['minutes'] == 7
    assert PRACTICE['sections'][2]['title'] == (
        'Waypoints — tu rutina en el panel y el lado del pick'
    )
