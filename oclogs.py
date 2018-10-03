#!/usr/bin/env python3

import logging
import os
import json
import arrow
import requests
import crayons
import click
import time
import random

from threading import Thread

try:
    from slackclient import SlackClient
except Exception:
    pass

logging.basicConfig()

COLOR_KEYS = ('red', 'green', 'blue', 'yellow', 'cyan', 'magenta', 'white', 'black')
COLORS = [getattr(crayons, c) for c in COLOR_KEYS]
DATE_FORMAT = "YYYY-MM-DD HH:mm:ss"


def colorit(name):
    return COLORS[sum(map(ord, name)) % len(COLORS)](name)


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
        if self.status:
            self.state = list(self.status["state"].keys())[0]
            self.state_data = self.status["state"][self.state]
        else:
            self.state = self.state_data = None

    def pluck_data(self):
        for c in self.data["spec"]["containers"]:
            if c["name"] == self.name:
                self.spec = c
                break

        if "containerStatuses" in self.data["status"]:
            for c in self.data["status"]["containerStatuses"]:
                if c["name"] == self.name:
                    self.status = c
                    break
        else:
            self.status = None


class Pod(Resource):

    def __init__(self, d):
        super().__init__(d)
        md = self.metadata
        self.namespace = md["namespace"]
        self.name = md["name"]
        self.status = d["status"]["phase"]
        self.started = arrow.get(md["creationTimestamp"])
        self.containers = list(self.populate_containers())

    def populate_containers(self):
        for name in (c["name"] for c in self.data["spec"]["containers"]):
            yield Container(name, self.data)

    def __eq__(self, o):
        return o is not None and \
               o.namespace == self.namespace and \
               o.name == self.name and \
               o.status == self.status

    def __repr__(self):
        return "%s %s: [%s] %s" % (
            self.started.format(DATE_FORMAT),
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
            self.last_seen.format(DATE_FORMAT),
            colorit(self.namespace),
            self.reason,
            crayons.white(f"{self.kind}/{self.name}"),
            self.message
        )


class Observer(object):

    def __init__(self, since=arrow.now().shift(minutes=-1), slack=None):
        self.since = since
        self.slack = slack
        self.seen_messages = {}

    def has_been_seen(self, msg_key, now):
        return (msg_key in self.seen_messages and
                self.seen_messages[msg_key] > self.since)

    def clear_seen_messages(self):
        if random.randint(0, 100) == 1:
            hour_ago = arrow.now().shift(hours=-1)
            for k, v in list(self.seen_messages.items()):
                if v < hour_ago:
                    del self.seen_messages[k]

    def observe(self, resource, feed):
        pass


class Console(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        msg = repr(resource)
        now = arrow.now()

        if self.has_been_seen(msg, now):
            return

        self.seen_messages[msg] = now

        if resource.last_seen is None or resource.last_seen > self.since:
            print(msg)


class SystemOOM(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == Event and resource.reason == "SystemOOM":
            now = arrow.now()

            if self.has_been_seen(resource.node, now) or resource.last_seen < self.since:
                return

            self.seen_messages[resource.node] = now

            print(crayons.white("{:*^80}".format("SYSTEM OOM")))
            print(f"Node: {resource.node}")
            print(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            print(crayons.white("*" * 80))
            msg = "\n".join([
                ":rotating_light: *System OOM* :rotating_light:",
                f"Node: {resource.node}",
            ])
            self.slack.send_message(msg)


class FailedPodKill(Observer):

    def observe(self, resource, feed):
        self.clear_seen_messages()
        if type(resource) == Event and resource.reason == "FailedKillPod":
            msg_key = resource.namespace + resource.name
            now = arrow.now()

            if self.has_been_seen(msg_key, now) or resource.last_seen < self.since:
                return

            self.seen_messages[msg_key] = now

            print(crayons.white("{:*^80}".format("Failed to kill pod")))
            print(f"Pod: {resource.name}")
            print(f"Killed: {resource.last_seen.format(DATE_FORMAT)}")
            print(resource.message)
            print(crayons.white("*" * 80))

            msg = "\n".join([
                ":super_saiyan: *Failed to kill pod* :super_saiyan:",
                f"Namespace: {resource.namespace}",
                f"Pod: {resource.name}",
                "```%s```" % " ".join(resource.message.split("\n"))
            ])

            self.slack.send_message(msg)


class PodOOM(Observer):

    def observe(self, resource, feed):
        if type(resource) != Pod:
            return

        for c in resource.containers:
            if c.state == "terminated" and c.state_data.get("reason") == "OOMKilled":
                killed = arrow.get(c.state_data.get("finishedAt"))
                if killed > self.since:
                    self.console(resource, c, killed)
                    self.send_slack(resource, c, killed)

    def send_slack(self, p, c, killed):
        msg = "\n".join([
            ":dead-docker: *POD OOM* :dead-docker:",
            f"Namespace: {p.namespace}",
            f"Pod: {p.name}",
            f"Container: {c.name}"
        ])
        self.slack.send_message(msg)

    def console(self, p, c, killed):
        print(crayons.white("{:*^80}".format("OOM KILLED")))
        print(f"Pod: {p.name}")
        print(f"Container: {c.name}")
        print(f"Killed: {killed.format(DATE_FORMAT)}")
        print(crayons.white("*" * 80))


class OpenshiftFeed(object):

    resource = None
    api_suffix = None

    def __init__(self, api, headers, namespace, observers, ca_store):
        self.api = api
        self.headers = headers
        self.namespace = namespace
        self.observers = observers
        self.ca_store = ca_store
        self.resources = {}

    def fetch_loop(self):
        while True:
            try:
                ns_url = f"namespaces/{self.namespace}/" if self.namespace else ""

                kwargs = {"headers": self.headers, "stream": True}
                if self.ca_store:
                    kwargs["verify"] = self.ca_store

                r = requests.get(f"{self.api}/watch/{ns_url}{self.api_suffix}", **kwargs)

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

    resource = Pod
    api_suffix = "pods"


class EventFeed(OpenshiftFeed):

    resource = Event
    api_suffix = "events"


class Slack(object):

    def __init__(self):
        try:
            token = os.environ["SLACK_TOKEN"]
            self.channel = os.environ["SLACK_CHANNEL"]
            self.client = SlackClient(token)
        except Exception:
            self.client = self.channel = None

    def send_message(self, msg):
        if self.client:
            self.client.api_call("chat.postMessage", channel=self.channel, text=msg)


def disable_color():
    global COLORS
    global crayons
    from collections import namedtuple

    COLORS = [lambda *a, **k: a[0] for c in COLOR_KEYS]
    crayons_tuple = namedtuple("crayons", COLOR_KEYS)
    crayons = crayons_tuple(*COLORS)


@click.command()
@click.option("--token", default=os.path.expanduser("~/token"))
@click.option("--api")
@click.option("--api-token")
@click.option("-n", "--namespace")
@click.option("--color/--no-color", default=True)
@click.option("--ca-store")
def main(token, api, api_token, namespace, color, ca_store):

    if not api:
        print("Please specify valid api hostname using --api")
        return

    if not color:
        disable_color()

    API = f"https://{api}/api/v1"

    if api_token:
        token = api_token
    else:
        with open(token) as fp:
            token = fp.read().strip()

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    slack = Slack()

    observers = (Console(slack=slack), PodOOM(slack=slack), SystemOOM(slack=slack), FailedPodKill(slack=slack))

    for cls in (PodFeed, EventFeed):
        feed = cls(API, headers, namespace, observers, ca_store)
        Thread(target=feed.fetch_loop).start()


if __name__ == "__main__":
    main(auto_envvar_prefix="OCLOGS")
