from time import sleep

from hypothesis import given, strategies

from bot import timer, paginate_iterator


@given(strategies.floats(min_value=0, max_value=0.001))
def test_timer(mocker, sleep_time):
    mock_logger = mocker.MagicMock()
    with timer(logger=mock_logger):
        sleep(sleep_time)

    assert mock_logger.info.called_once()
    assert float(mock_logger.info.call_args[0][0].split(':')[-1]) >= sleep_time


@given(strategies.integers(min_value=1, max_value=100), strategies.lists(strategies.integers()))
def test_paginate(chunk_size, it):
    iterator = paginate_iterator(it, chunk_size=chunk_size)
    chunked_list = list(iterator)
    for i in chunked_list[:-1]:  # all but the last chunk must include exactly chunk_size elements
        assert(len(list(i)) == chunk_size)

    for i in chunked_list[-1:]:  # it can be empty
        assert(len(list(i))) <= chunk_size  # the last chunk should at most be chunk_size elements
