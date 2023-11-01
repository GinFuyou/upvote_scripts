import argparse
import csv
import json
import logging
from enum import Enum

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

logging.basicConfig(encoding='utf-8', level=logging.DEBUG)

HIDE_PWD = True

LOGIN_PATH = "signin"


class StatCodes(Enum):
    deleted = "3870"
    awaiting = "3865"


# BOARD_ID = 7251

def get_dashboard_url(args):
    return f"{args.url}dashboard/boards/{args.board}/suggestions"

DELETE_DASHBOARD_DATA = {
    "page": "1",
    "order": "newest",
    "status_id": StatCodes.awaiting.value,
    "tag": "",
    "markAs": StatCodes.deleted.value,  # deleted
    "tagAs": "",
    "suggestion_id": []
}


def log_cookies(session):
    logging.debug(" Session Cookies:")
    for cookie in session.cookies:
        logging.debug(f" [{cookie.name}]: `{cookie.value}`")

def show_topic(topic_dict):
    d = topic_dict
    print(f"# {d['Suggestion ID']:<7} ({d['pythonized_creation_date']}) [{d['Status code']}]"
          f" Votes: {d['Votes']} '{d['Title']}' by {d['Name']}  ")

def read_csv(args, first_row_is_header=True, newline_limit=40):
    with open(args.csv) as csvfile:
        upvote_reader = csv.reader(csvfile)

        counter = 0
        if first_row_is_header:
            headings = next(upvote_reader)
            max_head_lenght = max([len(item) for item in headings]) + 1
        else:
            headings = None

        topics = []
        for row in upvote_reader:
            counter += 1
            # print(f"{counter}. " + ', '.join(row))
            if headings:
                row_with_headings = zip(headings, row)
                topic_dict = dict(row_with_headings)
                if args.show_csv:
                    print(f"[{counter: >4}]")

                    for head, value in topic_dict.items():
                        print(f"{head:<{max_head_lenght}}:", end=' ')
                        if len(value) > newline_limit:
                            print("\n", end='')
                            print(f'"{value}"')
                        else:
                            print(f"{value}")
                    print("\n")
                topics.append(topic_dict)
            else:
                raise NotImplementedError("No heading, not expected")
    return topics

def preprocess_topic_dicts(list_of_dicts):
    for topic_dict in list_of_dicts:
        topic_dict['Votes'] = int(topic_dict['Votes'])

        topic_dict['pythonized_creation_date'] = dateutil_parser.parse(topic_dict['Date created']).date()
    return list_of_dicts

def filter_topics(list_of_dicts,
                  date_gte=None,
                  date_lte=None,
                  votes_gte=None,
                  votes_lte=None,
                  include_tags=None,
                  include_statuscodes=None):

    if isinstance(date_gte, str):
        date_gte = dateutil_parser.parse(date_gte).date()
    if isinstance(date_lte, str):
        date_lte = dateutil_parser.parse(date_lte).date()

    logging.debug(f"got {len(list_of_dicts)} topics")
    list_copy = list_of_dicts.copy()
    if date_gte:
        list_copy = [topic for topic in list_copy if topic['pythonized_creation_date'] >= date_gte]
    if date_lte:
        list_copy = [topic for topic in list_copy if topic['pythonized_creation_date'] <= date_lte]
    if votes_gte:
        list_copy = [topic for topic in list_copy if topic['Votes'] >= votes_gte]
    if votes_lte:
        list_copy = [topic for topic in list_copy if topic['Votes'] <= votes_lte]
    if include_statuscodes:
        list_copy = [topic for topic in list_copy if topic['Status code'] in include_statuscodes]
    if include_tags:
        list_copy = [topic for topic in list_copy if topic['Tags'] in include_tags]
    logging.info(f"filter to {len(list_copy)} topics")
    return list_copy


def make_request_or_stop(session, url, method="GET", **kwargs):
    req = requests.Request(method, url, data=kwargs.get('data'))
    if kwargs.get('data'):
        form_data_safe = kwargs.get('data').copy()
        if HIDE_PWD and 'password' in form_data_safe.keys():
            form_data_safe['password'] = "*" * len(form_data_safe['password'])
        logging.info(f" >> Sending request to '{url}' [{method}] {form_data_safe}")
    else:
        logging.info(f" >> Sending request to '{url}' [{method}] no data")
    if method == "POST":
        resp = session.post(url, data=kwargs.get('data'))
    else:
        resp = session.send(req.prepare())

    if resp.ok:
        logging.info(f"status: {resp.status_code}")
        log_cookies(session)
    else:
        logging.error(f"status  : {resp.status_code}")
        logging.error(f"text    : {resp.text}")
        logging.error(f"headers : {resp.headers}")
        raise RuntimeError("Request failed")
    return resp


def auth(args, login_path=LOGIN_PATH, cookie_file=None):
    login_url = args.url + login_path
    session = requests.Session()

    loaded_cookies = None
    if cookie_file:
        try:
            logging.info(f"Loading cookies from file {cookie_file}")
            with open(cookie_file, 'r', encoding ='utf8') as f:
                loaded_cookies = json.load(f)
            make_request_or_stop(session, get_dashboard_url(args), cookies=loaded_cookies)
            # session.cookies = loaded_cookies
        except (OSError, RuntimeError) as exc:
            logging.error(f"couldn't read file: {exc}")
        else:
            return session

    logging.info(f"Trying to authenticate to {login_url}")

    resp = make_request_or_stop(session, login_url, "GET")
    logging.info(f"Response: {resp.status_code}")

    soup = BeautifulSoup(resp.text, 'html.parser')
    csrf_input = soup.find("input", {"name": "csrf_token"}).get('value')
    logging.debug(f"CSRF token in hidden input: {csrf_input}")

    # CSRF token must be present in 2 places, it's required by protection mechanism
    form_data = {'email': args.login, 'password': args.password, 'csrf_token': csrf_input}
    form_data_safe = form_data.copy()
    if HIDE_PWD:
        form_data_safe['password'] = "*" * len(form_data_safe['password'])
    logging.debug(f"Prepared form data: {form_data_safe}")

    resp = make_request_or_stop(session, login_url, "POST", data=form_data)
    if cookie_file:
        with open(cookie_file, 'w', encoding ='utf8') as f:
            json.dump(session.cookies.get_dict(), f)

    return session

def delete_with_dashboard(args, session, topic_ids):
    logging.warning(f"Requesting deletion of following ids: {', '.join(topic_ids)}")
    form_data = DELETE_DASHBOARD_DATA.copy()
    form_data['suggestion_id'] = topic_ids
    resp = make_request_or_stop(session, get_dashboard_url(args), method="POST", data=form_data)

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument("-l", "--login", help='login (email)')
    parser.add_argument("-p", "--password", help='password')
    parser.add_argument("-b", "--board", default=7251, help='board id')
    parser.add_argument("--url", default="https://app.featureupvote.com/", help='base upvote url')
    parser.add_argument("--csv",
                        action='store_const',
                        # nargs="*",
                        const="feature_upvote_suggestions_export.csv",
                        help='path to csv file with upvote exports')
    parser.add_argument("--before",
                        dest='date_lte',
                        help='filter suggestion data with date before this, free-from format (date_lte)')
    parser.add_argument("--after",
                        dest='date_gte',
                        help='filter suggestion data with date after this, free-from format (date_gte)')
    parser.add_argument("--votes_lte", type=int, help='select suggestions with votes less than or equal to this value')
    parser.add_argument("--votes_gte", type=int, help='select suggestions with votes more than or equal to this value')
    parser.add_argument("--tags", help='select suggestions with this tags (NOT IMPLEMENTED)')
    parser.add_argument("--status",
                        dest='include_statuscodes',
                        help='select suggestions with this status')
    parser.add_argument("--show_csv", action="store_true", help="shpw loading of CSV data")

    args = parser.parse_args()
    logging.debug(f"{args=}")

    if args.csv:
        topics = read_csv(args)
        preprocess_topic_dicts(topics)

        filter_kwargs = {}
        for key in ("date_gte", "date_lte", "votes_gte", "votes_lte", "include_statuscodes"):
            value = getattr(args, key)
            if value:
                filter_kwargs[key] = value
        filtered_topics = filter_topics(topics, **filter_kwargs)

        filtered_ids = []
        for topic in filtered_topics:
            show_topic(topic)
            filtered_ids.append(topic["Suggestion ID"])
        print(f"Selected ids: {filtered_ids}")

    if args.password and args.login:
        session = auth(args)
    # delete_with_dashboard(args, session, ["220203", "220087"])


    logging.info("Finished")
