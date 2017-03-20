import ctypes
import json
import logging
import re
from argparse import ArgumentParser
from datetime import datetime
from functools import partial, wraps
from glob import glob
from heapq import nlargest
from os import path
from random import sample
from typing import Dict

import requests
import telepot
from mtgsdk import cards, sets, changelog
from telepot.delegate import per_inline_from_id, create_open, pave_event_space
from telepot.helper import InlineUserHandler, AnswererMixin
from telepot.namedtuple import InlineQueryResultPhoto
from toolz import dicttoolz
from tqdm import tqdm

RESULTS_AT_ONCE = 8
FILE_DIR = path.dirname(path.abspath(__file__))

set_data = {}  # type: Dict[str: sets.Set]
card_data = {}  # type: Dict[str: cards.Card]


logger = logging.getLogger(__name__)


class InlineHandler(InlineUserHandler, AnswererMixin):
    def __init__(self, *args, **kwargs):
        super(InlineHandler, self).__init__(*args, **kwargs)

    def on_inline_query(self, msg):
        def compute_answer():
            query_id, from_id, query_string, offset = telepot.glance(msg, flavor='inline_query', long=True)
            info_msg = '{id}: {q_id} from {f_id}. Query: {q} with offset: {off}'.format(
                    id=self.id, q_id=query_id, f_id=from_id, q=query_string, off=offset)
            logger.info(info_msg)

            start_time = datetime.now()
            try:
                response = get_photos_from_gatherer(query_string, int(offset) if offset else 0)
            except TypeError:
                # probably we got a wrong offset
                logger.info('{} is not a valid offset'.format(offset))
                return
            logger.info('took {}'.format(datetime.now() - start_time))
            logger.info('next offset: {}'.format(response.get('next_offset', -1)))
            return response

        self.answerer.answer(msg, compute_answer)


match_lib = ctypes.cdll.LoadLibrary(path.join(FILE_DIR, 'match.so'))

match_lib.has_match.restype = ctypes.c_bool
match_lib.has_match.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

match_lib.match.restype = ctypes.c_double
match_lib.match.argtypes = [ctypes.c_char_p, ctypes.c_char_p]


def convert_to_c_chars(f):
    @wraps(f)
    def _inner(a, b):
        a = ctypes.c_char_p(bytes(a, 'utf8'))
        b = ctypes.c_char_p(bytes(b, 'utf8'))
        return f(a, b)

    return _inner


@convert_to_c_chars
def has_match(needle: str, haystack: str):
    """Return True if needle has a match in haystack."""
    return match_lib.has_match(needle, haystack)


@convert_to_c_chars
def match(needle: str, haystack: str):
    """Return match score for needle in haystack."""
    return match_lib.match(needle, haystack)


def common_words(a: str, b: str):
    a_set = set(''.join(e for e in s if e.isalnum()) for s in a.split())
    b_set = set(''.join(e for e in s if e.isalnum()) for s in b.split())

    return a_set.intersection(b_set)


def match_card(query: str, card: cards.Card):
    """Returns match score for the query and name of card."""
    query = query.lower()
    name = card.name.lower()

    full_word_score = len(common_words(query, name))

    full_match = name.find(query)
    if full_match >= 0:
        full_match = 1 / (full_match + 1)

    return full_match, full_word_score, match(query, name)


def get_photos_from_gatherer(query_string: str, offset: int = 0):
    if not query_string:
        matches = sample(list(card_data.values()), RESULTS_AT_ONCE)
        next_offset = True
    else:
        filtered_cards = [card for card in card_data.values() if has_match(query_string.lower(), card.name.lower())]
        matches = nlargest(offset + RESULTS_AT_ONCE, filtered_cards, key=partial(match_card, query_string))[offset:]

        next_offset = len(filtered_cards) > (offset + RESULTS_AT_ONCE)

    results = [InlineQueryResultPhoto(id=card.id, photo_url=card.image_url, thumb_url=card.image_url, caption=card.name,
                                      photo_width=223, photo_height=311) for card in matches]

    return dict(results=results, next_offset=offset + RESULTS_AT_ONCE if next_offset else '')


def newest_json_file():
    all_card_json_files = glob(path.join(FILE_DIR, 'cards_*.json'))
    logger.debug(all_card_json_files)
    highest_version = '0.0.0'
    for file in all_card_json_files:
        file = path.split(file)[-1]
        logger.info(file)
        match = re.match(r'cards_([0-9]+\.[0-9]+\.[0-9]+)\.json', file)
        if match is None:
            continue
        version_number = match.group(1)
        if tuple(map(int, version_number.split('.'))) > tuple(map(int, highest_version.split('.'))):
            highest_version = version_number
            break

    return highest_version


def is_newest_version():
    logger.info('checking for new json')

    highest_version = newest_json_file()

    logger.info(highest_version)

    remote_newest_version = changelog.newest_version().version
    logger.info('remote newest is: ' + remote_newest_version)
    return remote_newest_version == highest_version


def update_set_info():
    logger.info('Updating sets.')
    for set in sets.search():
        set.release_date = datetime.strptime(set.release_date, '%Y-%m-%d')
        set_data[set.code] = set


def update_card_info():
    start_time = datetime.now()

    total_card_count = int(requests.head('https://api.magicthegathering.io/v1/cards').headers['total-count'])

    for card in tqdm(cards.search(), total=total_card_count):
        if card.name in card_data:
            new_set = set_data[card.set]
            cur_set = set_data[card_data[card.name].set]
            if new_set.release_date <= cur_set.release_date:
                continue

        if card.image_url is None:
            continue

        card_data[card.name] = card

    logger.info('Updating cards took {}'.format(datetime.now() - start_time))


def update_data():
    """Has to be called manually if needed."""
    global card_data

    update_set_info()

    logger.info('Updating cards.')
    if not is_newest_version():
        update_card_info()
        new_version = changelog.newest_version().version
        json_file_path = path.join(FILE_DIR, 'cards_{}.json'.format(new_version))
        with open(json_file_path, 'w') as json_file:
            json.dump(dicttoolz.valmap(lambda c: c.__dict__, card_data), json_file)

    else:
        json_file_path = path.join(FILE_DIR, 'cards_{}.json'.format(newest_json_file()))
        with open(json_file_path) as json_file:
            card_data = dicttoolz.valmap(lambda d: cards.Card(**d), json.load(json_file))


if __name__ == '__main__':
    parser = ArgumentParser(description='MTG Card Image Fetch Telegram Bot')
    parser.add_argument('token', type=str, metavar='t', help='The Telegram Bot API Token')
    parser.add_argument('--level', metavar='l', default='info', choices=[l.lower() for l in logging._nameToLevel])
    args = parser.parse_args()

    logging.basicConfig(level=args.level.upper(),
                        format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%m.%d.%Y %H:%M:%S',
                        handlers=[logging.StreamHandler(), logging.FileHandler('mtgbot_{:%Y_%m_%d_%X}.log'.format(datetime.now()))]
                        )

    update_data()

    TOKEN = args.token

    bot = telepot.DelegatorBot(TOKEN, [
        pave_event_space()(per_inline_from_id(), create_open, InlineHandler, timeout=20),
    ])

    bot.message_loop(run_forever='Listening ...')
