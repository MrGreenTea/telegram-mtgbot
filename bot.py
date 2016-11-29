import argparse
import asyncio
import datetime
import glob
import json
import os
import re

import requests
import telepot
import telepot.aio
from fuzzywuzzy import process
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

set_data = {}
card_data = {}


class InlineHandler(InlineUserHandler, AnswererMixin):
    def __init__(self, *args, **kwargs):
        super(InlineHandler, self).__init__(*args, **kwargs)

    def on_inline_query(self, msg):
        def compute_answer():
            query_id, from_id, query_string = telepot.glance(msg, flavor='inline_query')
            print(self.id, ':', 'Inline Query:', query_id, from_id, query_string)

            articles = get_photos_from_gatherer(query_string)

            return articles

        self.answerer.answer(msg, compute_answer)

    def on_chosen_inline_result(self, msg):
        from pprint import pprint
        pprint(msg)
        result_id, from_id, query_string = telepot.glance(msg, flavor='chosen_inline_result')
        print(self.id, ':', 'Chosen Inline Result:', result_id, from_id, query_string)


def get_photos_from_gatherer(query_string: str):
    if not query_string:
        return []

    matches = process.extract(query_string, card_data, limit=10)

    return [
        InlineQueryResultPhoto(id=card.id, photo_url=card.image_url, thumb_url=card.image_url, caption=name)
        for card, value, name in matches
        ]


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

    remote_newest_version = changelog.newest_version()['version']
    print(remote_newest_version)
    return remote_newest_version == highest_version


def update_set_info():
    for set in sets.search():
        set.release_date = datetime.datetime.strptime(set.release_date, '%Y-%m-%d')
        set_data[set.code] = set


def update_card_info():
    start_time = datetime.datetime.now()

    total_card_count = int(requests.get('https://api.magicthegathering.io/v1/cards').headers['total-count'])

    for card in tqdm(cards.search(), total=total_card_count):
        if card.image_url is None:
            continue

        if card.name in card_data:
            new_set = set_data[card.set]
            cur_set = set_data[card_data[card.name].set]
            if new_set.release_date > cur_set.release_date:
                continue

        card_data[card.name] = card

    print('Updating cards took', datetime.datetime.now() - start_time)


def update_data():
    global card_data

    update_set_info()

    if not is_newest_version():
        print('update')

        update_card_info()
        new_version = changelog.newest_version()['version']
        json_file_path = os.path.join(FILE_DIR, 'cards_{}.json'.format(new_version))
        with open(json_file_path, 'w') as json_file:
            json.dump(dicttoolz.valmap(lambda c: c.__dict__, card_data), json_file)

    else:
        json_file_path = os.path.join(FILE_DIR, 'cards_{}.json'.format(newest_json_file()))
        with open(json_file_path) as json_file:
            card_data = dicttoolz.valmap(lambda d: cards.Card(**d), json.load(json_file))


update_data()

loop = asyncio.get_event_loop()

parser = argparse.ArgumentParser(description='MTG Card Image Fetch Telegram Bot')
parser.add_argument('token', type=str, metavar='T', help='The Telegram Bot API Token')
# args = parser.parse_args()

# TOKEN = args.token

bot = telepot.aio.DelegatorBot(TOKEN, [
    pave_event_space()(
        per_inline_from_id(), create_open, InlineHandler, timeout=20),
])

loop.create_task(bot.message_loop())
print('Listening ...')

loop.run_forever()