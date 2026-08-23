"""Fixed protocol metadata for the Pick and Place engagement study."""

GROUND_TRUTH_INTERVAL_SECONDS = 10 * 60

PRACTICE = {
    'id': 'pick-and-place-physical',
    'title': 'Pick and Place en el robot físico xArm 6',
    'estimated_minutes': 60,
    'source_file': 'practica_pick_and_place_physical.md',
    'sections': [
        {
            'id': 'paso_1', 'number': 1,
            'title': 'El ciclo pick-and-place — ocho fases, dos alturas',
            'minutes': 17,
        },
        {
            'id': 'paso_2', 'number': 2,
            'title': 'El home se enseña con las manos, no viene de fábrica',
            'minutes': 8,
        },
        {
            'id': 'paso_3', 'number': 3,
            'title': 'Waypoints — tu rutina en el panel y el lado del pick',
            'minutes': 17,
        },
        {
            'id': 'paso_4', 'number': 4,
            'title': 'Simetría del ciclo — el lado del place y el cierre repetible',
            'minutes': 8,
        },
        {
            'id': 'paso_5', 'number': 5,
            'title': 'Validación con pieza real — la corrida y tu nota',
            'minutes': 8,
        },
        {
            'id': 'paso_6', 'number': 6,
            'title': 'El robot no sabe si agarró — y la corrida final es mía',
            'minutes': 7,
        },
    ],
}

ENGAGEMENT_ITEMS = [
    {
        'id': 'task_engagement',
        'facet': 'engagement_with_task',
        'text_es': 'Estuve involucrado/a con el tema que estábamos trabajando.',
        'text_en': 'I was engaged with the topic at hand.',
    },
    {
        'id': 'effort',
        'facet': 'effort_and_persistence',
        'text_es': 'Puse mucho esfuerzo.',
        'text_en': 'I put in a lot of effort.',
    },
    {
        'id': 'persistence',
        'facet': 'effort_and_persistence',
        'text_es': 'Me gustaría que pudiéramos continuar trabajando un poco más.',
        'text_en': 'I wish we could still continue with the work for a while.',
    },
    {
        'id': 'flow',
        'facet': 'flow',
        'text_es': 'Estuve tan involucrado/a que olvidé todo lo que ocurría a mi alrededor.',
        'text_en': 'I was so involved that I forgot everything around me.',
    },
]

LIKERT_SCALE = [
    {'value': 1, 'label_es': 'Nada cierto para mí', 'label_en': 'Not true at all for me'},
    {'value': 2, 'label_es': 'No es cierto para mí', 'label_en': 'Not true for me'},
    {'value': 3, 'label_es': 'Neutral', 'label_en': 'Neutral'},
    {'value': 4, 'label_es': 'Es cierto para mí', 'label_en': 'True for me'},
    {'value': 5, 'label_es': 'Muy cierto para mí', 'label_en': 'Very true for me'},
]

CITATION = {
    'authors': 'Jerome I. Rotgans y Henk G. Schmidt',
    'year': 2011,
    'title': 'Cognitive engagement in the problem-based learning classroom',
    'journal': 'Advances in Health Sciences Education, 16(4), 465–479',
    'doi': '10.1007/s10459-011-9272-9',
}


def section_by_id(section_id):
    """Return one known workshop section or ``None``."""
    return next(
        (section for section in PRACTICE['sections'] if section['id'] == section_id),
        None,
    )


def public_definition():
    """Return JSON-safe protocol metadata without sharing mutable constants."""
    return {
        **PRACTICE,
        'sections': [dict(section) for section in PRACTICE['sections']],
        'items': [dict(item) for item in ENGAGEMENT_ITEMS],
        'likert_scale': [dict(item) for item in LIKERT_SCALE],
        'citation': dict(CITATION),
        'ground_truth_interval_seconds': GROUND_TRUTH_INTERVAL_SECONDS,
    }
