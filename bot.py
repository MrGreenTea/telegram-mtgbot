import logging
from argparse import ArgumentParser
from contextlib import contextmanager
from datetime import datetime
from itertools import zip_longest
from urllib import parse

import requests
import telepot
from telepot.delegate import per_inline_from_id, create_open, pave_event_space
from telepot.helper import InlineUserHandler, AnswererMixin
from telepot.namedtuple import InlineQueryResultPhoto, InlineKeyboardButton, InlineKeyboardMarkup

RESULTS_AT_ONCE = 25
CACHE_SIZE = 32

LOGGER = logging.getLogger(__name__)


# pylint: disable=logging-format-interpolation

@contextmanager
def timer(msg=None, logger=LOGGER):
    """Time the context and log it with logger and optional msg."""
    start_time = datetime.now()
    try:
        yield
    finally:
        time_msg = 'took ' + str(datetime.now() - start_time)
        if msg:
            logger.info(msg + ' ' + time_msg)
        else:
            logger.info(time_msg)


class InlineHandler(InlineUserHandler, AnswererMixin):
    def __init__(self, *args, **kwargs):
        super(InlineHandler, self).__init__(*args, **kwargs)

    def on_inline_query(self, msg):
        def compute_answer():
            username = msg['from']['username']
            query_id, from_id, query_string, offset = telepot.glance(msg, flavor='inline_query', long=True)
            LOGGER.info(f'{self.id}: {query_id} from {username}. Query: {query_id!r} with offset: {offset}')

            try:
                _off = int(offset) if offset else 0
            except TypeError:
                # probably we got a wrong offset
                LOGGER.info(f'{offset!r} is not a valid offset')
                return dict(results=[], next_offset='')

            if not query_string:
                with timer():
                    random_photo = []
                    for _ in range(5):
                        random_photo.extend(inline_photo_from_card(requests.get('https://api.scryfall.com/cards/random').json()))
                    response = {'results': random_photo,
                                'next_offset': _off + 1
                                }
            else:
                with timer():
                    response = get_photos_from_scryfall(query_string, _off)

                LOGGER.info(f'next offset: {response.get("next_offset", -1)}')

            return response

        self.answerer.answer(msg, compute_answer)


class Results(list):
    def __init__(self, query, chunk_size=RESULTS_AT_ONCE):
        super(Results, self).__init__()
        self.query = query
        self.search_url = parse.urljoin('https://api.scryfall.com/cards/search/',
                                        '?order=edhrec&q=' + parse.quote_plus(query + ' include:extras'))
        self.next_url = self.search_url
        self.chunk_size = chunk_size

    def get_url(self, url):
        req = requests.get(url)
        req.raise_for_status()
        json = req.json()
        return json

    def __getitem__(self, item):
        if item >= len(self):
            if self.next_url is not None:
                json = self.get_url(self.next_url)
                self.extend(list(p) for p in paginate_iterator(json['data'], self.chunk_size))
                self.next_url = json.get('next_page', None)
            else:
                raise IndexError(f'{self!r} has no page {item} for chunk_size={self.chunk_size}')

        return super(Results, self).__getitem__(item)

    def __repr__(self):
        return f'{__name__}.{self.__class__.__name__}({self.query!r}, {self.chunk_size!r})'


def paginate_iterator(it, chunk_size):
    _fill_value = object()
    iters = [iter(it)] * chunk_size
    for chunk in zip_longest(*iters, fillvalue=_fill_value):
        yield (i for i in chunk if i is not _fill_value)


def paginate(query_string, packet_size=25):
    """Iterate in packs of packet_size over search results."""
    return Results(query_string, chunk_size=packet_size)


def inline_photo_from_card(card):
    """Build a InlineQueryResultPhoto from the given card dict."""
    markup_keyboard = InlineKeyboardMarkup(inline_keyboard=[[  # looks quite awkward. Is a list of lists for button rows
        InlineKeyboardButton(text=card['name'], url=card['scryfall_uri'])]])

    arguments = dict(id=(card['id']), photo_width=672, photo_height=936, reply_markup=markup_keyboard)
    try:
        yield InlineQueryResultPhoto(**arguments,
                                     photo_url=card['image_uris']['png'], thumb_url=card['image_uris']['small'])
    except KeyError:
        for face in card['card_faces']:
            args = dict(**arguments,
                        photo_url=face['image_uris']['png'], thumb_url=face['image_uris']['small'])
            args['id'] = ''.join(e for e in f"{card['id']}-{face['name']}" if e.isalnum())
            yield InlineQueryResultPhoto(**args)


def get_photos_from_scryfall(query_string: str, offset: int = 0):
    """Return photos for query_string."""
    try:
        cards = paginate(query_string, packet_size=RESULTS_AT_ONCE)
        results = []
        for card in cards[offset]:
            results.extend(inline_photo_from_card(card))
        next_offset = offset + 1
    except (requests.HTTPError, IndexError):  # we silently ignore 404 and other errors
        next_offset = ''
        results = []

    return dict(results=results, next_offset=next_offset)


def run_bot():
    parser = ArgumentParser(description='MTG Card Image Fetch Telegram Bot')
    parser.add_argument('token', type=str, metavar='t', help='The Telegram Bot API Token')
    parser.add_argument('--level', metavar='l', default='info', choices=[l.lower() for l in logging._nameToLevel])
    args = parser.parse_args()

    logging.basicConfig(level=args.level.upper(),
                        format='%(asctime)s | %(levelname)s: %(message)s', datefmt='%m.%d.%Y %H:%M:%S',
                        handlers=[logging.StreamHandler(),
                                  logging.FileHandler('mtgbot_{:%Y_%m_%d_%X}.log'.format(datetime.now()))
                                  ]
                        )

    bot = telepot.DelegatorBot(args.token, [
        pave_event_space()(per_inline_from_id(), create_open, InlineHandler, timeout=20),
    ])

    bot.message_loop(run_forever='Listening ...')


if __name__ == '__main__':
    run_bot()
