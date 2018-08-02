#!/usr/bin/python3

import logging
import os
import json
import arrow
import requests
import crayons
import click

from threading import Thread

logging.basicConfig()

colors = [getattr(crayons, c) for c in ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'black')]


def colorit(name):
    return colors[sum(map(ord, name)) % len(colors)](name)


class Pod(object):

    def __init__(self, d):
        self.data = d
        md = d["metadata"]
        self.namespace = md["namespace"]
        self.name = md["name"]
        self.status = d["status"]["phase"]
        self.started = arrow.get(md["creationTimestamp"])

    def __eq__(self, o):
        return o is not None and \
               o.namespace == self.namespace and \
               o.name == self.name and \
               o.status == self.status

    def __repr__(self):
        return "%s %s: [%s] %s" % (
            self.started.format("YYYY-MM-DD HH:mm:ss"),
            colorit(self.namespace),
            self.status,
            crayons.white(self.name)
        )


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


pods = {}


def pod_status(api, headers, namespace):
    ns_url = f"namespaces/{namespace}/" if namespace else ""
    r = requests.get(f"{api}/watch/{ns_url}pods", headers=headers, stream=True)

    if r.status_code != 200:
        print(f"Invalid status from server: {r.status_code}\n{r.json()['message']}")
        return

    for l in r.iter_lines():
        d = json.loads(l)
        pod = Pod(d["object"])

        if pod != pods.get(pod.name):
            print(pod)

        pods[pod.name] = pod


@click.command()
@click.option("--token", default=os.path.expanduser("~/token"))
@click.option("--api")
@click.option("-n", "--namespace")
def cli(token, api, namespace):

    API = f"https://{api}/api/v1"

    with open(token) as fp:
        token = fp.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    Thread(target=pod_status, args=(API, headers, namespace)).start()

    ns_url = f"namespaces/{namespace}/" if namespace else ""
    r = requests.get(f"{API}/watch/{ns_url}events", headers=headers, stream=True)

    if r.status_code != 200:
        print(f"Invalid status from server: {r.status_code}\n{r.json()['message']}")
        return

    start = arrow.now().shift(minutes=-1)

    for e in (Event.from_event_feed(l) for l in r.iter_lines()):
        if e.last_seen > start:
            print(e)
