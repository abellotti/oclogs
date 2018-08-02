#!/usr/bin/python3

import logging
import os
import json
import arrow
import requests
import crayons
import click
import time

from concurrent import futures

logging.basicConfig()

colors = [getattr(crayons, c) for c in ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'black')]


def colorit(name):
    return colors[sum(map(ord, name)) % len(colors)](name)


class Event(object):
    """
    count: how many times has this event been seen
    first_seen: when was this event first seen
    kind: type of target resource (e.g. pod)
    namespace: namespace of target resource
    name: name of target resource
    last_seen: when was this event last seen
    message: specific info about the event
    metadata: typical metadata on any kubernetes resource
    reason: event type (e.g. SystemOOM, Created, etc)
    source: node hostname
    type: logging level, e.g. Warning, Normal, etc
    """

    @classmethod
    def from_event_feed(cls, line):
        return cls(json.loads(line)["object"])

    def __init__(self, d):
        self.data = d
        self.count = d["count"]
        self.first_seen = arrow.get(d["firstTimestamp"])
        self.last_seen = arrow.get(d["lastTimestamp"])
        self.obj = d["involvedObject"]
        self.message = d["message"]
        self.metadata = d["metadata"]
        self.reason = d["reason"]
        self.component = d["source"]["component"]
        self.node = d["source"]["host"] if self.component == "kubelet" else None
        self.namespace = self.obj.get("namespace", "???")
        self.name = self.obj.get("name")
        self.kind = self.obj.get("kind")

    def __repr__(self):
        return "%s %s: [%s] on %s - %s" % (
            self.last_seen.format("YYYY-MM-DD HH:mm:ss"),
            colorit(self.namespace),
            self.reason,
            crayons.white(f"{self.kind}/{self.name}"),
            self.message
        )


@click.command()
@click.option("--token", default=os.path.expanduser("~/token"))
@click.option("--api")
def cli(token, api):

    API = f"https://{api}/api/v1"

    with open(token) as fp:
        token = fp.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    session = requests.Session()
    session.headers.update(headers)
    pool = futures.ThreadPoolExecutor()

    def poll_until(e, state="running"):
        target_container = e.message.split(":")[1][2:]
        for _ in range(50):
            pod = session.get(f"{API}/namespaces/{e.namespace}/pods/{e.name}").json()
            try:
                if type(pod["status"]) == str:
                    state_name = pod["status"]
                    reason = None
                else:
                    status = [s for s in pod["status"]["containerStatuses"]
                              if s["name"] == target_container][0]
                    cstate = status["state"]
                    state_name = list(cstate.keys())[0]
                    reason = cstate[state_name].get("reason")
                if state_name != state:
                    msg = f'Container {crayons.white(target_container)} {crayons.red("killed")}'
                    msg += f'with status {crayons.white(state_name)}.'
                    if reason:
                        msg += f' Reason: {reason}'
                    print(msg)
                    return
            except Exception as exc:
                logging.exception("error")
            time.sleep(1)

    r = requests.get(f"{API}/watch/events", headers=headers, stream=True)
    start = arrow.now().shift(minutes=-1)

    for e in (Event.from_event_feed(l) for l in r.iter_lines()):
        if e.last_seen > start:
            print(e)
            if e.reason == "Killing":
                pool.submit(poll_until, e)
