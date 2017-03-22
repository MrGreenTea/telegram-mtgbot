import logging
from itertools import zip_longest
from urllib import parse
from argparse import ArgumentParser
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache

from cachetools import LRUCache
import requests
import telepot
from telepot.delegate import per_inline_from_id, create_open, pave_event_space
from telepot.helper import InlineUserHandler, AnswererMixin
from telepot.namedtuple import InlineQueryResultPhoto, InlineKeyboardButton, InlineKeyboardMarkup

RESULTS_AT_ONCE = 25
CACHE_SIZE = 128

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
        self.cache = LRUCache(maxsize=CACHE_SIZE)

    def on_inline_query(self, msg):
        def compute_answer():
            query_id, from_id, query_string, offset = telepot.glance(msg, flavor='inline_query', long=True)
            info_msg = '{id}: {q_id} from {f_id}. Query: {q!r} with offset: {off}'.format(id=self.id, q_id=query_id,
                                                                                          f_id=from_id, q=query_string,
                                                                                          off=offset)
            LOGGER.info(info_msg)

            if not query_string:
                try:
                    query_string = self.cache[from_id]
                except KeyError:
                    LOGGER.info('No saved query for {}'.format(from_id))
                else:
                    LOGGER.info('Returning results for last query string: {!r}'.format(query_string))

            try:
                _off = int(offset) if offset else 0
            except TypeError:
                # probably we got a wrong offset
                LOGGER.info('{!r} is not a valid offset'.format(offset))
                response = dict(results=[], next_offset='')
            else:
                with timer():
                    response = get_photos_from_scryfall(query_string, _off)

            LOGGER.info('next offset: {}'.format(response.get('next_offset', -1)))

            if response['results']:
                self.cache[from_id] = query_string
                LOGGER.info('Saved query: {!r} for user {f_id}'.format(query_string, f_id=from_id))

            return response

        self.answerer.answer(msg, compute_answer)


def paginate_iterator(it, chunk_size):
    _fill_value = object()
    iters = [iter(it)] * chunk_size
    for chunk in zip_longest(*iters, fillvalue=_fill_value):
        yield (i for i in chunk if i is not _fill_value)


@lru_cache(maxsize=CACHE_SIZE)
def paginate(query_string, packet_size=25):
    """Iterate in packs of packet_size over search results."""
    url = parse.urljoin('https://api.scryfall.com/cards/search/', '?q=' + parse.quote_plus(query_string))
    while url is not None:
        req = requests.get(url)
        req.raise_for_status()

        json = req.json()
        matches = json['data']
        url = json.get('next_page', None)
        yield from paginate_iterator(matches, packet_size)


def inline_photo_from_card(card):
    """Build a InlineQueryResultPhoto from the given card dict."""
    markup_keyboard = InlineKeyboardMarkup(inline_keyboard=[[  # looks quite awkward. Is a list of lists for button rows
        InlineKeyboardButton(text=card['name'], url=card['scryfall_uri'])]])

    return InlineQueryResultPhoto(id=card['id'], photo_url=card['image_uri'], thumb_url=card['image_uri'],
                                  photo_width=336, photo_height=469, reply_markup=markup_keyboard)


def get_photos_from_scryfall(query_string: str, offset: int=0):
    """Return photos for query_string."""
    try:
        matches = next(paginate(query_string, packet_size=RESULTS_AT_ONCE), [])
    except requests.HTTPError:  # we silently ignore 404 and other errors
        matches = []

    results = [inline_photo_from_card(card) for card in matches]
    return dict(results=results, next_offset=offset + 1 if matches else '')


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
