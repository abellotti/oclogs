#!/usr/bin/python3

import logging
import os
import json
import arrow
import requests
import crayons
import click
import time

from threading import Thread

logging.basicConfig()

colors = [getattr(crayons, c) for c in ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'black')]


def colorit(name):
    return colors[sum(map(ord, name)) % len(colors)](name)


class Resource(object):

    name = None
    namespace = None
    last_seen = None

    def __init__(self, d):
        self.data = d
        self.metadata = d["metadata"]


class Container(Resource):

    def __init__(self, name, d):
        """
        Containers get passed the entire pod JSON and pluck its own data out
        based on the name parameter.
        """
        super().__init__(d)
        self.name = name
        self.pluck_data()
        self.namespace = d["metadata"]["namespace"]

    def pluck_data(self):
        for c in self.data["spec"]["containers"]:
            if c["name"] == self.name:
                self.spec = c
                break

        for c in self.data["status"]["containerStatuses"]:
            if c["name"] == self.name:
                self.status = c
                break


class Pod(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
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


class Event(Resource):
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

    def __init__(self, d):
        super().__init__(d)
        self.count = d["count"]
        self.first_seen = arrow.get(d["firstTimestamp"])
        self.last_seen = arrow.get(d["lastTimestamp"])
        self.obj = d["involvedObject"]
        self.message = d["message"]
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


class Observer(object):

    def observe(self, resource, feed):
        pass


class ConsoleObserver(Observer):

    def __init__(self, since=arrow.now().shift(minutes=-1)):
        self.since = since

    def observe(self, resource, feed):
        if resource.last_seen:
            if not self.since or resource.last_seen > self.since:
                print(resource)
        else:
            print(resource)


class OpenshiftFeed(object):

    resource = None
    api_suffix = None

    def __init__(self, api, headers, namespace, observers):
        self.api = api
        self.headers = headers
        self.namespace = namespace
        self.observers = observers
        self.resources = {}

    def fetch_loop(self):
        while True:
            try:
                ns_url = f"namespaces/{self.namespace}/" if self.namespace else ""
                r = requests.get(f"{self.api}/watch/{ns_url}{self.api_suffix}",
                                 headers=self.headers,
                                 stream=True)

                if r.status_code != 200:
                    print(f"Invalid status from server: %s\n%s" % (
                        r.status_code,
                        r.json()['message']
                    ))
                    return

                for l in r.iter_lines():
                    d = json.loads(l)
                    resource = self.resource(d["object"])
                    self.resources[resource.name] = resource
                    for o in self.observers:
                        o.observe(resource, self)
            except Exception:
                logging.exception("Failed connection")
            print("Reconnecting...")
            time.sleep(1)


class PodFeed(OpenshiftFeed):

    def __init__(self, api, headers, namespace, observers):
        super().__init__(api, headers, namespace, observers)
        self.resource = Pod
        self.api_suffix = "pods"


class EventFeed(OpenshiftFeed):

    def __init__(self, api, headers, namespace, observers):
        super().__init__(api, headers, namespace, observers)
        self.resource = Event
        self.api_suffix = "events"


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

    observers = (ConsoleObserver(),)

    for cls in (PodFeed, EventFeed):
        feed = cls(API, headers, namespace, observers)
        Thread(target=feed.fetch_loop).start()
