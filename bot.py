import argparse
import asyncio
import datetime
import glob
import heapq
import json
import os
import random
import re
from typing import Dict

import requests
import telepot
import telepot.aio
from fuzzywuzzy import fuzz
from mtgsdk import cards, sets, changelog
from telepot.aio.delegate import per_inline_from_id, create_open, pave_event_space
from telepot.aio.helper import InlineUserHandler, AnswererMixin
from telepot.namedtuple import InlineQueryResultPhoto
from toolz import dicttoolz
from tqdm import tqdm

"""
$ python3.5 inlinea.py <token>
It demonstrates answering inline query and getting chosen inline results.
"""

FILE_DIR = os.path.dirname(__file__)

set_data = {}  # type: Dict[str: sets.Set]
card_data = {}  # type: Dict[str: cards.Card]


class InlineHandler(InlineUserHandler, AnswererMixin):
    def __init__(self, *args, **kwargs):
        super(InlineHandler, self).__init__(*args, **kwargs)

    def on_inline_query(self, msg):
        def compute_answer():
            query_id, from_id, query_string = telepot.glance(msg, flavor='inline_query')
            print(self.id, ':', 'Inline Query:', query_id, from_id, query_string)

            start_time = datetime.datetime.now()
            articles = get_photos_from_gatherer(query_string)
            print('took', datetime.datetime.now() - start_time)
            return articles

        self.answerer.answer(msg, compute_answer)


def get_photos_from_gatherer(query_string: str):
    if not query_string:
        matches = random.sample(list(card_data.values()), 8)
    else:
        def match(card):
            """We match first by fuzz.ratio and then by length difference."""
            name = card.name.lower()
            consecutive_score = 0
            for c in query_string.lower():
                try:
                    while name[consecutive_score] != c:
                        consecutive_score += 1
                except IndexError:
                    consecutive_score = -1
                    break
            else:
                consecutive_score = (consecutive_score + 1) / len(query_string)
            try:
                len_ratio = 1 / len(name)
            except ZeroDivisionError:
                print(name)
                len_ratio = 0

            return fuzz.WRatio(query_string, name), 1 / consecutive_score, len_ratio

        matches = heapq.nlargest(8, card_data.values(), key=match)

    return [InlineQueryResultPhoto(id=card.id, photo_url=card.image_url, thumb_url=card.image_url, caption=card.name)
            for card in matches]


def newest_json_file():
    all_card_json_files = glob.glob(os.path.join(FILE_DIR, 'cards_*.json'))
    highest_version = '0.0.0'
    for file in all_card_json_files:
        file = os.path.split(file)[-1]
        print(file)
        match = re.match('cards_([0-9]+\.[0-9]+\.[0-9]+)\.json', file)
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
        set.release_date = datetime.datetime.strptime(set.release_date, '%Y-%m-%d')
        set_data[set.code] = set


def update_card_info():
    start_time = datetime.datetime.now()

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

    print('Updating cards took', datetime.datetime.now() - start_time)


def update_data():
    global card_data

    update_set_info()

    print('Updating cards.')
    if not is_newest_version():
        update_card_info()
        new_version = changelog.newest_version().version
        json_file_path = os.path.join(FILE_DIR, 'cards_{}.json'.format(new_version))
        with open(json_file_path, 'w') as json_file:
            json.dump(dicttoolz.valmap(lambda c: c.__dict__, card_data), json_file)

    else:
        json_file_path = os.path.join(FILE_DIR, 'cards_{}.json'.format(newest_json_file()))
        with open(json_file_path) as json_file:
            card_data = dicttoolz.valmap(lambda d: cards.Card(**d), json.load(json_file))


update_data()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    parser = argparse.ArgumentParser(description='MTG Card Image Fetch Telegram Bot')
    parser.add_argument('token', type=str, metavar='t', help='The Telegram Bot API Token')
    args = parser.parse_args()

    TOKEN = args.token

    bot = telepot.aio.DelegatorBot(TOKEN, [
        pave_event_space()(
            per_inline_from_id(), create_open, InlineHandler, timeout=20),
    ])

    loop.create_task(bot.message_loop())
    print('Listening ...')

    loop.run_forever()
