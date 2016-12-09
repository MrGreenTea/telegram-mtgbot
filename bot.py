import ctypes
import json
import re
from argparse import ArgumentParser
from datetime import datetime
from functools import partial
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
FILE_DIR = path.dirname(__file__)

set_data = {}  # type: Dict[str: sets.Set]
card_data = {}  # type: Dict[str: cards.Card]


class InlineHandler(InlineUserHandler, AnswererMixin):
    def __init__(self, *args, **kwargs):
        super(InlineHandler, self).__init__(*args, **kwargs)

    def on_inline_query(self, msg):
        def compute_answer():
            query_id, from_id, query_string, offset = telepot.glance(msg, flavor='inline_query', long=True)
            print(self.id, ':', 'Inline Query:', query_id, from_id, query_string, 'offset: ', offset)

            start_time = datetime.now()
            response = get_photos_from_gatherer(query_string, int(offset) if offset else 0)
            print('took', datetime.now() - start_time)
            print('next offset:', response.get('next_offset', -1))
            return response

        self.answerer.answer(msg, compute_answer)


match_lib = ctypes.cdll.LoadLibrary(path.join(FILE_DIR, 'match.so'))

match_lib.has_match.restype = ctypes.c_bool
match_lib.has_match.argtypes = [ctypes.c_char_p, ctypes.c_char_p]

match_lib.match.restype = ctypes.c_double
match_lib.match.argtypes = [ctypes.c_char_p, ctypes.c_char_p]


def convert_to_c_chars(f):
    def _inner(a, b):
        a = ctypes.c_char_p(bytes(a, 'utf8'))
        b = ctypes.c_char_p(bytes(b, 'utf8'))
        return f(a, b)

    return _inner


@convert_to_c_chars
def has_match(needle, haystack):
    """Return True if needle has a match in haystack."""
    return match_lib.has_match(needle, haystack)


@convert_to_c_chars
def match(needle, haystack):
    """Return match score for needle in haystack."""
    return match_lib.match(needle, haystack)


def match_card(query: str, card: cards.Card):
    """Returns match score for the query and name of card."""
    query = query.lower()
    name = card.name.lower()

    full_words = len(set(name.split()).intersection(set(query.split())))

    return query in name, full_words, match(query, name)


def get_photos_from_gatherer(query_string: str, offset):
    if not query_string:
        matches = sample(list(card_data.values()), RESULTS_AT_ONCE)
        next_offset = True
    else:
        filtered_cards = [card for card in card_data.values() if has_match(query_string.lower(), card.name.lower())]
        matches = nlargest(offset + RESULTS_AT_ONCE, filtered_cards, key=partial(match_card, query_string))[offset:]

        print(len(filtered_cards))
        next_offset = len(filtered_cards) > (offset + RESULTS_AT_ONCE)

    results = [InlineQueryResultPhoto(id=card.id, photo_url=card.image_url, thumb_url=card.image_url, caption=card.name,
                                      photo_width=223, photo_height=311) for card in matches]

    return dict(results=results, next_offset=offset + RESULTS_AT_ONCE if next_offset else '')


def newest_json_file():
    all_card_json_files = glob(path.join(FILE_DIR, 'cards_*.json'))
    highest_version = '0.0.0'
    for file in all_card_json_files:
        file = path.split(file)[-1]
        print(file)
        match = re.match(r'cards_([0-9]+\.[0-9]+\.[0-9]+)\.json', file)
        if match is None:
            continue
        version_number = match.group(1)
        if any(int(x) > int(y) for x, y in zip(version_number.split('.'), highest_version.split('.'))):
            highest_version = version_number
            break

    return highest_version


def is_newest_version():
    print('checking for new json')

    highest_version = newest_json_file()

    print(highest_version)

    remote_newest_version = changelog.newest_version().version
    print(remote_newest_version)
    return remote_newest_version == highest_version


def update_set_info():
    print('Updating sets.')
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

    print('Updating cards took', datetime.now() - start_time)


def update_data():
    """Has to be called manually if needed."""
    global card_data

    update_set_info()

    print('Updating cards.')
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
    update_data()
    parser = ArgumentParser(description='MTG Card Image Fetch Telegram Bot')
    parser.add_argument('token', type=str, metavar='t', help='The Telegram Bot API Token')
    args = parser.parse_args()

    TOKEN = args.token

    bot = telepot.DelegatorBot(TOKEN, [
        pave_event_space()(per_inline_from_id(), create_open, InlineHandler, timeout=20),
    ])

    bot.message_loop(run_forever='Listening ...')
