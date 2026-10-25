"""Check Mosquitto auth + ACL against a running broker (see config/acl).

Run inside a container that has paho-mqtt, passing credentials as env vars:
  PW_SERVER, PW_NODERED, PW_DEVICE   passwords from .env
  MQTT_BROKER (default mqtt-broker), MQTT_PORT (default 1883), MQTT_USE_TLS (0/1),
  MQTT_CA_CERT (path, when TLS)
Exit code 0 = every check passed.
"""
import os
import ssl
import sys
import threading
import time

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

HOST = os.getenv("MQTT_BROKER", "mqtt-broker")
PORT = int(os.getenv("MQTT_PORT", "1883"))
USE_TLS = os.getenv("MQTT_USE_TLS", "0") == "1"
MAC = "AA:BB:CC:DD:EE:01"
PW = {u: os.environ["PW_" + u.upper()] for u in ("server", "nodered", "device")}


def make(user=None, password=None):
    c = mqtt.Client(CallbackAPIVersion.VERSION2)
    if user is not None:
        c.username_pw_set(user, password)
    if USE_TLS:
        c.tls_set(ca_certs=os.getenv("MQTT_CA_CERT"))
    return c


def connect_rc(user, password):
    """Return the CONNACK reason code as a string ('Success' = accepted)."""
    box = {}
    ev = threading.Event()
    c = make(user, password)

    def on_connect(client, userdata, flags, rc, props=None):
        box["rc"] = str(rc)
        ev.set()

    c.on_connect = on_connect
    c.connect(HOST, PORT, 10)
    c.loop_start()
    ev.wait(5)
    c.loop_stop()
    c.disconnect()
    return box.get("rc", "timeout")


def subscribe_granted(user, password, topic):
    box = {}
    ev = threading.Event()
    c = make(user, password)
    c.on_connect = lambda cl, ud, fl, rc, p=None: cl.subscribe(topic)

    def on_subscribe(client, userdata, mid, rcs, props=None):
        box["rc"] = [str(r) for r in rcs]
        ev.set()

    c.on_subscribe = on_subscribe
    c.connect(HOST, PORT, 10)
    c.loop_start()
    ev.wait(5)
    c.loop_stop()
    c.disconnect()
    return box.get("rc", ["timeout"])


def delivered(pub_user, topic):
    """Publish as pub_user; True if a `server` subscriber on the same topic receives it."""
    got = threading.Event()
    sub = make("server", PW["server"])
    ready = threading.Event()
    sub.on_connect = lambda cl, ud, fl, rc, p=None: cl.subscribe(topic, qos=1)
    sub.on_subscribe = lambda cl, ud, mid, rcs, p=None: ready.set()
    sub.on_message = lambda cl, ud, msg: got.set()
    sub.connect(HOST, PORT, 10)
    sub.loop_start()
    ready.wait(5)
    pub = make(pub_user, PW[pub_user])
    pub.connect(HOST, PORT, 10)
    pub.loop_start()
    time.sleep(0.5)
    pub.publish(topic, b'{"t":"acl-test"}', qos=1).wait_for_publish(3)
    ok = got.wait(2)
    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()
    return ok


def receives(sub_user, sub_filter, topic):
    """Subscribe as sub_user to sub_filter, publish on topic as `server`; True if sub_user gets it.

    Mosquitto grants wide filters (e.g. home/#) in SUBACK and applies the ACL per delivered
    message, so what matters is whether a message actually arrives, not the SUBACK code.
    """
    got = threading.Event()
    ready = threading.Event()
    sub = make(sub_user, PW[sub_user])
    sub.on_connect = lambda cl, ud, fl, rc, p=None: cl.subscribe(sub_filter, qos=1)
    sub.on_subscribe = lambda cl, ud, mid, rcs, p=None: ready.set()
    sub.on_message = lambda cl, ud, msg: got.set()
    sub.connect(HOST, PORT, 10)
    sub.loop_start()
    ready.wait(5)
    pub = make("server", PW["server"])
    pub.connect(HOST, PORT, 10)
    pub.loop_start()
    time.sleep(0.5)
    pub.publish(topic, b'{"t":"acl-test"}', qos=1).wait_for_publish(3)
    ok = got.wait(2)
    pub.loop_stop(); pub.disconnect()
    sub.loop_stop(); sub.disconnect()
    return ok


results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name + ((" -> " + detail) if detail else ""))


rc = connect_rc(None, None)
check("anonymous connect is rejected", rc != "Success", rc)
rc = connect_rc("server", "wrong-password")
check("wrong password is rejected", rc != "Success", rc)
for u in PW:
    rc = connect_rc(u, PW[u])
    check(f"{u} can log in", rc == "Success", rc)

cmd = f"home/device/{MAC}/cmd"
state = f"home/device/{MAC}/state"
check("server  -> publish cmd   allowed", delivered("server", cmd))
check("nodered -> publish cmd   allowed", delivered("nodered", cmd))
check("device  -> publish cmd   DENIED", not delivered("device", cmd))
check("device  -> publish state allowed", delivered("device", state))
check("nodered -> publish state DENIED", not delivered("nodered", state))

check("device  receives its own cmd", receives("device", cmd, cmd))
check("device  on '#' does NOT receive other devices' state", not receives("device", "#", state))
check("device  on '#' does NOT receive app/ notifications", not receives("device", "#", "app/user/1/notifications"))
check("device  on '#' does NOT receive home/security alerts", not receives("device", "#", "home/security/x/alert"))
check("nodered receives device state", receives("nodered", "home/device/+/state", state))
check("nodered on '#' does NOT receive app/ notifications", not receives("nodered", "#", "app/user/1/notifications"))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
