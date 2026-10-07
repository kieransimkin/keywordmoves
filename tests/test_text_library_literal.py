import pytest

from keywordmoves.builtin.text_library import TextLibraryPlugin, _literal_candidates
from keywordmoves.errors import ConfigurationError
from keywordmoves.models import ExecutionContext, PluginRequest


def phrases(documents, width=3):
    return {candidate.phrase: candidate
            for candidate in _literal_candidates(documents, 200, width)}


def test_literal_does_not_invent_removed_word_or_line_joins():
    result = phrases([('song', 'clay from stars\npaper, dreams\nblue\nmoon')])
    assert 'clay stars' not in result
    assert 'clay from stars' in result
    assert 'paper dreams' not in result
    assert 'blue moon' not in result


def test_documents_do_not_make_a_phrase_across_sources():
    assert 'paper dreams' not in phrases([('a', 'paper'), ('b', 'dreams')])


def test_unicode_and_offsets_roundtrip_without_a_demand_score():
    text = 'بازگشت آزادی\nCafe\u0301 dreams\nAI blues'
    result = phrases([('source.txt', text)])
    for phrase in ('بازگشت آزادی', 'café dreams', 'ai blues'):
        candidate = result[phrase]
        assert candidate.score is None
        assert candidate.metadata['external_demand'] == 'unvalidated'
        for span in candidate.metadata['source_spans']:
            assert text[span['start']:span['end']] == span['text']


def test_count_is_not_the_capped_supporting_span_count():
    candidate = phrases([('a', ('paper dreams\n' * 25))])['paper dreams']
    assert candidate.evidence[0].value == 25
    assert len(candidate.metadata['source_spans']) == 20
    assert candidate.metadata['spans_truncated'] is True


def test_numeric_names_remain_literal():
    result = phrases([('song', 'Rule 30\nParis 2024\nWindows 95')])
    assert {'rule 30', 'paris 2024', 'windows 95'} <= result.keys()


def test_file_offsets_preserve_bom_unicode_and_crlf(tmp_path):
    source = tmp_path / 'lyrics.txt'
    text = 'paper\r\ndreams\r\nblue moon\r\nبازگشت آزادی'
    source.write_bytes(b'\xef\xbb\xbf' + text.encode('utf-8'))
    result = TextLibraryPlugin().run(
        PluginRequest('extract-literal', inputs=(source,), options={'limit': 200}),
        ExecutionContext(None),
    )
    assert any(k.phrase == 'blue moon' for k in result.keywords)
    for candidate in result.keywords:
        for span in candidate.metadata['source_spans']:
            assert text[span['start']:span['end']] == span['text']


@pytest.mark.parametrize('value', [0, 6, 'not-a-number'])
def test_invalid_ngram_option_is_a_configuration_error(tmp_path, value):
    source = tmp_path / 'lyrics.txt'
    source.write_text('paper dreams', encoding='utf-8')
    with pytest.raises(ConfigurationError, match='max_ngram'):
        TextLibraryPlugin().run(
            PluginRequest('extract-literal', inputs=(source,), options={'max_ngram': value}),
            ExecutionContext(None),
        )


@pytest.mark.parametrize('boundary', ['.', ',', '\n', '\r\n', '!', ';'])
def test_punctuation_and_line_boundaries_are_not_crossed(boundary):
    assert 'paper dreams' not in phrases([('a', f'paper{boundary}dreams')])
